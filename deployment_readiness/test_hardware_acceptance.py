"""Authored synthetic captures only: these tests never open a device or backend."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from deployment_readiness import hardware_acceptance as probe


def fixture(frames=1251, *, raw=True, synthetic=False):
    records = []
    def add(kind, ns, data):
        records.append({"type": kind, "session_id": "authored-fixture", "record_index": len(records),
                        "host_monotonic_ns": ns, "data": data})
    def packet(direction, ns, message):
        if raw:
            add(f"usb_{direction}_chunk", ns, {"hex": (json.dumps(message,separators=(",", ":"))+"\n").encode().hex(),
                                             "received_monotonic_ns": ns})
        else:
            add(f"robot_{direction}", ns, {"message": message})
    hello = {"type": "hello", "version": probe.EXPECTED_FIRMWARE,
             "supportsTorqueOffPolicyMonitor": True, "supportsSerialInputAbort": True, "serialInputAbortVersion": 2, "policyArmed": False,
             "policyMonitoring": False, "policyFeedbackIntervalMs": 20,
             "serialFifoThresholdConfigured": True, "serialErrorCounts": [0,0,0,0],
             "imu": {"available": True}}
    add("session_start", 0, {"schema_version": 1, "context": {"synthetic": synthetic}})
    if raw:
        add("usb_tx_chunk", 0, {"hex": (b"\x18"+b"a"*32).hex(), "received_monotonic_ns":0})
        packet("rx",0,{"type":"serial_abort","version":2,"nonce":"a"*32})
        add("usb_tx_chunk", 0, {"hex":"0a", "received_monotonic_ns":0})
    packet("tx",0,{"cmd":"hello"})
    packet("rx",0,hello)
    packet("tx",0,{"cmd":"policy_monitor","duration_ms":30000})
    packet("rx",0,{"type":"ok","cmd":"policy_monitor"})
    for tick in range(1,frames+1):
        ns = tick*20_000_000
        packet("rx",ns,{"type":"policy_monitor_state","armed":False,"seq":tick-1,"tick":tick,
                         "sample_ms":tick*20,"feedback_us":3000,"current_us":1000,"frame_us":4500,
                         "missed_feedback_periods":0,"ids":list(range(1,13)),"angles_deg":[180.]*12,
                         "current_raw":[1]*12,"status_errors":[0]*12,"feedback_complete":True,
                         "current_complete":True,"accel_mg":[0,0,1000],"gyro_dps":[0,0,0]})
        if tick < frames:
            packet("tx",ns+1_000_000,{"cmd":"policy_monitor_frame","seq":tick,
                                    "targets":{str(i):180. for i in range(1,13)}})
    packet("tx",frames*20_000_000+2_000_000,{"cmd":"hello"})
    packet("rx",frames*20_000_000+3_000_000,hello)
    packet("tx",frames*20_000_000+4_000_000,{"cmd":"policy_disarm"})
    packet("rx",frames*20_000_000+5_000_000,{"type":"policy_monitor_stopped"})
    add("session_end", frames*20_000_000+6_000_000,{"outcome":"passed","complete":True,"dropped_records":0,"transport_teardown":{"quiesced":True,"host_write_outcome":"HOST_WRITE_COMPLETED"}})
    return records


def messages(records, direction="rx"):
    for record in records:
        if record["type"] == f"robot_{direction}":
            yield record, record["data"]["message"]
        elif record["type"] == f"usb_{direction}_chunk":
            try: message=json.loads(bytes.fromhex(record["data"]["hex"]))
            except (ValueError,UnicodeError): continue
            yield record, message


def edit_message(record, message):
    if record["type"].startswith("usb_"):
        record["data"]["hex"] = (json.dumps(message)+"\n").encode().hex()
    else: record["data"]["message"] = message


def mutate_state(records, change, index=100):
    selected = [(r,m) for r,m in messages(records) if m.get("type")=="policy_monitor_state"]
    record,message = selected[index]
    change(message)
    edit_message(record,message)


def run(records, **kwargs):
    return probe.analyze_records(records, target="dog12", evidence_kind="operator-capture", **kwargs)


def gate(report,name):
    return next(c for c in report["checks"] if c["name"]==name)


class WireDecoderTests(unittest.TestCase):
    def test_fragment_every_byte_including_utf8(self):
        decoder = probe.WireDecoder(); result=[]
        for b in '{"type":"héllo"}\n'.encode(): result += decoder.feed(bytes([b]))
        decoder.finish()
        self.assertEqual(result,[{"type":"héllo"}]); self.assertEqual(decoder.counts["valid_lines"],1)
        self.assertFalse(decoder.counts["truncated_lines"])

    def test_coalesced_crlf_and_packet_padding(self):
        decoder=probe.WireDecoder()
        self.assertEqual(decoder.feed(b' {"a":1}\r\n   \n{"b":2}\n  '),[{"a":1},{"b":2}])
        decoder.finish(); self.assertEqual(decoder.counts["padding_lines"],1)
        self.assertFalse(decoder.counts["truncated_lines"])

    def test_truncated_complete_json_without_newline(self):
        decoder=probe.WireDecoder(); self.assertEqual(decoder.feed(b'{"a":1}'),[])
        decoder.finish(); self.assertEqual(decoder.counts["truncated_lines"],1)

    def test_error_then_recover_next_line(self):
        decoder=probe.WireDecoder()
        self.assertEqual(decoder.feed(b'{bad}\n{"ok":true}\n'),[{"ok":True}])
        self.assertEqual(decoder.counts["invalid_lines"],1)

    def test_invalid_utf8_rejected(self):
        decoder=probe.WireDecoder(); decoder.feed(b'{"s":"\xff"}\n')
        self.assertEqual(decoder.counts["invalid_lines"],1)

    def test_duplicate_keys_rejected(self):
        decoder=probe.WireDecoder(); decoder.feed(b'{"seq":1,"seq":2}\n')
        self.assertEqual(decoder.counts["invalid_lines"],1)

    def test_nonfinite_and_nonobject_rejected(self):
        for data in (b'{"a":NaN}\n',b'{"a":Infinity}\n',b'{"a":1e999}\n',b'[]\n',b'null\n'):
            decoder=probe.WireDecoder(); decoder.feed(data)
            self.assertEqual(decoder.counts["invalid_lines"],1)

    def test_overflow_discards_until_newline_then_recovers(self):
        decoder=probe.WireDecoder()
        result=decoder.feed(b'x'*(probe.MAX_WIRE_LINE+1)+b'\n{"ok":1}\n')
        self.assertEqual(result,[{"ok":1}]); self.assertEqual(decoder.counts["oversized_lines"],1)

    def test_large_coalesced_chunk_is_not_line_overflow(self):
        decoder=probe.WireDecoder()
        self.assertEqual(len(decoder.feed(b'{"a":1}\n'*5000)),5000)
        self.assertFalse(decoder.counts["oversized_lines"])


class AcceptanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.base=fixture()
    def setUp(self): self.records=copy.deepcopy(self.base)
    def test_complete_fixture_checks_pass_without_hardware_claim(self):
        report=run(self.records)
        self.assertEqual(report["status"],"PASS",[c for c in report["checks"] if c["status"]!="PASS"])
        self.assertFalse(report["usb_transport_independently_verified"])
        self.assertFalse(report["motion_authorized"])
        self.assertEqual(gate(report,"feedback_timing")["measurements"]["firmware_hz"],50)

    def test_synthetic_label_cannot_be_overridden(self):
        self.records[0]["data"]["context"]["synthetic"]=True
        self.assertEqual(run(self.records)["status"],"BLOCKED")
        self.assertEqual(run(self.records)["declared_evidence_kind"],"synthetic")

    def test_unconfirmed_and_rover_blocked(self):
        for target in ("unconfirmed","rover"):
            report=probe.analyze_records(self.records,target=target,evidence_kind="operator-capture")
            self.assertEqual(gate(report,"target")["status"],"BLOCKED")

    def test_unknown_origin_blocked(self):
        self.assertEqual(probe.analyze_records(self.records,target="dog12")["status"],"BLOCKED")

    def test_parsed_pixel_cannot_pass_framing_or_age(self):
        report=run(fixture(raw=False))
        self.assertEqual(gate(report,"byte_framing")["status"],"BLOCKED")
        self.assertEqual(gate(report,"feedback_handling_age")["status"],"BLOCKED")

    def test_empty_blocked(self): self.assertEqual(run([])["status"],"BLOCKED")
    def test_short_capture_blocked(self): self.assertEqual(gate(run(fixture(3)),"sample_coverage")["status"],"BLOCKED")
    def test_interrupted_capture_blocked(self):
        self.records.pop(); self.assertEqual(gate(run(self.records),"capture_integrity")["status"],"BLOCKED")
    def test_dropped_records_fail(self):
        self.records[-1]["data"]["dropped_records"]=1
        self.assertEqual(gate(run(self.records),"capture_integrity")["status"],"FAIL")
    def test_changed_session_or_index_fail(self):
        for key,value in (("session_id","other"),("record_index",999),("host_monotonic_ns",False)):
            records=copy.deepcopy(self.base); records[100][key]=value
            self.assertEqual(gate(run(records),"capture_integrity")["status"],"FAIL")
    def test_host_clock_regression_fail(self):
        self.records[100]["host_monotonic_ns"]=0
        self.assertEqual(gate(run(self.records),"capture_integrity")["status"],"FAIL")
    def test_wrong_firmware_fail(self):
        self.assertEqual(gate(run(self.records,expected_firmware="different"),"firmware_identity")["status"],"FAIL")
    def test_armed_state_fail(self):
        mutate_state(self.records,lambda m:m.update(armed=True))
        self.assertEqual(gate(run(self.records),"torque_off_monitor_scope")["status"],"FAIL")
    def test_unsafe_command_fail(self):
        r,m=next(messages(self.records,"tx")); m["cmd"]="policy_arm"; edit_message(r,m)
        self.assertEqual(gate(run(self.records),"torque_off_monitor_scope")["status"],"FAIL")
    def test_raw_malformed_hex_fail(self):
        self.records[2]["data"]["hex"]="xx"
        self.assertEqual(gate(run(self.records),"byte_framing")["status"],"FAIL")
    def test_mixed_parsed_raw_fail(self):
        r,m=next(messages(self.records)); r["type"]="robot_rx"; r["data"]={"message":m}
        self.assertEqual(gate(run(self.records),"byte_framing")["status"],"FAIL")
    def test_truncated_wire_fail(self):
        self.records[-2]["data"]["hex"]=self.records[-2]["data"]["hex"][:-2]
        self.assertEqual(gate(run(self.records),"byte_framing")["status"],"FAIL")
    def test_firmware_error_fail(self):
        r,m=list(messages(self.records))[-1]; edit_message(r,{"type":"error","message":"invalid json"})
        self.assertEqual(gate(run(self.records),"firmware_errors")["status"],"FAIL")
    def test_uart_counter_delta_and_reset_fail(self):
        for value in ([1,0,0,0],[-1,0,0,0]):
            records=copy.deepcopy(self.base)
            r,m=[(r,m) for r,m in messages(records) if m["type"]=="hello"][-1]
            m["serialErrorCounts"]=value; edit_message(r,m)
            self.assertNotEqual(gate(run(records),"uart_errors")["status"],"PASS")
    def test_missing_post_hello_blocks_uart(self):
        r,m=[(r,m) for r,m in messages(self.records) if m["type"]=="hello"][-1]
        edit_message(r,{"type":"other"})
        self.assertEqual(gate(run(self.records),"uart_errors")["status"],"BLOCKED")
    def test_tick_gap_and_repeat_fail(self):
        for tick in (100,103):
            records=copy.deepcopy(self.base); mutate_state(records,lambda m:m.update(tick=tick))
            self.assertEqual(gate(run(records),"feedback_sequence")["status"],"FAIL")
    def test_cumulative_missed_periods_not_summed(self):
        for r,m in messages(self.records):
            if m.get("type")=="policy_monitor_state": m["missed_feedback_periods"]=2; edit_message(r,m)
        result=gate(run(self.records),"firmware_deadlines")
        self.assertEqual(result["status"],"FAIL"); self.assertEqual(result["measurements"]["missed_periods"],2)
    def test_sample_clock_wrap_passes(self):
        for r,m in messages(self.records):
            if "sample_ms" in m: m["sample_ms"]=(m["sample_ms"]+probe.UINT32-100)&probe.UINT32; edit_message(r,m)
        self.assertEqual(gate(run(self.records),"feedback_timing")["status"],"PASS")
    def test_startup_stall_not_hidden_by_warmup(self):
        mutate_state(self.records,lambda m:m.update(sample_ms=500),index=3)
        self.assertEqual(gate(run(self.records),"feedback_timing")["status"],"FAIL")
    def test_stale_callback_age_fail(self):
        r,m=[(r,m) for r,m in messages(self.records) if m["type"]=="policy_monitor_state"][100]
        r["data"]["received_monotonic_ns"]-=110_000_000
        self.assertEqual(gate(run(self.records),"feedback_handling_age")["status"],"FAIL")
    def test_future_callback_rejected(self):
        self.records[2]["data"]["received_monotonic_ns"]=1
        self.assertEqual(gate(run(self.records),"byte_framing")["status"],"FAIL")
    def test_unknown_ack_fail(self):
        mutate_state(self.records,lambda m:m.update(seq=999999))
        self.assertEqual(gate(run(self.records),"command_acknowledgement")["status"],"FAIL")
    def test_initial_can_preamble_is_recognized_in_tx_only(self):
        self.assertEqual(gate(run(self.records),"byte_framing")["status"],"PASS")
        self.assertEqual(gate(run(self.records),"input_abort_handshake")["status"],"PASS")
        self.records[2]["data"]["hex"]=self.records[2]["data"]["hex"]+"180a"
        self.assertEqual(gate(run(self.records),"byte_framing")["status"],"FAIL")
    def test_stale_nonce_ack_does_not_pass(self):
        r,m=next(messages(self.records)); m["nonce"]="b"*32; edit_message(r,m)
        self.assertEqual(gate(run(self.records),"input_abort_handshake")["status"],"BLOCKED")
    def test_bounded_midstream_attach_fragment_before_nonce_ack_is_reported(self):
        self.records[2]["data"]["hex"] = b'old partial}\n'.hex()+self.records[2]["data"]["hex"]
        result=gate(run(self.records),"byte_framing")
        self.assertEqual(result["status"],"PASS")
        self.assertEqual(result["measurements"]["tolerated_attachment_fragments"],1)
    def test_corruption_after_ack_in_same_chunk_still_fails(self):
        self.records[2]["data"]["hex"] = b'partial}\n'.hex()+self.records[2]["data"]["hex"]+b'corrupt}\n'.hex()
        result=gate(run(self.records),"byte_framing")
        self.assertEqual(result["status"],"FAIL")
        self.assertEqual(result["measurements"]["tolerated_attachment_fragments"],1)
        self.assertEqual(result["measurements"]["post_handshake_or_unqualified_framing_errors"],1)
    def test_excess_startup_corruption_is_not_waived(self):
        self.records[2]["data"]["hex"] = (b'bad\n'*33).hex()+self.records[2]["data"]["hex"]
        self.assertEqual(gate(run(self.records),"byte_framing")["status"],"FAIL")
    def test_startup_byte_limit_is_not_waived(self):
        self.records[2]["data"]["hex"] = ((b'x'*3000+b'\n')*22).hex()+self.records[2]["data"]["hex"]
        self.assertEqual(gate(run(self.records),"byte_framing")["status"],"FAIL")
    def test_unmatched_ack_never_qualifies_startup_fragment(self):
        r,m=next(messages(self.records)); m["nonce"]="b"*32; edit_message(r,m)
        self.records[2]["data"]["hex"] = b'partial}\n'.hex()+self.records[2]["data"]["hex"]
        result=gate(run(self.records),"byte_framing")
        self.assertEqual(result["status"],"FAIL")
        self.assertEqual(result["measurements"]["tolerated_attachment_fragments"],0)
    def test_old_firmware_blocks_paired_transport_even_when_expected(self):
        for r,m in messages(self.records):
            if m.get("type")=="hello":
                m["version"]="0.1.17-rxdiag"; m.pop("supportsSerialInputAbort"); edit_message(r,m)
        report=run(self.records,expected_firmware="0.1.17-rxdiag")
        self.assertEqual(gate(report,"firmware_identity")["status"],"PASS")
        self.assertEqual(gate(report,"reconnect_abort_compatibility")["status"],"BLOCKED")
    def test_teardown_missing_is_blocked(self):
        self.records[-1]["data"].pop("transport_teardown")
        self.assertEqual(gate(run(self.records),"transport_teardown")["status"],"BLOCKED")
    def test_torque_disable_only_allowed(self):
        r,m=next(messages(self.records,"tx")); edit_message(r,{"cmd":"servo_torque","all":True,"enabled":False})
        self.assertEqual(gate(run(self.records),"torque_off_monitor_scope")["status"],"PASS")
        edit_message(r,{"cmd":"servo_torque","all":True,"enabled":True})
        self.assertEqual(gate(run(self.records),"torque_off_monitor_scope")["status"],"FAIL")
    def test_stalled_ack_fails_even_if_final_catches_up(self):
        for r,m in messages(self.records):
            if m.get("type")=="policy_monitor_state" and m["tick"]<1251:
                m["seq"]=0; edit_message(r,m)
        result=gate(run(self.records),"command_acknowledgement")
        self.assertEqual(result["status"],"FAIL")
        self.assertGreater(result["measurements"]["latest_sent_minus_ack"]["max"],1000)
        self.assertGreater(result["measurements"]["repeated_ack_count"],1000)
        self.assertGreater(result["measurements"]["oldest_unacknowledged_record_age_ms"]["max"],120)
    def test_invalid_message_type_fails_without_crashing(self):
        r,m=next(messages(self.records)); m["type"]={"bad":1}; edit_message(r,m)
        self.assertEqual(gate(run(self.records),"byte_framing")["status"],"FAIL")
    def test_final_unacked_blocks(self):
        mutate_state(self.records,lambda m:m.update(seq=1249),index=1250)
        self.assertEqual(gate(run(self.records),"command_acknowledgement")["status"],"BLOCKED")
    def test_duplicate_servo_id_fail(self):
        mutate_state(self.records,lambda m:m.update(ids=[1]*12))
        self.assertEqual(gate(run(self.records),"servo_feedback")["status"],"FAIL")
    def test_null_angle_and_status_error_fail(self):
        for key,values in (("angles_deg",[None]+[180.]*11),("status_errors",[1]+[0]*11)):
            records=copy.deepcopy(self.base); mutate_state(records,lambda m:m.update({key:values}))
            self.assertEqual(gate(run(records),"servo_feedback")["status"],"FAIL")
    def test_missing_current_not_zero_or_complete(self):
        for r,m in messages(self.records):
            if "current_raw" in m: m["current_raw"]=[None]*12; m["current_complete"]=False; edit_message(r,m)
        report=run(self.records)
        self.assertEqual(gate(report,"servo_current")["status"],"FAIL")
        self.assertEqual(gate(report,"servo_feedback")["measurements"]["servos"]["1"]["current_raw"]["count"],0)
    def test_missing_imu_blocked_and_zero_gravity_fail(self):
        mutate_state(self.records,lambda m:m.pop("accel_mg"))
        self.assertEqual(gate(run(self.records),"imu")["status"],"BLOCKED")
        mutate_state(self.records,lambda m:m.update(accel_mg=[0,0,0]))
        self.assertEqual(gate(run(self.records),"imu")["status"],"FAIL")
    def test_processing_missing_blocked_and_overrun_fail(self):
        mutate_state(self.records,lambda m:m.pop("frame_us"))
        self.assertEqual(gate(run(self.records),"firmware_processing")["status"],"BLOCKED")
        mutate_state(self.records,lambda m:m.update(frame_us=20000))
        self.assertEqual(gate(run(self.records),"firmware_processing")["status"],"FAIL")


class FileAndCliTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/"capture.jsonl"
    def write(self,records): self.path.write_text("".join(json.dumps(r)+"\n" for r in records),encoding="utf-8")
    def test_regular_file_hash_and_identity(self):
        self.write(fixture(3)); records,source=probe.read_capture(self.path)
        self.assertEqual(len(records),18); self.assertEqual(len(source["sha256"]),64)
    def test_truncated_capture_and_duplicate_key_rejected(self):
        for content in ('{"a":1}', '{"a":1,"a":2}\n'):
            self.path.write_text(content)
            with self.assertRaises(ValueError): probe.read_capture(self.path)
    def test_symlink_rejected(self):
        self.write(fixture(3)); link=self.path.with_name("link"); link.symlink_to(self.path)
        with self.assertRaises(ValueError): probe.read_capture(link)
    @unittest.skipUnless(hasattr(os,"mkfifo"),"FIFO creation unavailable on this OS")
    def test_fifo_rejected_before_open(self):
        os.mkfifo(self.path)
        with self.assertRaises(ValueError): probe.read_capture(self.path)
    def test_size_limit(self):
        self.path.write_text("123456\n")
        with patch.object(probe,"MAX_FILE_BYTES",3), self.assertRaises(ValueError): probe.read_capture(self.path)
    def test_cli_synthetic_json_and_exit(self):
        self.write(fixture(3,synthetic=True))
        result=subprocess.run([sys.executable,"-B","-m","deployment_readiness.hardware_acceptance",str(self.path),"--target","dog12","--evidence-kind","synthetic"],capture_output=True,text=True)
        self.assertEqual(result.returncode,2,result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"],"BLOCKED")
    def test_cli_missing_file_blocked(self):
        result=subprocess.run([sys.executable,"-B","-m","deployment_readiness.hardware_acceptance",str(self.path)],capture_output=True,text=True)
        self.assertEqual(result.returncode,2); self.assertEqual(json.loads(result.stdout)["status"],"BLOCKED")
    def test_cli_no_writes_network_or_processes(self):
        self.write(fixture(3,synthetic=True))
        script='''import sys, runpy
sys.dont_write_bytecode = True
def audit(event,args):
    if event in {"subprocess.Popen", "os.system", "socket.__new__", "os.mkdir", "os.remove", "os.rename"}: raise RuntimeError(event)
    if event == "open" and isinstance(args[2],int) and args[2] & (1|2|64|512|1024): raise RuntimeError("write open")
sys.addaudithook(audit)
sys.argv=["probe",sys.argv[1],"--target","dog12","--evidence-kind","synthetic"]
runpy.run_module("deployment_readiness.hardware_acceptance",run_name="__main__")
'''
        result=subprocess.run([sys.executable,"-B","-c",script,str(self.path)],capture_output=True,text=True)
        self.assertEqual(result.returncode,2,result.stderr); self.assertEqual(json.loads(result.stdout)["status"],"BLOCKED")


if __name__ == "__main__": unittest.main()
