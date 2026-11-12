"""Current Pixel actor + production Kotlin math, without Android or transport.

Optional dependencies are imported only when PixelActor is constructed. This
module has no live-robot mode and no model-provider/network configuration.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import queue
import signal
import subprocess
import threading
import time
from typing import Any

from .runtime_bridge import BridgeError, ShadowCommandBridge
from .supervisor import CapabilitySnapshot

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ASSETS = ROOT / "pixel_robot/app/src/main/assets"
CALIBRATION = "assembly-four-leg-linkage-12dof.calibration.json"
BUNDLE_FILES = ("policy_metadata.json", "policy_android_manifest.json", "policy_actor.onnx",
                "stride_reference.json", CALIBRATION, "policy_reference_vectors.json")


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise BridgeError("duplicate_asset_key")
        result[key] = value
    return result


def _json(raw: bytes | str):
    def invalid(_value):
        raise BridgeError("nonfinite_json")
    return json.loads(raw, object_pairs_hook=_object, parse_constant=invalid)


def _vector(value: Any, size: int) -> list[float]:
    if (type(value) not in (list, tuple) or len(value) != size
            or any(type(v) not in (int, float) or not math.isfinite(v) for v in value)):
        raise BridgeError("invalid_runtime_vector")
    return list(value)


class PixelActor:
    """Load exact checked-in ONNX bytes, bind assets and verify exported parity."""
    def __init__(self, assets: Path = DEFAULT_ASSETS, *, max_duration_ms: int = 1_000):
        import numpy as np
        import onnxruntime as ort
        self.np, self.ort = np, ort
        self.assets = Path(assets)
        raw = {}
        for name in BUNDLE_FILES:
            path = self.assets / name
            if path.stat().st_size > 8 * 1024 * 1024:
                raise BridgeError("oversized_runtime_asset")
            raw[name] = path.read_bytes()
        self.asset_sha256 = {name: hashlib.sha256(data).hexdigest() for name, data in raw.items()}
        metadata = _json(raw["policy_metadata.json"])
        manifest = _json(raw["policy_android_manifest.json"])
        reference = _json(raw["stride_reference.json"])
        calibration = _json(raw[CALIBRATION])
        self.capabilities = CapabilitySnapshot.from_metadata(metadata, max_duration_ms=max_duration_ms)
        self.metadata = metadata
        self.servo_ids = [item['servo_id'] for item in sorted(calibration['joints'], key=lambda item: item['policy_index'])]
        if (metadata.get("observation_builder") != "current_body_v22_428"
                or metadata.get("observation_size") != 428
                or metadata.get("action_size") != 12
                or metadata.get("joint_coordinate_convention") != "cad_drives_v1"
                or manifest.get("schema_version") != 2
                or manifest.get("profile_id") != metadata["profile_id"]
                or manifest.get("profile_sha256") != metadata["profile_sha256"]
                or manifest.get("source_weights_sha256") != metadata["weights_sha256"]
                or manifest.get("onnx_sha256") != self.asset_sha256["policy_actor.onnx"]
                or metadata.get("reference_sha256") != self.asset_sha256["stride_reference.json"]
                or metadata["stride_reference_contract"].get("sha256") != self.asset_sha256["stride_reference.json"]
                or reference.get("profile_sha256") != metadata["profile_sha256"]
                or reference.get("joint_coordinate_convention") != "cad_drives_v1"
                or calibration.get("robot") != metadata["profile_id"]
                or calibration.get("joint_coordinate_convention") != "cad_drives_v1"):
            raise BridgeError("runtime_bundle_identity_mismatch")
        # This loads bytes rather than a path; ONNX external data is unsupported.
        # The selected provider is CPU only, with no discovery or hardware path.
        ort.disable_telemetry_events()
        options = ort.SessionOptions()
        options.intra_op_num_threads = options.inter_op_num_threads = 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        options.log_severity_level = 3
        self._actor = ort.InferenceSession(raw["policy_actor.onnx"], options,
                                           providers=["CPUExecutionProvider"])
        self._actor.disable_fallback()
        inputs, outputs = self._actor.get_inputs(), self._actor.get_outputs()
        if (len(inputs) != 1 or inputs[0].name != "observation"
                or inputs[0].shape != [1, 428] or inputs[0].type != "tensor(float)"
                or len(outputs) != 1 or outputs[0].name != "action"
                or outputs[0].shape != [1, 12] or outputs[0].type != "tensor(float)"
                or self._actor.get_providers() != ["CPUExecutionProvider"]):
            raise BridgeError("runtime_actor_interface_mismatch")
        vectors = _json(raw["policy_reference_vectors.json"])
        if (vectors.get("checkpoint_sha256") != metadata["checkpoint_sha256"]
                or vectors.get("observation_size") != 428 or vectors.get("action_size") != 12
                or len(vectors.get("cases", [])) != 8
                or vectors.get("atol") != 2e-6 or vectors.get("rtol") != 1e-5
                or manifest.get("parity_cases") != 8
                or manifest.get("parity_atol") != 2e-6 or manifest.get("parity_rtol") != 1e-5):
            raise BridgeError("runtime_reference_contract_mismatch")
        max_error = 0.0
        for case in vectors["cases"]:
            observed = np.asarray(self.action(case["observation"]), dtype=np.float32)
            expected = np.asarray(_vector(case["action"], 12), dtype=np.float32)
            if not np.allclose(observed, expected, atol=2e-6, rtol=1e-5):
                raise BridgeError("runtime_actor_reference_parity_failed")
            max_error = max(max_error, float(np.max(np.abs(observed - expected))))
        self.parity = {"cases": 8, "atol": 2e-6, "rtol": 1e-5, "max_abs_error": max_error}

    def action(self, observation: list[float]) -> list[float]:
        values = self.np.asarray(_vector(observation, 428), dtype=self.np.float32)
        if not self.np.all(self.np.isfinite(values)):
            raise BridgeError("runtime_float32_overflow")
        result = self._actor.run(["action"], {"observation": values.reshape(1, 428)})[0]
        if result.shape != (1, 12) or not self.np.all(self.np.isfinite(result)):
            raise BridgeError("runtime_actor_invalid_result")
        return result[0].tolist()


class PixelMathProcess:
    """Bounded local JSONL peer running only the repo's production Kotlin math.

    `command` is trusted host configuration, never model/user operation text.
    No shell is used. The peer must not include Android, USB or a controller.
    """
    def __init__(self, command: list[str], *, timeout_s: float = 5.0):
        if not command or not 0 < timeout_s <= 30:
            raise BridgeError("invalid_math_process_configuration")
        self._timeout = timeout_s
        self._process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                         stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
                                         bufsize=1, start_new_session=(os.name == "posix"))
        self._queue: queue.Queue = queue.Queue(maxsize=4)
        self._closed = False
        self._writers: list[threading.Thread] = []
        self._reader = threading.Thread(target=self._read, name="pixel-math-jsonl", daemon=True)
        self._reader.start()

    def _read(self):
        try:
            while True:
                line = self._process.stdout.readline(65537)
                if not line or len(line) > 65536 or not line.endswith('\n'):
                    self._queue.put_nowait(None)
                    return
                self._queue.put_nowait(line)
        except (OSError, ValueError, queue.Full):
            return

    def exchange(self, value: dict[str, Any]) -> dict[str, Any]:
        if self._closed:
            raise BridgeError("math_process_closed")
        try:
            deadline = time.monotonic() + self._timeout
            line = json.dumps(value, allow_nan=False, separators=(',', ':'))
            if len(line) > 65535:
                raise BridgeError("oversized_math_request")
            written: queue.Queue = queue.Queue(maxsize=1)
            def write():
                try:
                    self._process.stdin.write(line + '\n')
                    self._process.stdin.flush()
                    written.put_nowait(True)
                except (OSError, ValueError):
                    written.put_nowait(False)
            writer = threading.Thread(target=write, name="pixel-math-write", daemon=True)
            self._writers = [writer]
            writer.start()
            if not written.get(timeout=max(0.001, deadline - time.monotonic())):
                raise BridgeError("math_process_failed")
            writer.join(timeout=1)
            response = self._queue.get(timeout=max(0.001, deadline - time.monotonic()))
            parsed = _json(response) if response is not None else None
            if type(parsed) is not dict or parsed.get("error") or parsed.get("ok") is False:
                raise BridgeError("math_process_failed")
            return parsed
        except (OSError, ValueError, queue.Empty, TypeError):
            self.close()
            raise BridgeError("math_process_failed") from None

    def close(self):
        if self._closed:
            return
        self._closed = True
        if os.name == "posix":
            try:
                os.killpg(self._process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        elif self._process.poll() is None:
            self._process.terminate()
        try:
            self._process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait()
        if os.name == "posix":
            try:
                os.killpg(self._process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        self._reader.join(timeout=2)
        for writer in self._writers:
            writer.join(timeout=2)
        if self._reader.is_alive() or any(writer.is_alive() for writer in self._writers):
            # Never wait on a buffered stream lock still owned by a live thread.
            raise BridgeError("math_process_cleanup_unconfirmed")
        for stream in (self._process.stdin, self._process.stdout):
            try:
                stream.close()
            except OSError:
                pass


class PixelShadowRuntime:
    """Join the real session, exact Kotlin observation/action math and ONNX actor.

    Returned values are discardable diagnostics. `complete` acknowledges only a
    synthetic/replay calculation, not transport, movement or task completion.
    """
    def __init__(self, bridge: ShadowCommandBridge, actor: PixelActor, peer: PixelMathProcess):
        if bridge.capabilities.digest != actor.capabilities.digest:
            raise BridgeError("bridge_actor_capability_mismatch")
        self.bridge, self.actor, self.peer = bridge, actor, peer
        self._compute_lock = threading.Lock()
        self._generation = None
        self._closed = False

    def _reset(self, generation: int):
        result = self.peer.exchange({"op": "reset"})
        for field, name in (("metadata_sha256", "policy_metadata.json"),
                            ("stride_reference_sha256", "stride_reference.json"),
                            ("calibration_sha256", CALIBRATION)):
            if result.get(field) != self.actor.asset_sha256[name]:
                raise BridgeError("jvm_actor_asset_mismatch")
        if result.get("hardware_access") is not False:
            raise BridgeError("jvm_hardware_scope_mismatch")
        self._generation = generation

    def step(self, *, sample_sequence: int, observed_at_ms: int, sensor: dict[str, Any],
             acknowledged_action: list[float], evidence_kind: str) -> dict[str, Any]:
        if self._closed:
            raise BridgeError("runtime_closed")
        if evidence_kind not in ("synthetic_feedback", "recorded_feedback_replay"):
            raise BridgeError("runtime_evidence_label_required")
        if not self._compute_lock.acquire(blocking=False):
            raise BridgeError("runtime_compute_already_in_flight")
        ticket = None
        try:
            ticket = self.bridge.begin_frame(sample_sequence=sample_sequence, observed_at_ms=observed_at_ms)
            if ticket.capability_digest != self.actor.capabilities.digest:
                raise BridgeError("bridge_actor_capability_mismatch")
            if self._generation != ticket.generation:
                self._reset(ticket.generation)
            posture = [0.0, 0.0, 0.0]
            command = list(ticket.command)
            if type(sensor) is not dict or set(sensor) != {"imu", "position", "velocity", "current", "dt"}:
                raise BridgeError("invalid_runtime_sensor")
            imu = _vector(sensor.get('imu'), 6)
            _vector(sensor.get('position'), 12)
            _vector(sensor.get('velocity'), 12)
            _vector([sensor.get('dt')], 1)
            current = sensor.get('current')
            if (type(current) is not list or len(current) != 12
                    or any(v is not None and (type(v) is not int or not -32768 <= v <= 32767) for v in current)):
                raise BridgeError("invalid_runtime_current")
            observed = self.peer.exchange({"op": "observe", "sensor": sensor,
                                          "command": command, "posture": posture,
                                          "acknowledged_action": _vector(acknowledged_action, 12)})
            observation = _vector(observed.get("observation"), 428)
            residual = self.actor.action(observation)
            processed = self.peer.exchange({"op": "action", "residual": residual,
                                            "command": command, "posture": posture,
                                            "gravity": imu[3:]})
            filtered = _vector(processed.get('filtered'), 12)
            applied = _vector(processed.get('applied'), 12)
            targets = _vector(processed.get('targets_deg'), 12)
            if (processed.get('servo_ids') != self.actor.servo_ids
                    or observed.get('hardware_access') is not False
                    or processed.get('hardware_access') is not False):
                raise BridgeError("jvm_runtime_output_identity_mismatch")
            completed = self.peer.exchange({"op": "complete"})
            if completed.get('acknowledgement') != 'synthetic_host_only':
                raise BridgeError("jvm_runtime_acknowledgement_mismatch")
            self.bridge.finish_frame(ticket)
            return {"evidence_kind": evidence_kind, "physical_execution": False,
                    "physical_stop_confirmed": False, "request_id": ticket.request_id,
                    "sample_sequence": sample_sequence, "capability_digest": ticket.capability_digest,
                    "command": command, "observation": observation, "actor_residual": residual,
                    "filtered_action": filtered, "applied_action": applied,
                    "servo_ids": list(self.actor.servo_ids),
                    "diagnostic_targets_deg": targets}
        except BridgeError:
            if ticket is not None:
                self.bridge.fail_frame(ticket)
            self._generation = None
            raise
        except Exception:
            if ticket is not None:
                self.bridge.fail_frame(ticket)
            self._generation = None
            raise BridgeError("runtime_computation_failed") from None
        finally:
            self._compute_lock.release()

    def close(self):
        self._closed = True
        self.bridge.close()  # Fence in-flight results before process cleanup.
        self.peer.close()
