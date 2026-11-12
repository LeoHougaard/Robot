"""Adversarial replay through production firmware dispatch, using fake I/O only.

This compiles the real parser, command dispatcher, policy admission/frame/
feedback/watchdog, torque-off and boot-disarm functions extracted from main.cpp.
Device boundaries and unrelated manual-command handlers are fake. No serial
port, socket, board, physical bus or actuator is opened. This is not hardware
qualification. Each --case can be run against --source to reproduce a regression.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

PRELUDE = r'''
#include <ArduinoJson.h>
#include <algorithm>
#include <atomic>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <deque>
#include <functional>
#include <stdexcept>
#include <string>
#include <vector>
#include "PeriodicDeadline.h"
#include "JsonCommandBoundary.h"
#include "SerialInputAbort.h"
struct String : std::string {
 using std::string::string;
 using std::string::operator=;
 String(const std::string &s):std::string(s) {}
 explicit String(size_t n):std::string(std::to_string(n)) {}
 String substring(size_t start,size_t end) const { return substr(start,end-start); }
};
using std::isfinite;
using std::min;
template<class T,class L,class H> T constrain(T v,L l,H h) {return std::max(T(l),std::min(v,T(h)));}
#define ROBOT_DOG_VERSION "HOST_REPLAY_NO_DEVICE"
#define REQUIRE(expr) do { if(!(expr)) throw std::runtime_error(std::string(#expr)+" at line "+std::to_string(__LINE__)); } while(0)
'''

FAKES = r'''
ServoConfig servos[MAX_SERVOS];
unsigned servoCount=MAX_SERVOS;
PolicyControlState policyControl;
struct {bool active=false;} identifyJob;
struct {const char *name="fake";float command=0,limit=.35;} motors[2];
constexpr unsigned MOTOR_COUNT=2;
struct {bool available=true,monitorEnabled=false;} imuState;
bool programPlaying=false,feedbackReady=true,imuReady=true;
uint32_t nowMs=100;
uint32_t millis() {return nowMs;}
uint32_t micros() {return nowMs*1000;}
std::atomic<uint32_t> serialErrorCounts[4]{};
size_t serialRxBufferBytes=8192;
bool serialFifoThresholdConfigured=true;
unsigned positionWrites=0,moveWrites=0,torqueReads=0,otherCommands=0;
bool torqueOff[MAX_SERVOS];
std::vector<std::string> errors,oks,events;
std::vector<JsonDocument> responses;
void sendError(const char* v) {errors.push_back(v);}
void sendOk(const char* v) {oks.push_back(v);}
void sendJson(JsonDocument &doc) {responses.emplace_back(doc);if(doc["type"]=="error")errors.push_back(doc["message"].as<std::string>());}
struct Bus {
 bool readTorqueDisabled(uint8_t id) {++torqueReads;return torqueOff[id-1];}
 void syncPosition(const uint8_t*,const uint16_t*,unsigned) {++positionWrites;events.push_back("positions");}
 void setTorque(uint8_t id,bool enabled) {torqueOff[id-1]=!enabled;events.push_back(enabled?"torque_on":"torque_off");}
 void setTorqueLimit(uint8_t,uint16_t) {}
 void moveTo(uint8_t,uint16_t,uint16_t,uint8_t) {++moveWrites;events.push_back("move");}
 void writeMotorSpeed(uint8_t,uint16_t,uint8_t) {}
 bool readPosition(uint8_t,uint16_t &position,uint8_t &error) {position=2048;error=0;return feedbackReady;}
 void discardPendingResponses() {}
} servoBus;
struct {bool isConnected() {return false;} struct IP {String toString(){return "";}};IP localIP(){return {};}} WiFi;
ServoConfig* findServo(uint8_t id) {for(auto &s:servos)if(s.id==id)return &s;return nullptr;}
void setServoMode(ServoConfig &s,bool motor) {s.motorMode=motor;}
void ensureServoMode(ServoConfig&) {}
void sampleServoBatteryNow() {}
uint16_t angleToBusPosition(const ServoConfig&,float a){return uint16_t(a*4095/360);}
float busPositionToMeasuredAngle(const ServoConfig&,uint16_t p){return p*360.f/4095;}
float clampAngle(const ServoConfig &s,float a){return constrain(a,s.minAngle,s.maxAngle);}
void writeServoTarget(ServoConfig&,float,uint16_t,uint8_t){++moveWrites;}
bool syncReadConfiguredServoState(ServoNativeFeedback*,bool* received) {
 for(unsigned i=0;i<servoCount;++i){received[i]=feedbackReady;servos[i].measuredAngle=181;servos[i].hasMeasuredAngle=feedbackReady;}
 return feedbackReady;
}
bool syncReadConfiguredServoCurrent(int16_t*,bool* received){for(unsigned i=0;i<servoCount;++i)received[i]=true;return true;}
bool sampleImu(){return imuReady;}
void sendPolicyState(uint32_t,uint32_t,const ServoNativeFeedback*,const bool*,const int16_t*,const bool*,uint32_t=0,uint32_t=0,uint32_t=0) {}
void addServoBatteryToJson(JsonDocument&) {}
void addConfigToJson(JsonDocument&) {}
void sendConfig() {}
void sendState(bool=false) {}
void homeAll() {++otherCommands;}
bool moveServo(uint8_t,float,uint16_t,uint8_t){++otherCommands;return true;}
void readAllServoAngles(bool) {}
bool readServoAngle(uint8_t,bool){return feedbackReady;}
void sendWifiStatus() {}
void sendImuState() {}
void sendMotorState() {}
void stopAllMotors(){for(auto &m:motors)m.command=0;}
void handleProgramClear(){++otherCommands;}
void stopAllServoVelocities();
void stopAllServoMotors();
String inputLine;
bool discardInputLine=false;
SerialInputAbort serialInputAbort;
struct SerialInput {
 std::deque<char> data;
 bool available(){return !data.empty();}
 int read(){char v=data.front();data.pop_front();return static_cast<unsigned char>(v);}
 void append(const std::string &s){data.insert(data.end(),s.begin(),s.end());}
} Serial;
'''

TESTS = r'''
void reset() {
 policyControl=PolicyControlState{};positionWrites=moveWrites=torqueReads=otherCommands=0;
 programPlaying=false;feedbackReady=imuReady=true;imuState.available=true;identifyJob.active=false;nowMs=100;
 inputLine="";discardInputLine=false;Serial.data.clear();errors.clear();oks.clear();events.clear();responses.clear();
 serialInputAbort=SerialInputAbort{};
 for(unsigned i=0;i<MAX_SERVOS;++i){servos[i]=ServoConfig{};servos[i].id=i+1;servos[i].minAngle=90;servos[i].homeAngle=180;servos[i].maxAngle=270;servos[i].lastAngle=180;torqueOff[i]=true;}
 for(auto &m:motors)m.command=0;
}
std::string frame(uint32_t seq=1,float angle=185) {
 JsonDocument d;d["cmd"]="policy_frame";d["seq"]=seq;
 for(unsigned i=1;i<=MAX_SERVOS;++i)d["targets"][std::to_string(i)]=angle;
 std::string line;serializeJson(d,line);return line;
}
void wire(const std::string &s){Serial.append(s);pollSerial();}
void arm(){wire("{\"cmd\":\"policy_arm\",\"confirm\":\"CALIBRATED_AND_LIFTED\"}\n");REQUIRE(policyControl.armed);}
bool allOff(){return std::all_of(torqueOff,torqueOff+MAX_SERVOS,[](bool v){return v;});}
const std::string safety="{\"cmd\":\"stop\"}\n{\"cmd\":\"policy_disarm\"}\n{\"cmd\":\"servo_torque\",\"all\":true,\"enabled\":false}\n";
const std::string nonce="0123456789abcdef0123456789abcdef";
const std::string abortRequest=std::string(1,'\x18')+nonce;
void test_boot_and_arm_gates(){
 reset();std::fill(torqueOff,torqueOff+MAX_SERVOS,false);disarmServosAtBoot();REQUIRE(allOff());REQUIRE(moveWrites==0&&positionWrites==0&&!policyControl.armed);
 wire("{\"cmd\":\"policy_arm\"}\n");REQUIRE(!policyControl.armed&&allOff());
 feedbackReady=false;wire("{\"cmd\":\"policy_arm\",\"confirm\":\"CALIBRATED_AND_LIFTED\"}\n");REQUIRE(!policyControl.armed&&allOff()&&moveWrites==0);
 feedbackReady=true;imuReady=false;wire("{\"cmd\":\"policy_arm\",\"confirm\":\"CALIBRATED_AND_LIFTED\"}\n");REQUIRE(!policyControl.armed&&allOff()&&moveWrites==0);
 imuReady=true;servos[11].hasStatusError=true;servos[11].statusError=1;
 wire("{\"cmd\":\"policy_arm\",\"confirm\":\"CALIBRATED_AND_LIFTED\"}\n");REQUIRE(!policyControl.armed&&allOff()&&moveWrites==0);
 servos[11].statusError=0;arm();REQUIRE(!allOff()&&moveWrites==12&&positionWrites==0);
 for(auto &s:servos)REQUIRE(s.lastAngle==181);
}
void test_fragmentation_and_coalescing(){
 const auto f=frame();
 for(size_t split=0;split<=f.size();++split){reset();arm();wire(f.substr(0,split));REQUIRE(positionWrites==0);wire(f.substr(split)+"\r\n");REQUIRE(positionWrites==1&&policyControl.lastSequence==1);}
 reset();arm();for(char c:f)wire(std::string(1,c));wire("\n\n"+frame(2)+"\n"+frame(3)+"\n");REQUIRE(positionWrites==3&&policyControl.lastSequence==3);
}
void test_stale_reordered_and_wrap_sequences(){
 reset();arm();wire(frame(4)+"\n"+frame(2)+"\n"+frame(4)+"\n"+frame(0)+"\n");REQUIRE(positionWrites==1&&policyControl.lastSequence==4);
 nowMs=200;wire(frame(3)+"\n");REQUIRE(policyControl.lastFrameAt==100);
 nowMs=221;runPolicyWatchdog();REQUIRE(!policyControl.armed);wire(frame(5)+"\n");REQUIRE(positionWrites==1);
 reset();arm();wire(frame(UINT32_MAX)+"\n"+frame(1)+"\n");REQUIRE(positionWrites==1&&policyControl.lastSequence==UINT32_MAX);
 wire(safety);arm();wire(frame(1)+"\n");REQUIRE(positionWrites==2&&policyControl.lastSequence==1);
}
void test_watchdog_before_late_frame(){
 reset();arm();nowMs=220;wire(frame()+"\n");REQUIRE(positionWrites==1&&policyControl.armed);
 reset();arm();nowMs=221;wire(frame()+"\n");runPolicyWatchdog();REQUIRE(positionWrites==0&&!policyControl.armed&&!allOff());
 REQUIRE(policyControl.lastFrameAt==0&&responses.back()["reason"]=="frame timeout; holding last targets");
 wire(frame(2)+"\n");REQUIRE(positionWrites==0&&!policyControl.armed);wire(safety);REQUIRE(allOff());
 reset();nowMs=UINT32_MAX-50;arm();nowMs=70;wire(frame()+"\n");REQUIRE(positionWrites==0&&!policyControl.armed);
}
void test_loss_and_watchdog(){
 const auto f=frame();
 for(size_t cut=1;cut<f.size();++cut){reset();arm();wire(f.substr(0,cut)+"\n");REQUIRE(positionWrites==0);nowMs=221;runPolicyWatchdog();REQUIRE(!policyControl.armed);}
 // Replay every possible 128-byte loss window, as in observed UART loss.
 for(size_t cut=0;cut+128<=f.size();++cut){reset();arm();wire(f.substr(0,cut)+f.substr(cut+128)+"\n");REQUIRE(positionWrites==0);nowMs=221;runPolicyWatchdog();REQUIRE(!policyControl.armed);}
 reset();arm();wire(f.substr(0,40)+"\n"+frame(2)+"\n");REQUIRE(positionWrites==1&&policyControl.lastSequence==2);
}
void test_lost_delimiter_and_trailing_bytes(){
 for(const auto &tail:std::vector<std::string>{"garbage",frame(2),std::string("\0",1)+frame(2)}){
  reset();arm();wire(frame()+tail+"\n");REQUIRE(positionWrites==0&&policyControl.lastSequence==0);
 }
 reset();arm();wire(frame()+" \t\r\n");REQUIRE(positionWrites==1);
}
void test_json_strings_and_malformed_syntax(){
 reset();arm();JsonDocument d;deserializeJson(d,frame());
 d["note"]="nested-looking {}[] quotes \" backslash \\ escaped controls \n\t\r and Unicode \xc3\xa9";
 d["metadata"]["nested"].add("}");d["metadata"]["nested"].add("[");
 std::string line;serializeJson(d,line);wire(" \t"+line+" \t\r\n");REQUIRE(positionWrites==1);
 for(const auto &bad:std::vector<std::string>{"", "[]", "null", "{]", "{\"cmd\":\"stop\"", "{\"cmd\":\"stop\",}"}){
  reset();arm();wire(bad+"\n");REQUIRE(policyControl.armed&&positionWrites==0&&oks.empty());
 }
}
void test_oversized_line_and_resync(){
 reset();wire(std::string(SERIAL_JSON_CAPACITY+5,'x'));wire("{\"cmd\":\"policy_arm\",\"confirm\":\"CALIBRATED_AND_LIFTED\"}\n");REQUIRE(!policyControl.armed&&allOff());
 arm();wire(frame()+std::string(SERIAL_JSON_CAPACITY,' ')+"\n");REQUIRE(positionWrites==0);wire(frame(2)+"\n");REQUIRE(positionWrites==1&&policyControl.lastSequence==2);
}
void test_safety_batch_and_queued_frames(){
 reset();arm();wire(frame()+"\n"+safety+frame(2)+"\n");REQUIRE(positionWrites==1&&!policyControl.armed&&allOff());
 REQUIRE(std::find(oks.begin(),oks.end(),"servo_torque")!=oks.end());
 reset();arm();wire("{\"cmd\":\"stop\"}\n");REQUIRE(!policyControl.armed&&!allOff()); // stop holds; never fabricate torque-off evidence
 wire(safety+safety);REQUIRE(allOff()&&!policyControl.armed);
 reset();arm();wire(frame().substr(0,40));wire("\n"+safety);REQUIRE(positionWrites==0&&!policyControl.armed&&allOff());
}
void test_reconnect_requires_explicit_arm(){
 reset();arm();wire(frame(10)+"\n"+frame(11).substr(0,40));nowMs=221;runPolicyWatchdog();REQUIRE(!policyControl.armed);
 // Reconnected host aborts old input, then hello and safety. No command may re-arm.
 wire(abortRequest+"\n{\"cmd\":\"hello\"}\n"+safety+frame(12)+"\n");REQUIRE(!policyControl.armed&&allOff()&&positionWrites==1);
 arm();wire(frame(1)+"\n");REQUIRE(policyControl.armed&&positionWrites==2&&policyControl.lastSequence==1);
}
void test_abort_discards_complete_old_commands(){
 const std::vector<std::string> commands={"{\"cmd\":\"policy_arm\",\"confirm\":\"CALIBRATED_AND_LIFTED\"}",
  "{\"cmd\":\"servo_torque\",\"all\":true,\"enabled\":true}","{\"cmd\":\"move\",\"id\":1,\"angle\":200}"};
 for(const auto &old:commands){
  reset();wire(old);REQUIRE(moveWrites==0&&otherCommands==0&&allOff());
  wire("\x18");REQUIRE(inputLine.empty()&&discardInputLine&&responses.empty());
  wire(nonce);REQUIRE(responses.back()["type"]=="serial_abort"&&responses.back()["version"]==2&&responses.back()["nonce"]==nonce);
  REQUIRE(!policyControl.armed&&allOff()&&moveWrites==0&&otherCommands==0);
  wire("old tail must be ignored\n{\"cmd\":\"hello\"}\n");
  REQUIRE(!policyControl.armed&&allOff()&&moveWrites==0&&otherCommands==0&&!discardInputLine);
  REQUIRE(responses.back()["type"]=="hello"&&responses.back()["supportsSerialInputAbort"]==true);
 }
 reset();arm();wire(frame());wire(abortRequest+"\n"+safety);REQUIRE(positionWrites==0&&!policyControl.armed&&allOff());
 reset();wire(std::string(SERIAL_JSON_CAPACITY+5,'x'));wire("\x18"+abortRequest+"\n{\"cmd\":\"hello\"}\n");
 REQUIRE(!discardInputLine&&responses.back()["type"]=="hello"&&allOff());
}
void test_abort_nonce_is_bounded(){
 const std::string old="{\"cmd\":\"policy_arm\",\"confirm\":\"CALIBRATED_AND_LIFTED\"}";
 for(size_t cut=0;cut<nonce.size();++cut){
  reset();wire(old+std::string(1,'\x18')+nonce.substr(0,cut));
  REQUIRE(responses.empty()&&!policyControl.armed&&allOff()&&discardInputLine);
  wire(abortRequest);REQUIRE(responses.size()==1&&responses.back()["nonce"]==nonce);
 }
 for(size_t split=0;split<=nonce.size();++split){
  reset();wire(std::string(1,'\x18')+nonce.substr(0,split));REQUIRE(responses.size()==(split==nonce.size()?1:0));
  wire(nonce.substr(split));REQUIRE(responses.size()==1&&responses.back()["version"]==2);
 }
 for(char bad:std::vector<char>{'G','A','-',0,'\n',0x7f}){
  reset();wire(old+std::string(1,'\x18')+nonce.substr(0,15)+std::string(1,bad)+nonce.substr(16));
  REQUIRE(responses.empty()&&!policyControl.armed&&allOff()&&!serialInputAbort.collecting&&discardInputLine);
  wire(abortRequest);REQUIRE(responses.size()==1&&responses.back()["nonce"]==nonce);
 }
 reset();wire(abortRequest+nonce);REQUIRE(responses.size()==1&&serialInputAbort.length==32&&discardInputLine);
 wire(std::string(1,'\x18')+"fedcba9876543210fedcba9876543210");
 REQUIRE(responses.size()==2&&responses.back()["nonce"]=="fedcba9876543210fedcba9876543210");
}
void test_delimiter_free_probe_never_executes(){
 // Also passes against legacy firmware: it cannot ACK, but never receives
 // the delimiter that would execute a complete old command. A dropped CAN
 // likewise cannot turn the nonce-only request into a command execution.
 const std::vector<std::string> commands={"{\"cmd\":\"policy_arm\",\"confirm\":\"CALIBRATED_AND_LIFTED\"}",
  "{\"cmd\":\"servo_torque\",\"all\":true,\"enabled\":true}"};
 for(const auto &old:commands)for(const auto &probe:std::vector<std::string>{abortRequest,nonce}){
  reset();wire(old+probe);REQUIRE(!policyControl.armed&&allOff()&&moveWrites==0&&positionWrites==0);
 }
}
void test_monitor_and_feedback_loss(){
 reset();wire("{\"cmd\":\"policy_monitor\",\"duration_ms\":1000}\n");REQUIRE(policyControl.monitoring&&allOff());
 auto f=frame();f.replace(f.find("policy_frame"),12,"policy_monitor_frame");wire(f+"\n"+frame(2)+"\n");REQUIRE(positionWrites==0&&moveWrites==0&&allOff()&&policyControl.lastSequence==1);
 wire(safety);REQUIRE(!policyControl.monitoring&&allOff());
 reset();arm();feedbackReady=false;for(unsigned i=1;i<=3;++i){nowMs=100+20*i;runPolicyFeedback();}REQUIRE(!policyControl.armed&&positionWrites==0);
}
int main(int argc,char** argv){
 std::vector<std::pair<std::string,std::function<void()>>> tests={
 {"boot_and_arm_gates",test_boot_and_arm_gates},{"fragmentation_and_coalescing",test_fragmentation_and_coalescing},
 {"stale_reordered_and_wrap_sequences",test_stale_reordered_and_wrap_sequences},{"watchdog_before_late_frame",test_watchdog_before_late_frame},
 {"loss_and_watchdog",test_loss_and_watchdog},{"lost_delimiter_and_trailing_bytes",test_lost_delimiter_and_trailing_bytes},
 {"json_strings_and_malformed_syntax",test_json_strings_and_malformed_syntax},
 {"oversized_line_and_resync",test_oversized_line_and_resync},{"safety_batch_and_queued_frames",test_safety_batch_and_queued_frames},
 {"reconnect_requires_explicit_arm",test_reconnect_requires_explicit_arm},{"abort_discards_complete_old_commands",test_abort_discards_complete_old_commands},
 {"abort_nonce_is_bounded",test_abort_nonce_is_bounded},{"delimiter_free_probe_never_executes",test_delimiter_free_probe_never_executes},
 {"monitor_and_feedback_loss",test_monitor_and_feedback_loss}};
 unsigned passed=0,failed=0;
 for(const auto &test:tests){if(argc>1&&test.first!=argv[1])continue;try{test.second();++passed;printf("PASS %s\n",test.first.c_str());}catch(const std::exception &e){++failed;fprintf(stderr,"FAIL %s: %s\n",test.first.c_str(),e.what());}}
 printf("Firmware replay: %u passed, %u failed; fake I/O only\n",passed,failed);return failed||!passed?1:0;
}
'''


def between(source: str, start: str, end: str) -> str:
    begin = source.index(start)
    return source[begin:source.index(end, begin)]


def harness(source: str) -> str:
    constants = "\n".join(re.findall(
        r"^static constexpr [^\n]* (?:MAX_SERVOS|SERIAL_JSON_CAPACITY|DEFAULT_MONITOR_INTERVAL_MS|COMMISSIONING_TORQUE_LIMIT|POLICY_[A-Z_]+) = [^\n]+", source, re.M))
    structures = (between(source, "struct ServoConfig {", "struct ProgramStep {") +
                  between(source, "struct ServoNativeFeedback {", "struct ServoNativeTelemetry {") +
                  between(source, "struct PolicyControlState {", "struct DefaultServoDef {"))
    unrelated = "\n".join(f"void {name}(JsonDocument&) {{++otherCommands;}}" for name in (
        "handleConfigSet startProgram handleProgramStep handleProgramStart handleWifiSet "
        "handleMonitorSet handleImuMonitorSet handleMotorSet handleServoPing handleServoBusProbe "
        "handleServoTelemetry handleServoScan handleServoSetId handleServoVelocity handleServoVelocityStop "
        "handleServoMode handleServoMotorSet handleServoMotorStop handleServoTorqueLimit handleCenterPose "
        "handleServoJog handleServoIdentify").split())
    production = (
        between(source, "bool policyHardwareReady(", "void stopAllServoVelocities() {") +
        between(source, "void stopAllServoVelocities() {", "bool setServoTorqueLimit(") +
        between(source, "void disarmServosAtBoot() {", "void sampleServoBatteryNow() {") +
        between(source, "void handleServoTorque(JsonDocument &doc)", "void handleServoTorqueLimit(") +
        between(source, "void handleCommand(const String &line)", "void addCorsHeaders() {") +
        between(source, "void pollSerial() {", "void runNetworkServices() {"))
    return PRELUDE + constants + structures + FAKES + unrelated + production + TESTS


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json-include", required=True, type=Path)
    parser.add_argument("--source", type=Path, default=root / "src/main.cpp")
    parser.add_argument("--compiler", default="g++")
    parser.add_argument("--case")
    parser.add_argument("--sanitize", action="store_true")
    parser.add_argument("--result-json", type=Path)
    args = parser.parse_args()
    source = args.source.read_text()
    with tempfile.TemporaryDirectory(prefix="robot-transport-replay-") as directory:
        cpp, exe = Path(directory) / "replay.cpp", Path(directory) / "replay"
        cpp.write_text(harness(source))
        flags = ["-g", "-fsanitize=address,undefined", "-fno-omit-frame-pointer"] if args.sanitize else ["-O2"]
        subprocess.run([args.compiler, "-std=c++17", "-Wno-deprecated-declarations", *flags,
                        "-I", str(args.json_include), "-I", str(root / "include"), str(cpp), "-o", str(exe)], check=True)
        print(f"Production source SHA-256: {hashlib.sha256(source.encode()).hexdigest()}", flush=True)
        result = subprocess.run([str(exe), *([args.case] if args.case else [])], capture_output=True, text=True)
        print(result.stdout, end="", flush=True)
        print(result.stderr, end="", flush=True)
        if args.result_json:
            files = [args.source, root / "include/JsonCommandBoundary.h", root / "include/SerialInputAbort.h", root / "include/PeriodicDeadline.h", Path(__file__)]
            results = [{"name": line.split()[1], "status": "passed"} for line in result.stdout.splitlines() if line.startswith("PASS ")]
            results.extend({"name": line.split(":", 1)[0].split()[1], "status": "failed", "detail": line.split(":", 1)[1].strip()}
                           for line in result.stderr.splitlines() if line.startswith("FAIL "))
            evidence = {"schema_version": 1, "generated_utc": datetime.now(timezone.utc).isoformat(),
                        "status": "passed" if result.returncode == 0 else "failed", "exit_code": result.returncode,
                        "scenario_count": len(results), "passed": sum(row["status"] == "passed" for row in results),
                        "failed": sum(row["status"] == "failed" for row in results), "skipped": 0, "scenarios": results,
                        "physical_execution": False, "hardware_qualification": False, "sanitized": args.sanitize,
                        "sanitizers": ["address", "undefined"] if args.sanitize else [],
                        "leak_check_disabled": args.sanitize and "detect_leaks=0" in os.environ.get("ASAN_OPTIONS", ""),
                        "compiler": args.compiler, "arduinojson_include": str(args.json_include),
                        "source_sha256": {str(path.relative_to(root)) if path.is_relative_to(root) else str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in files},
                        "scope": "Production command parser/dispatcher, arm admission, policy frames, feedback watchdog, stop, torque-off and boot-disarm against fake I/O; no UART timing/electrical/actuator validation"}
            args.result_json.parent.mkdir(parents=True, exist_ok=True)
            args.result_json.write_text(json.dumps(evidence, indent=2) + "\n")
        if result.returncode:
            raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
