"""Executable, authored-intent → current Pixel compute-only offline demonstration."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time

from .pixel_runtime import DEFAULT_ASSETS, ROOT, PixelActor, PixelMathProcess, PixelShadowRuntime
from .runtime_bridge import BridgeError, ShadowCommandBridge


class ReplayClock:
    """Deterministic fixture time, explicitly not measured transport timing."""
    now = 0
    def __call__(self):
        return self.now


def synthetic_sensor(index: int = 0):
    return {"imu": [0.0, 0.0, 0.0, 0.0, 0.0, -1.0],
            "position": [0.0] * 12, "velocity": [0.0] * 12,
            "current": [None if (index + joint) % 11 == 0 else 0 for joint in range(12)],
            "dt": 0.02}


def _run_preparation(command: list[str], *, timeout_s: float = 240):
    """Own cold-compiler descendants too, including an interrupted preparation."""
    process = subprocess.Popen(command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, encoding='utf-8', start_new_session=(os.name == 'posix'))
    try:
        stdout, stderr = process.communicate(timeout=timeout_s)
        return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
    except BaseException:
        if os.name == 'posix':
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        else:
            process.kill()
        process.communicate(timeout=2)
        raise


def math_command() -> list[str]:
    """Bounded cold preparation before any shadow envelope/frame is issued.

    The repository-owned verifier compiles/checks source and dependency hashes;
    it does not start a peer or download anything. Per-frame timeouts remain
    separate from this explicitly bounded 240-second cold-build budget.
    """
    command = [sys.executable, str(ROOT / "pixel_robot/tools/run_host_kotlin_tests.py"), "--bridge-command"]
    cache = os.environ.get("ROBOT_PIXEL_JVM_CACHE")
    if cache:
        command.extend(["--cache-dir", cache])
    try:
        prepared = _run_preparation(command, timeout_s=240)
        report = json.loads(prepared.stdout)
        if type(report) is not dict:
            raise BridgeError('math_preparation_failed_or_blocked')
        argv = report.get('command')
        java = shutil.which('java')
        if (prepared.returncode != 0 or report.get('status') != 'bridge_ready'
                or report.get('hardware_access') is not False
                or type(argv) is not list or len(argv) != 5
                or any(type(part) is not str or not part for part in argv)
                or java is None or Path(argv[0]).resolve() != Path(java).resolve()
                or argv[1] != '-cp'
                or argv[3] != 'com.leo.pixelrobot.host.PolicyFrameBridgeKt'
                or Path(argv[4]).resolve() != DEFAULT_ASSETS.resolve()):
            raise BridgeError('math_preparation_failed_or_blocked')
        return argv
    except (OSError, ValueError, subprocess.SubprocessError):
        raise BridgeError('math_preparation_failed_or_blocked') from None


def run_demo(*, allow_shadow: bool = False, allow_unqualified_metadata: bool = False,
             frames: int = 200) -> dict:
    if allow_shadow is not True or allow_unqualified_metadata is not True:
        raise BridgeError("explicit_shadow_and_unqualified_test_opt_in_required")
    if type(frames) is not int or not 1 <= frames <= 250:
        raise BridgeError("demo_frames_must_be_1_to_250")
    started = time.monotonic()
    actor = PixelActor(DEFAULT_ASSETS, max_duration_ms=5_000)
    clock = ReplayClock()
    bridge = ShadowCommandBridge(actor.capabilities, allow_shadow=True,
                                 allow_unqualified_metadata=True, clock_ms=clock)
    peer = PixelMathProcess(math_command(), timeout_s=5)
    runtime = PixelShadowRuntime(bridge, actor, peer)
    try:
        # Warm local compiled math before issuing the bounded request. There is
        # no model call: these complete proposal bytes are authored test input.
        runtime._reset(-1)
        ready = time.monotonic()
        request = bridge.propose(json.dumps({"schema_version": 1, "kind": "velocity_request",
            "frame": "body", "forward_m_s": .02, "lateral_m_s": 0,
            "yaw_rate_rad_s": 0, "duration_ms": frames * 20}))
        acknowledged = [0.0] * 12
        hold_zero, nonzero_after_hold = True, False
        observation_sizes, residual_sizes = set(), set()
        for index in range(frames):
            clock.now = index * 20
            diagnostic = runtime.step(sample_sequence=index, observed_at_ms=clock.now,
                sensor=synthetic_sensor(index), acknowledged_action=acknowledged,
                evidence_kind="synthetic_feedback")
            observation_sizes.add(len(diagnostic['observation']))
            residual_sizes.add(len(diagnostic['actor_residual']))
            acknowledged = diagnostic['applied_action']
            if index < 50:
                hold_zero = hold_zero and all(value == 0 for value in acknowledged)
            else:
                nonzero_after_hold = nonzero_after_hold or any(value != 0 for value in acknowledged)
        clock.now = frames * 20
        bridge.poll()
        events = bridge.take_events()
        states = [event['state'] for event in events if event['event'] == 'outcome' and event.get('request_id') == request]
        if (states != ['shadow_accepted', 'expired'] or not hold_zero
                or observation_sizes != {428} or residual_sizes != {12}
                or (frames > 50 and not nonzero_after_hold)):
            raise BridgeError("demo_invariant_failed")
        # A separate one-use pending diagnostic ticket demonstrates immediate
        # trusted STOP fencing. It does not send a stop to any physical device.
        clock.now += 20
        bridge.propose(json.dumps({"schema_version": 1, "kind": "velocity_request",
            "frame": "body", "forward_m_s": .02, "lateral_m_s": 0,
            "yaw_rate_rad_s": 0, "duration_ms": 100}))
        revoked = bridge.begin_frame(sample_sequence=frames, observed_at_ms=clock.now)
        bridge.stop()
        try:
            bridge.finish_frame(revoked)
        except BridgeError:
            stop_fenced = True
        else:
            raise BridgeError("demo_stop_failed_to_fence_frame")
        stop_events = [event for event in bridge.take_events() if event['event'] == 'outcome'
                       and event['state'] == 'stop_requested']
        if len(stop_events) != 1 or stop_events[0]['physical_stop_confirmed'] is not False:
            raise BridgeError("demo_stop_outcome_failed")
        return {"schema_version": 1, "status": "passed", "frames": frames,
                "evidence_kind": "authored_proposal_real_actor_and_kotlin_synthetic_feedback",
                "physical_execution": False, "physical_stop_confirmed": False,
                "language_model_inference": False, "policy_actor_inference": True, "hardware_access": False,
                "clock_kind": "deterministic_fixture_clock_not_realtime_measurement",
                "observation_size": 428, "actor_output_size": 12,
                "first_50_applied_actions_zero": hold_zero,
                "nonzero_actions_after_hold": nonzero_after_hold,
                "outcome_states": states, "actor_parity": actor.parity,
                "stop_outcome": "stop_requested", "revoked_frame_publication_rejected": stop_fenced,
                "capability_digest": actor.capabilities.digest,
                "asset_sha256": actor.asset_sha256,
                "dependencies": {"numpy": actor.np.__version__, "onnxruntime": actor.ort.__version__},
                "cold_start_wall_seconds": ready - started,
                "fixture_run_wall_seconds": time.monotonic() - ready,
                "wall_seconds_including_jvm_startup": time.monotonic() - started}
    finally:
        runtime.close()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--allow-shadow', action='store_true')
    parser.add_argument('--allow-unqualified-metadata', action='store_true')
    parser.add_argument('--frames', type=int, default=200)
    args = parser.parse_args(argv)
    try:
        report = run_demo(allow_shadow=args.allow_shadow,
                          allow_unqualified_metadata=args.allow_unqualified_metadata,
                          frames=args.frames)
    except Exception:
        print(json.dumps({"status": "failed_or_blocked", "error": "offline_runtime_demo_failed",
                          "physical_execution": False, "hardware_access": False}))
        return 2
    print(json.dumps(report, sort_keys=True, allow_nan=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
