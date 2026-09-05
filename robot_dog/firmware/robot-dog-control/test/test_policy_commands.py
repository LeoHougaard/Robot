"""Run the production policy handlers with a fake bus; no physical outputs.

Usage: python test/test_policy_commands.py --json-include <ArduinoJson/src>
Requires a host C++ compiler and the same ArduinoJson headers as PlatformIO.
Only dependencies are stubbed: handler bodies are extracted from main.cpp.
"""
import argparse
from pathlib import Path
import subprocess
import tempfile


PRELUDE = r'''
#include <ArduinoJson.h>
#include <cassert>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <limits>
#include <string>
using String = std::string;
using std::isfinite;
constexpr unsigned MAX_SERVOS=12, MOTOR_COUNT=2, POLICY_FEEDBACK_INTERVAL_MS=20;
struct Servo { uint8_t id; float minAngle=90, maxAngle=270, lastAngle=180, velocityDps=0, motorSpeed=0; };
using ServoConfig = Servo;
Servo servos[MAX_SERVOS];
unsigned servoCount=12;
struct { float command=0; } motors[MOTOR_COUNT];
struct { bool active=false; } identifyJob;
bool programPlaying=false;
struct Policy {
 bool armed=false,monitoring=false,compactFeedback=false;
 uint32_t monitorEndsAt=0,lastSequence=0,feedbackTick=0,feedbackFailures=0,
  missedFeedbackPeriods=0,nextFeedbackAt=0,lastFrameAt=0;
} policyControl;
unsigned writes=0, errors=0, torqueReads=0;
bool torqueOff[MAX_SERVOS];
struct Bus {
 bool readTorqueDisabled(uint8_t id) { ++torqueReads; return torqueOff[id-1]; }
 void syncPosition(const uint8_t*, const uint16_t*, unsigned) { ++writes; }
} servoBus;
uint32_t millis() { return 100; }
bool policyHardwareReady(String&) { return true; }
void sendOk(const char*) {}
void sendError(const char*) { ++errors; }
void disarmPolicy(const char*, bool) { policyControl.armed=false; policyControl.monitoring=false; }
uint16_t angleToBusPosition(const Servo&,float angle) { return uint16_t(angle*4095/360); }
void reset() {
 policyControl=Policy{};writes=errors=torqueReads=0;programPlaying=false;identifyJob.active=false;
 for(unsigned i=0;i<12;++i) {servos[i]=Servo{};servos[i].id=i+1;torqueOff[i]=true;}
 for(auto &motor:motors)motor.command=0;
}
JsonDocument frame() {
 JsonDocument doc;doc["seq"]=1;
 for(unsigned i=1;i<=12;++i)doc["targets"][std::to_string(i)]=185.;
 return doc;
}
'''

TESTS = r'''
int main() {
 reset();JsonDocument monitor;
 torqueOff[11]=false;handlePolicyMonitor(monitor);
 assert(!policyControl.monitoring && !policyControl.armed && errors==1 && writes==0 && torqueReads==12);
 reset();programPlaying=true;handlePolicyMonitor(monitor);assert(errors==1 && torqueReads==0);
 reset();motors[1].command=1;handlePolicyMonitor(monitor);assert(errors==1 && torqueReads==0);
 reset();servos[0].velocityDps=1;handlePolicyMonitor(monitor);assert(errors==1 && !policyControl.monitoring);
 reset();monitor["duration_ms"]=60001;handlePolicyMonitor(monitor);assert(errors==1 && !policyControl.monitoring);
 reset();monitor["duration_ms"]=30000;handlePolicyMonitor(monitor);
 assert(policyControl.monitoring && !policyControl.armed && torqueReads==12 && writes==0);
 auto doc=frame();handlePolicyFrame(doc,true);
 assert(writes==0 && policyControl.lastSequence==1 && servos[0].lastAngle==180 && errors==0);
 doc["seq"]=2;handlePolicyFrame(doc); // ordinary frame cannot write in monitor mode
 assert(writes==0 && policyControl.lastSequence==1 && errors==1);
 reset();policyControl.armed=true;doc=frame();handlePolicyFrame(doc);
 assert(writes==1 && servos[11].lastAngle==185 && policyControl.lastSequence==1);
 handlePolicyFrame(doc);assert(writes==1 && errors==1); // replay rejected
 for(float bad: {89.f,271.f,std::numeric_limits<float>::quiet_NaN(),std::numeric_limits<float>::infinity()}) {
  reset();policyControl.armed=true;doc=frame();doc["targets"]["12"]=bad;handlePolicyFrame(doc);
  assert(writes==0 && policyControl.lastSequence==0 && errors==1 && !policyControl.armed);
 }
 reset();policyControl.armed=true;doc=frame();doc["targets"].remove("12");handlePolicyFrame(doc);assert(writes==0 && errors==1);
 reset();policyControl.armed=true;doc=frame();doc["targets"]["12"]="185";handlePolicyFrame(doc);assert(writes==0 && errors==1);
 reset();policyControl.armed=true;doc=frame();doc["targets"]["13"]=185;handlePolicyFrame(doc);assert(writes==0 && errors==1);
 for(double sequence: {-1.,1.5,4294967296.}) {
  reset();policyControl.armed=true;doc=frame();doc["seq"]=sequence;handlePolicyFrame(doc);assert(writes==0 && errors==1);
 }
 reset();doc=frame();handlePolicyFrame(doc,true);assert(writes==0 && errors==1);
}
'''


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json-include", required=True, type=Path)
    parser.add_argument("--compiler", default="g++")
    args = parser.parse_args()
    source = (Path(__file__).resolve().parents[1] / "src/main.cpp").read_text()
    handlers = source[source.index("void handlePolicyMonitor("):source.index("void runPolicyFeedback()")]
    with tempfile.TemporaryDirectory(prefix="robot-policy-test-") as directory:
        root = Path(directory)
        cpp, exe = root / "policy_commands.cpp", root / "policy_commands.exe"
        cpp.write_text(PRELUDE + handlers + TESTS)
        subprocess.run([args.compiler, "-std=c++17", "-O2", "-I", str(args.json_include), str(cpp), "-o", str(exe)], check=True)
        subprocess.run([str(exe)], check=True)
    print("Production policy handlers: valid writes, invalid input rejection and read-only monitor checks passed")
