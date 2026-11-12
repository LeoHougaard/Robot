"""Bounded static setup diagnostics, not deployment authorization.

No project modules, optional packages, subprocesses, sockets or device APIs are
imported/executed. Input paths must name ordinary files. Output is stdout only.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.metadata
import io
import json
import math
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import zipfile

ASSETS = "pixel_robot/app/src/main/assets/"
POLICY = "pixel_robot/app/src/main/java/com/leo/pixelrobot/policy/"
LEGACY = "robot_dog/policy_runtime/"
AXES = ("forward_m_s", "lateral_m_s", "yaw_rate_rad_s")
SERVO_IDS = [7, 8, 9, 1, 2, 3, 4, 5, 6, 10, 11, 12]
SEMANTICS = [f"{leg}_{joint}" for leg in ("front_right", "front_left", "back_right", "back_left")
             for joint in ("hip_abduction", "hip_flexion", "knee_flexion")]
PINS = {"baml-py": "0.226.2", "pydantic": "2.11.7", "pytest": "9.1.1", "torch": "2.5.1", "numpy": "2.2.6", "onnxruntime": "1.24.3"}
LIMIT = 64 * 1024 * 1024


class Problem(ValueError):
    def __init__(self, code, detail):
        self.code, self.detail = code, detail
        super().__init__(detail)


def require(condition, detail):
    if not condition:
        raise Problem("invalid_contract", detail)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def digest(value):
    return sha(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode())


def strict_json(data):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "duplicate JSON key")
            result[key] = value
        return result
    def constant(_):
        raise Problem("invalid_json", "nonfinite JSON number")
    try:
        value = json.loads(data, object_pairs_hook=pairs, parse_constant=constant)
        # JSON's 1e999 otherwise becomes infinity without parse_constant.
        json.dumps(value, allow_nan=False)
        return value
    except (UnicodeError, ValueError, RecursionError) as exc:
        if isinstance(exc, Problem):
            raise
        raise Problem("invalid_json", "invalid or nonfinite JSON") from exc


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def hash_string(value):
    return type(value) is str and re.fullmatch(r"[0-9a-fA-F]{64}", value) is not None


class Reader:
    """Read ordinary, bounded files only, retaining the bytes actually inspected."""
    def __init__(self, root):
        self.root = Path(root).absolute()
        self.inventory = {}

    def path(self, name):
        item = Path(name)
        if item.is_absolute() or not item.parts or any(p in ("..", ".") for p in item.parts):
            raise Problem("unsafe_path", "only relative file paths beneath the selected root are allowed")
        path = self.root
        for part in item.parts:
            path /= part
            if path.is_symlink():
                raise Problem("unsafe_path", "symlink inputs are not inspected")
        return path

    def read(self, name, limit=LIMIT):
        path = self.path(name)
        try:
            info = path.stat()
            if not stat.S_ISREG(info.st_mode):
                raise Problem("unsafe_path", "input is not an ordinary file")
            if info.st_size > limit:
                raise Problem("oversize", "input exceeds the bounded read limit")
            # O_NOFOLLOW protects the final component on POSIX; no device opens.
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))
            with os.fdopen(descriptor, "rb") as stream:
                start = os.fstat(stream.fileno())
                if not stat.S_ISREG(start.st_mode):
                    raise Problem("unsafe_path", "input changed to a nonregular file")
                data = stream.read(limit + 1)
                end = os.fstat(stream.fileno())
            if len(data) > limit:
                raise Problem("oversize", "input exceeds the bounded read limit")
            if (start.st_size, start.st_mtime_ns, start.st_ino) != (end.st_size, end.st_mtime_ns, end.st_ino):
                raise Problem("changed_during_read", "input changed while being read")
        except FileNotFoundError as exc:
            raise Problem("missing_file", f"missing {name}") from exc
        except OSError as exc:
            raise Problem("unreadable_file", f"cannot read {name}: {type(exc).__name__}") from exc
        entry = {"path": str(name), "size": len(data), "sha256": sha(data)}
        if str(name) in self.inventory and self.inventory[str(name)] != entry:
            raise Problem("changed_during_read", "input changed between checks")
        self.inventory[str(name)] = entry
        return data

    def json(self, name):
        value = strict_json(self.read(name))
        require(type(value) is dict, "expected a JSON object")
        return value

    def text(self, name):
        try:
            return self.read(name).decode("utf-8-sig")
        except UnicodeError as exc:
            raise Problem("invalid_text", "expected UTF-8 source") from exc


def check_calibration(value, *, profile, convention, scale, pixel=False):
    require(value.get("schema_version") == 1 and type(value.get("schema_version")) is int,
            "unsupported calibration schema")
    require(value.get("robot") == profile, "calibration profile mismatch")
    require(value.get("control_hz") == 50 and value.get("action_scale_rad") == scale,
            "calibration rate or action scale mismatch")
    require(value.get("calibrated") is True, "joint calibration flag is not true (a true flag is still only a file claim)")
    require(value.get("joint_coordinate_convention", "legacy_relative_knee") == convention,
            "joint coordinate convention mismatch")
    joints = value.get("joints")
    require(type(joints) is list and len(joints) == 12 and all(type(j) is dict for j in joints), "expected 12 joint objects")
    require(all(type(j.get("policy_index")) is int for j in joints), "invalid policy index type")
    joints = sorted(joints, key=lambda j: j["policy_index"])
    require([j["policy_index"] for j in joints] == list(range(12)), "policy indices must be exactly 0 through 11")
    require([j.get("semantic") for j in joints] == SEMANTICS, "joint semantic order mismatch")
    require(all(type(j.get("servo_id")) is int for j in joints) and [j["servo_id"] for j in joints] == SERVO_IDS,
            "servo mapping must be FR=7/8/9, FL=1/2/3, BR=4/5/6, BL=10/11/12")
    for i, joint in enumerate(joints):
        fields = [joint.get(k) for k in ("zero_deg", "min_deg", "max_deg", "servo_degrees_per_policy_radian")]
        require(all(finite(n) for n in fields), "joint values must be finite numbers")
        zero, low, high, gain = fields
        require(low < zero < high and abs(gain) >= 1, "invalid joint zero, limits, direction or scale")
        expected_parent = i - 1 if convention == "legacy_relative_knee" and i % 3 == 2 else None
        linkage = joint.get("linkage")
        if expected_parent is None:
            require(linkage is None, "unexpected linkage for joint coordinate convention")
        else:
            require(type(linkage) is dict and linkage.get("type") == "four_bar_follow"
                    and linkage.get("parent_policy_index") == expected_parent and linkage.get("parent_ratio") == 1,
                    "missing or incompatible four-bar linkage")
    imu = value.get("imu", {})
    require(type(imu) is dict and imu.get("calibrated") is True, "IMU calibration flag is not true")
    matrix, bias = imu.get("body_axis_from_sensor_axis"), imu.get("gyro_bias_dps")
    require(type(matrix) is list and len(matrix) == 3 and all(type(row) is list and len(row) == 3 for row in matrix),
            "IMU matrix must be 3 by 3")
    require(all(finite(x) for row in matrix for x in row), "nonfinite IMU matrix")
    require(type(bias) is list and len(bias) == 3 and all(finite(x) for x in bias), "invalid gyro bias")
    require(type(imu.get("gravity_sign")) in (int, float) and imu["gravity_sign"] in (-1, 1), "invalid gravity sign")
    for i in range(3):
        for j in range(3):
            require(abs(sum(matrix[i][k] * matrix[j][k] for k in range(3)) - int(i == j)) <= 1e-3,
                    "IMU matrix is not orthonormal")
    if pixel:
        expected = [[0, 1, 0], [-1, 0, 0], [0, 0, 1]]
        require(imu["gravity_sign"] == -1 and all(abs(matrix[i][j] - expected[i][j]) <= 1e-5 for i in range(3) for j in range(3)),
                "Pixel requires rear-facing USB-C/component-side-up board IMU transform")
    return {"joint_count": 12, "file_claims_calibrated": True, "physical_calibration_verified": False,
            "coordinate_convention": convention, "servo_ids_in_policy_order": SERVO_IDS}


def limits(value):
    bounds = value.get("validated_command_limits")
    require(type(bounds) is dict and set(bounds) == set(AXES), "expected exactly three velocity-axis limits")
    for axis in AXES:
        pair = bounds[axis]
        require(type(pair) is list and len(pair) == 2 and all(finite(x) for x in pair), "invalid command limits")
        require(pair[0] <= 0 <= pair[1], "command limits must be ordered and include zero")
    return bounds


def npz_headers(data):
    """Inspect NumPy container/header dimensions only; never deserialize objects."""
    shapes = {}
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            require(len(entries) == 10 and len({e.filename for e in entries}) == 10, "unexpected/duplicate NPZ arrays")
            for entry in entries:
                require(entry.file_size <= 8 * 1024 * 1024, "oversize NPZ member")
                with archive.open(entry) as stream:
                    prefix = stream.read(8)
                    require(prefix[:6] == b"\x93NUMPY" and prefix[6:] in (b"\x01\x00", b"\x02\x00"), "unsupported NPY header")
                    size_bytes = 2 if prefix[6] == 1 else 4
                    raw_length = stream.read(size_bytes)
                    require(len(raw_length) == size_bytes, "truncated NPY header")
                    length = int.from_bytes(raw_length, "little")
                    require(length <= 4096, "oversize NPY header")
                    header = ast.literal_eval(stream.read(length).decode("ascii"))
                    require(type(header) is dict and header.get("descr") in ("<f4", "<f8") and header.get("fortran_order") is False,
                            "NPZ must use numeric C-order arrays, never pickle/object data")
                    shape = header.get("shape")
                    require(type(shape) is tuple and 1 <= len(shape) <= 2 and all(type(x) is int and 0 < x <= 65536 for x in shape),
                            "invalid NPZ shape")
                    expected_bytes = math.prod(shape) * int(header["descr"][-1])
                    require(entry.file_size == 8 + size_bytes + length + expected_bytes, "NPZ payload length mismatch")
                    shapes[entry.filename] = list(shape)
    except (OSError, ValueError, SyntaxError, RuntimeError, zipfile.BadZipFile, EOFError) as exc:
        if isinstance(exc, Problem):
            raise
        raise Problem("invalid_contract", "unreadable NPZ header") from exc
    require(shapes.get("obs_mean.npy") == [180] and shapes.get("obs_var.npy") == [180], "normalization shape mismatch")
    inputs = 180
    for layer in ("0", "1", "2", "out"):
        weight, bias = shapes.get("w" + layer + ".npy"), shapes.get("b" + layer + ".npy")
        require(weight is not None and len(weight) == 2 and weight[1] == inputs and bias == [weight[0]], "actor layer shape mismatch")
        inputs = weight[0]
    require(inputs == 12, "actor output shape mismatch")
    return shapes


def legacy_bundle(reader):
    m = reader.json(LEGACY + "policy_metadata.json")
    expected = {"schema_version": 1, "profile_id": "assembly-1-12dof", "observation_size": 180,
                "observation_history": 4, "action_size": 12, "control_hz": 50, "action_scale_rad": .25,
                "action_delta_limit": .3, "command_smoothing_time_s": .4}
    require(all(m.get(k) == v and not isinstance(m.get(k), bool) for k, v in expected.items()), "legacy 4x45/12-action/rate/smoothing contract mismatch")
    require(m.get("profile_sha256", "").lower() == "b25a4a05fa5a6439b82b824d2c2c826f2a9cc5aacc274d75ee8b4d39978035d3", "legacy runtime profile pin mismatch")
    require(limits(m) == dict(zip(AXES, [[0, .18], [0, 0], [-.25, .25]])), "legacy command envelope mismatch")
    evaluation = m.get("evaluation", {})
    require(evaluation.get("passed") is True and evaluation.get("stage") == "goal", "missing declared Goal pass (not independently replayed)")
    data = reader.read(LEGACY + "policy_weights.npz")
    require(sha(data) == m.get("weights_sha256"), "legacy weights hash mismatch")
    shapes = npz_headers(data)
    calibration = check_calibration(reader.json(LEGACY + "assembly-1-12dof.calibration.json"),
                                   profile=m["profile_id"], convention="legacy_relative_knee", scale=.25)
    return {"profile_id": m["profile_id"], "observation_size": 180, "weights_sha256": sha(data),
            "array_shapes": shapes, "calibration": calibration, "inference_executed": False}


def pixel_bundle(reader):
    m = reader.json(ASSETS + "policy_metadata.json")
    manifest = reader.json(ASSETS + "policy_android_manifest.json")
    require(type(m.get("schema_version")) is int and m["schema_version"] == 2 and manifest.get("schema_version") == 2,
            "unsupported Pixel bundle schema")
    # This deliberately supports the reviewed checked-in V22 candidate only.
    expected = {"profile_id": "assembly-four-leg-linkage-12dof", "observation_builder": "current_body_v22_428",
                "observation_size": 428, "observation_history": 24, "action_size": 12, "control_hz": 50,
                "joint_coordinate_convention": "cad_drives_v1", "checkpoint_epoch": 3750}
    require(all(m.get(k) == v and not isinstance(m.get(k), bool) for k, v in expected.items()), "Pixel candidate/observation contract changed; review current production loader")
    source = reader.text(POLICY + "PolicyContract.kt")
    for key, constant in (("profile_sha256", "PROFILE"), ("weights_sha256", "WEIGHTS"), ("checkpoint_sha256", "CHECKPOINT")):
        found = re.search(r"ACCEPTED_CANDIDATE_" + constant + r'_SHA256\s*=\s*"([0-9a-f]{64})"', source)
        require(found is not None and m.get(key, "").lower() == found.group(1), "Pixel candidate identity does not match production loader pin")
    for key, manifest_key in (("profile_id", "profile_id"), ("profile_sha256", "profile_sha256"), ("weights_sha256", "source_weights_sha256")):
        require(m.get(key) == manifest.get(manifest_key), "ONNX deployment manifest disagrees with metadata")
    actor = reader.read(ASSETS + "policy_actor.onnx")
    require(sha(actor) == manifest.get("onnx_sha256"), "ONNX actor hash mismatch")
    require(limits(m) == dict(zip(AXES, [[-.04, .04], [-.02, .02], [-.1, .1]])), "Pixel accepted candidate command limits changed")
    require(m.get("history_selection") == {"frame_size": 70, "indices": [0, 10, 20, 21, 22, 23], "timing_reference_ms": 20.0}, "selected-history layout mismatch")
    action = m.get("action_contract", {})
    expected_action = {"actor_output_clip": [-1, 1],
                       "applied_normalized_clip_by_joint": [.4 if i % 3 == 0 else 1 for i in range(12)],
                       "low_pass_alpha": .2, "applied_normalized_slew_limit": .2, "position_target_scale_rad": .3}
    require(action == expected_action, "V22 action filter/slew/scale/clip contract mismatch")
    require(m.get("command_smoothing_time_s") == .4, "V22 command smoothing changed")
    posture = m.get("posture_command_contract", {})
    require(posture == {"height_offset_m": [0, 0], "roll_rad": [0, 0], "pitch_rad": [0, 0],
                        "smoothing_time_s": .5, "layout": "append_after_selected_history"}, "V22 posture contract mismatch")
    current = m.get("current_observation_contract", {})
    require(current.get("units") == "mA" and current.get("absolute") is True
            and current.get("missing_behavior") == "hold_last_finite_and_validity_zero"
            and finite(current.get("current_step_ma")) and current["current_step_ma"] > 0, "V22 current contract mismatch")
    for key in ("normalization_bias_ma", "normalization_scale_ma", "clip_normalized"):
        values = current.get(key)
        require(type(values) is list and len(values) == 12 and all(finite(x) for x in values), "invalid current normalization vector")
        require(all(x >= 0 if key == "normalization_bias_ma" else x > 0 for x in values), "invalid current normalization scale")
    stationary = m.get("stationary_action_contract", {})
    require(stationary == {"behavior": "policy_stabilization", "normalized_stance_action": [0] * 12,
                           "planar_command_deadband_m_s": 0, "yaw_command_deadband_rad_s": 0}, "V22 stationary contract mismatch")
    require(m.get("action_semantics") == "stride_reference_plus_residual", "V22 action semantics mismatch")
    stride = m.get("stride_reference_contract", {})
    require(stride.get("asset") == "stride_reference.json" and stride.get("clock") == "float32_20ms_per_fresh_feedback" and stride.get("initial_hold_frames") == 50, "stride reference contract mismatch")
    require(sha(reader.read(ASSETS + "stride_reference.json")) == stride.get("sha256") == m.get("reference_sha256"), "stride reference hash mismatch")
    refs = reader.json(ASSETS + "policy_reference_vectors.json")
    require(refs.get("checkpoint_sha256") == m["checkpoint_sha256"] and refs.get("observation_size") == 428 and refs.get("action_size") == 12,
            "reference-vector metadata mismatch")
    cases = refs.get("cases")
    require(type(cases) is list and len(cases) == manifest.get("parity_cases") and len(cases) > 0, "missing reference-vector cases")
    for row in cases:
        require(type(row) is dict, "invalid reference-vector row")
        for name, size in (("observation", 428), ("action", 12)):
            require(type(row.get(name)) is list and len(row[name]) == size and all(finite(x) for x in row[name]), "reference-vector dimensions or numeric values invalid")
    calibration = check_calibration(reader.json(ASSETS + m["profile_id"] + ".calibration.json"),
                                   profile=m["profile_id"], convention="cad_drives_v1", scale=.3, pixel=True)
    return {**expected, "onnx_sha256": sha(actor), "reference_cases_present": len(cases),
            "onnx_shape_and_parity_executed": False, "calibration": calibration,
            "calibration_source": "repository bundled file only",
            "effective_app_calibration_verified": False,
            "loaded_app_snapshot_verified": False,
            "file_claims_promoted": m.get("promoted"), "file_claims_physical_transport_verified": m.get("physical_transport_verified")}


def capability_contract(reader, name):
    m = reader.json(name)
    require(type(m.get("schema_version")) is int and m["schema_version"] == 2, "shadow capability metadata requires schema 2; legacy schema 1 is incompatible")
    bounds = limits(m)
    for key in ("profile_sha256", "checkpoint_sha256", "weights_sha256"):
        require(hash_string(m.get(key)) and m[key] == m[key].lower(), "shadow capability identities require lowercase SHA-256")
    require(type(m.get("deployment_tier")) is str and bool(m["deployment_tier"].strip()), "missing deployment tier")
    require(type(m.get("promoted")) is bool and type(m.get("physical_transport_verified")) is bool, "qualification flags require literal booleans")
    return {"metadata_schema": 2, "intent_schema": 1, "limits": bounds,
            "unqualified_shadow_opt_in_required": not (m["promoted"] and m["physical_transport_verified"]),
            "authorization_or_physical_readiness": False}


def source_contract_digest(data, symbols=None):
    """Fingerprint only explicitly selected Python contract definitions when possible."""
    if symbols is None:
        return sha(data.replace(b"\r\n", b"\n"))
    try:
        tree = ast.parse(data.decode("utf-8-sig"))
        found = {}
        for node in tree.body:
            names = [node.name] if isinstance(node, (ast.ClassDef, ast.FunctionDef)) else []
            if isinstance(node, ast.Assign):
                names = [target.id for target in node.targets if isinstance(target, ast.Name)]
            for name in names:
                if name in symbols:
                    found[name] = ast.dump(node, include_attributes=False)
        if set(found) != set(symbols):
            raise Problem("contract_drift", "a reviewed Python contract definition is missing")
        return digest(found)
    except (UnicodeError, SyntaxError) as exc:
        raise Problem("contract_drift", "reviewed Python contract source cannot be parsed") from exc


def canonical_pins(reader):
    """Read exact requirement/Step declarations as data, never import the runner."""
    pins, visited = {}, set()
    pattern = r"([A-Za-z0-9_.-]+)==([A-Za-z0-9.+_-]+)"
    def add(name, version):
        name = name.lower().replace("_", "-")
        require(name not in pins or pins[name] == version, "conflicting direct dependency pins")
        pins[name] = version
    def requirements(name, depth=0):
        require(depth <= 5 and name not in visited, "recursive/duplicate requirements inclusion")
        visited.add(name)
        for line in reader.text(name).splitlines():
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            if line.startswith("-r "):
                target = (reader.root / name).parent / line[3:].strip()
                target = Path(os.path.abspath(target))
                try:
                    relative = str(target.relative_to(reader.root))
                except ValueError as exc:
                    raise Problem("unsafe_path", "requirements include leaves repository") from exc
                requirements(relative, depth + 1)
                continue
            match = re.fullmatch(pattern, line)
            require(match is not None, "preflight only supports exact, local direct requirement pins")
            add(*match.groups())
    requirements("validation/requirements.txt")
    try:
        tree = ast.parse(reader.text("validation/runner.py"))
    except SyntaxError as exc:
        raise Problem("contract_drift", "canonical runner declarations cannot be parsed") from exc
    declared_steps, node_version = set(), None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "NODE_VERSION" for target in node.targets):
            require(isinstance(node.value, ast.Constant) and type(node.value.value) is str
                    and re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", node.value.value) is not None,
                    "unrecognized canonical Node version declaration")
            node_version = node.value.value
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "Step" and node.args:
            if isinstance(node.args[0], ast.Constant) and type(node.args[0].value) is str:
                declared_steps.add(node.args[0].value)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "Step" and len(node.args) >= 4:
            try:
                values = ast.literal_eval(node.args[3])
            except (ValueError, TypeError) as exc:
                raise Problem("contract_drift", "runner dependency declaration changed") from exc
            require(type(values) in (tuple, list), "unexpected runner dependency list")
            for value in values:
                match = re.fullmatch(pattern, value) if type(value) is str else None
                require(match is not None, "runner uses unsupported dependency declarations")
                add(*match.groups())
    require(bool(pins), "no exact dependency pins found")
    require("pixel-web-ui" not in declared_steps or node_version is not None, "Node test gate lacks an exact reviewed version declaration")
    return {"pins": pins, "requirement_files": sorted(visited), "declared_steps": sorted(declared_steps),
            "node_version": node_version}


def kotlin_cache(reader, cache):
    declarations = strict_json(reader.read("pixel_robot/tools/host_kotlin/dependencies.json"))
    require(type(declarations) is list and 0 < len(declarations) <= 100, "invalid Kotlin dependency manifest")
    cached = Reader(cache)
    hashes = []
    for entry in declarations:
        require(type(entry) is dict and type(entry.get("file")) is str and hash_string(entry.get("sha256")), "invalid Kotlin dependency declaration")
        name = entry["file"]
        require(len(Path(name).parts) == 1 and name.endswith((".jar", ".aar")), "invalid Kotlin artifact path")
        require(sha(cached.read(name)) == entry["sha256"], "Kotlin dependency byte hash mismatch: " + name)
        hashes.append({"file": name, "sha256": entry["sha256"]})
        if name.endswith(".aar"):
            classes = Path(name).stem + "-classes.jar"
            require(hash_string(entry.get("classes_sha256")), "missing AAR classes digest")
            require(sha(cached.read(classes)) == entry["classes_sha256"], "Kotlin AAR classes hash mismatch")
            hashes.append({"file": classes, "sha256": entry["classes_sha256"]})
    return {"artifacts": hashes, "compiled_classes_trusted": False,
            "note": "Only pinned dependency bytes checked. Canonical runner must fresh-compile production Kotlin and run tests."}


def package_inventory(names=None):
    values = {}
    for name in (*PINS, "pyserial") if names is None else names:
        try:
            values[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            values[name] = None
    return values


def dependency_check(packages, pins=None):
    issues = []
    pins = PINS if pins is None else pins
    for name, expected in pins.items():
        actual = packages.get(name)
        # CPU wheels report the local +cpu tag; arbitrary tags are not accepted.
        allowed = [expected, expected + "+cpu"] if name == "torch" else [expected]
        if actual not in allowed:
            issues.append({"package": name, "required": allowed, "installed": actual})
    return {"packages": packages, "issues": issues, "imports_executed": False,
            "required_pins": pins,
            "note": "Metadata availability in this interpreter only; canonical validation supports separate interpreters and checks actual imports/execution. Pyserial is not required for fake-transport tests."}


def consume_validation(path, repo_reader):
    """Read public runner reports; never run/reimplement its validation plan."""
    evidence = Reader(path)
    latest, loop = evidence.json("latest.json"), evidence.json("loop.json")
    require(type(latest.get("schema_version")) is int and latest["schema_version"] == 1 and loop.get("schema_version") == 1, "unsupported validation report schema")
    require(latest.get("status") in ("PASS", "FAIL", "BLOCKED", "TIMEOUT") and loop.get("status") in ("PASS", "FAIL", "BLOCKED", "TIMEOUT"), "invalid validation status")
    cycle = latest.get("cycle")
    require(type(cycle) is int and 1 <= cycle <= 999999, "invalid validation cycle")
    cycle_prefix = f"cycle-{cycle:03d}/"
    require(evidence.json(cycle_prefix + "report.json") == latest, "latest report differs from its cycle report")
    require(type(loop.get("cycles")) is list and loop["cycles"] and loop["cycles"][-1].get("cycle") == cycle, "loop does not name latest cycle")
    manifest = evidence.json(cycle_prefix + "source-before.json")
    require(type(manifest.get("files")) is list and 0 < len(manifest["files"]) <= 100000, "invalid source manifest")
    identity = {key: manifest[key] for key in ("head", "base", "files")}
    require(digest(identity) == manifest.get("sha256") == latest.get("source_before", {}).get("sha256"), "source manifest digest mismatch")
    changed = []
    for item in manifest["files"]:
        require(type(item) is dict and type(item.get("path")) is str, "invalid source manifest item")
        name = item["path"]
        if item.get("kind") == "file":
            try:
                if sha(repo_reader.read(name)) != item.get("sha256"):
                    changed.append(name)
            except Problem:
                changed.append(name)
        elif item.get("kind") == "deleted":
            if repo_reader.path(name).exists():
                changed.append(name)
        else:
            changed.append(name)  # Symlink comparison deliberately unsupported.
    require(type(latest.get("plan")) is list and digest(latest["plan"]) == latest.get("plan_sha256"), "validation plan digest mismatch")
    require(loop["cycles"][-1].get("status") == latest["status"], "loop/cycle recorded statuses disagree")
    if latest["status"] == "PASS":
        rows = latest.get("steps")
        require(type(rows) is list and len(rows) > 0 and all(type(row) is dict for row in rows), "missing validation steps")
        require(all(row.get("status") == "PASS" for row in rows if row.get("required") is True), "PASS contradicts a required step")
        names = {row.get("name") for row in rows if row.get("status") == "PASS" and row.get("required") is True}
        require(all(type(row) is dict and row.get("name") in names for row in latest["plan"]), "planned check absent from PASS steps")
        require({"source-stability", "dependency-stability"} <= names, "missing stability evidence")
        require(latest.get("source_after", {}).get("sha256") == manifest["sha256"], "source before/after identity differs")
        require(latest.get("all_requested_gates_passed") is True, "PASS missing requested-gates claim")
    claim = latest.get("profile") == "offline" and latest.get("selection") == [] and latest.get("partial_selection") is False and latest.get("full_offline_passed") is True
    require(not claim or latest["status"] == "PASS", "contradictory full offline pass claim")
    return {"recorded_cycle_status": latest["status"], "recorded_loop_status": loop["status"],
            "recorded_full_offline_pass": claim and loop["status"] == "PASS",
            "listed_source_files_changed": changed, "evidence_sha256": evidence.inventory,
            "fresh_acceptance": False,
            "limits": "Rehashes listed source only. New/unlisted files, Git identity, current dependency bytes and test truth are not revalidated. Rerun python -m validation for current acceptance."}


def audit_guard(event, args):
    """Defense in depth for this CLI; not a sandbox for hostile native extensions."""
    if event == "open":
        mode, flags = args[1], args[2]
        if (isinstance(mode, str) and any(c in mode for c in "wax+")) or (isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)):
            raise PermissionError("preflight forbids filesystem writes")
    if event.startswith(("socket.", "subprocess.", "os.exec", "os.spawn", "os.posix_spawn", "os.fork")) or event in {
        "os.system", "os.chdir", "os.remove", "os.rename", "os.rmdir", "os.mkdir", "os.link", "os.symlink", "os.chmod", "os.chown", "os.truncate", "os.utime", "os.putenv", "os.unsetenv"}:
        raise PermissionError("preflight forbids processes, network and environment/filesystem mutation")


def inspect(root, *, packages=None, tools=None, android_sdk=None, validation_report=None, kotlin_cache_dir=None, node_executable=None, scope="all"):
    reader = Reader(root)
    checks = []
    def add(name, stage, status, detail, data=None, action=None):
        checks.append({"id": name, "stage": stage, "status": status, "detail": detail,
                       "data": data or {}, "next_action": action})
    def run(name, stage, fn, detail, action):
        try:
            data = fn()
            add(name, stage, "PASS", detail, data, action)
        except (Problem, KeyError, TypeError, IndexError, AttributeError, OverflowError, RecursionError) as exc:
            code = exc.code if isinstance(exc, Problem) else "invalid_contract"
            message = exc.detail if isinstance(exc, Problem) else "malformed nested contract structure"
            status = "BLOCKED" if code in {"missing_file", "unreadable_file", "unsafe_path", "changed_during_read", "contract_drift"} else "FAIL"
            add(name, stage, status, message, {"reason_code": code}, action)
    run("repository", "offline_setup", lambda: {"guide_sha256": sha(reader.read("AGENTS.md")),
        "runner_sha256": sha(reader.read("validation/runner.py")), "requirements_sha256": sha(reader.read("validation/requirements.txt"))},
        "Repository instructions and canonical offline runner are present", "Use the exact repository root; do not copy files between unrelated checkouts.")
    add("python", "offline_setup", "PASS" if sys.version_info[:2] == (3, 12) else "BLOCKED",
        "Python 3.12 is the tested setup baseline", {"version": list(sys.version_info[:3]), "executable": sys.executable}, "Select an existing Python 3.12 environment; setup is operator-run.")
    run("canonical_dependency_pins", "offline_setup", lambda: canonical_pins(reader), "Dependency pins read from local canonical requirements and runner declarations", "Review conflicting/unsupported requirement declarations; preflight never imports the runner.")
    canonical = checks[-1]["data"]
    pins = canonical.get("pins", PINS)
    dependencies = dependency_check(package_inventory((*pins, "pyserial")) if packages is None else packages, pins)
    add("offline_dependencies", "offline_setup", "BLOCKED" if dependencies["issues"] else "PASS", "Direct dependency metadata compared with tested pins", dependencies, "See README.md setup commands; no packages are installed by preflight.")
    resolved = {name: shutil.which(name) for name in ("git", "bash", "java", "node", "adb", "pio", "pwsh")} if tools is None else tools
    if node_executable is not None:
        resolved = {**resolved, "node": shutil.which(str(node_executable))}
    for name in ("git", "bash", "java"):
        add(name, "offline_setup", "PASS" if resolved.get(name) else "BLOCKED", f"{name} PATH availability only; not executed", {"path": resolved.get(name)}, f"Make an existing {name} executable available on PATH.")
    if "pixel-web-ui" in canonical.get("declared_steps", []):
        add("node", "offline_setup", "PASS" if resolved.get("node") else "BLOCKED",
            "Node executable availability only; version and browser-harness execution belong to canonical validation",
            {"path": resolved.get("node"), "required_version": canonical.get("node_version"), "version_verified": False,
             "executed": False},
            "Select an already installed Node matching validation/runner.py NODE_VERSION; pass --node PATH to both tools if needed. No Node or npm process is started by preflight.")
    run("kotlin_dependency_cache", "offline_setup",
        lambda: kotlin_cache(reader, kotlin_cache_dir or (reader.root / "pixel_robot/.tooling/host-kotlin")),
        "Pinned Kotlin/JVM dependency bytes are present; Java version and actual compilation remain untested",
        "After authorized network setup: python pixel_robot/tools/run_host_kotlin_tests.py --download-only. Java 17+ must already be installed.")
    def sources():
        manifest = Reader(Path(__file__).parent).json("supported_sources.json")
        changed = []
        for name, expected in manifest.items():
            raw = reader.read(name)
            observed = source_contract_digest(raw, expected.get("symbols"))
            if observed != expected["sha256"]:
                changed.append(name)
        if changed:
            raise Problem("contract_drift", "reviewed static-check assumptions changed: " + ", ".join(changed))
        return {"matched_files": len(manifest), "comparison": "Selected Python contract definitions use AST; other source normalizes CRLF/LF",
                "scope": "Review freshness for legacy_bundle, pixel_bundle and shadow capability diagnostics only; never affects offline_setup"}
    run("reviewed_contract_sources", "contracts", sources, "Static diagnostics match the reviewed production-loader sources", "Review changed source and checker assumptions together; never refresh baseline hashes just to clear the gate.")
    run("legacy_bundle", "contracts", lambda: legacy_bundle(reader), "Legacy bundle byte/header/calibration consistency only", "Keep the 180-input assembly-1 legacy bundle separate from Pixel V22; this is not a hardware check.")
    run("pixel_bundle", "contracts", lambda: pixel_bundle(reader), "Pixel V22 byte/identity/reference/calibration consistency only", "Run the Android production loader/parity tests separately; no ONNX session was created.")
    run("pixel_shadow_capabilities", "contracts", lambda: capability_contract(reader, ASSETS + "policy_metadata.json"), "Pixel schema-2 metadata is structurally usable for shadow interpretation", "Manual shadow operation still requires explicit --allow-shadow and --allow-unqualified-metadata.")
    run("synthetic_shadow_capabilities", "contracts", lambda: capability_contract(reader, "robot_llm/data/capabilities.json"), "Synthetic fixture capabilities are structurally readable", "Do not substitute synthetic fixture hashes/limits for the physical bundle.")
    for profile, path in (("assembly-1-12dof", LEGACY), ("assembly-four-leg-linkage-12dof", ASSETS)):
        def profile_hash(profile=profile, path=path):
            metadata = reader.json(path + "policy_metadata.json")
            actual = sha(reader.read("control_center/profiles/" + profile + ".json"))
            return {"current_profile_sha256": actual, "bundle_export_profile_sha256": metadata.get("profile_sha256"),
                    "matches_export_bytes": actual.lower() == str(metadata.get("profile_sha256")).lower()}
        run("profile_snapshot_" + profile, "provenance", profile_hash, "Current profile compared to the bundle's declared export snapshot", "A historical profile hash mismatch needs exact export provenance; do not rewrite metadata to match today's profile.")
        if checks[-1]["status"] == "PASS" and not checks[-1]["data"]["matches_export_bytes"]:
            checks[-1]["status"] = "WARN"
            checks[-1]["detail"] = "Current profile bytes differ from the bundle's historical export hash; original export snapshot not verified"
    def source_inventory():
        gradle = reader.text("pixel_robot/app/build.gradle.kts")
        config = reader.text("robot_dog/firmware/robot-dog-control/platformio.ini")
        firmware = reader.text("robot_dog/firmware/robot-dog-control/src/main.cpp")
        usb = reader.text("pixel_robot/app/src/main/java/com/leo/pixelrobot/robot/UsbRobotTransport.kt")
        legacy = reader.text(LEGACY + "run_policy.py")
        def match(pattern, text):
            result = re.search(pattern, text)
            require(result is not None, "source constant format changed")
            return result.group(1)
        return {"pixel_version": match(r'versionName\s*=\s*"([^"]+)"', gradle),
                "compile_sdk": int(match(r"compileSdk\s*=\s*(\d+)", gradle)),
                "java_source_target": int(match(r"sourceCompatibility\s*=\s*JavaVersion.VERSION_(\d+)", gradle)),
                "java_bytecode_target": int(match(r"targetCompatibility\s*=\s*JavaVersion.VERSION_(\d+)", gradle)),
                "onnx_runtime_android": match(r'onnxruntime-android:([^"\s]+)', gradle),
                "firmware_build_label": match(r'ROBOT_DOG_VERSION=\\"([^\\"]+)', config),
                "firmware_usb_baud": int(match(r"Serial.begin\((\d+)\)", firmware)),
                "pixel_usb_baud": int(match(r"const val BAUD = ([\d_]+)", usb).replace("_", "")),
                "legacy_usb_baud": int(match(r"SERIAL_BAUD = (\d+)", legacy)),
                "platformio_platform_unpinned": bool(re.search(r"^platform\s*=\s*espressif32\s*$", config, re.M))}
    run("source_toolchain_and_transport", "contracts", source_inventory, "Build labels and USB baud rates read from source; no connected versions queried", "Pixel is the current path. Old README numbers are historical; do not run the legacy host against a different firmware baud.")
    info = checks[-1]["data"] if checks[-1]["status"] == "PASS" else {}
    if info and info["pixel_usb_baud"] != info["firmware_usb_baud"]:
        checks[-1].update(status="FAIL", detail="Pixel/firmware source USB baud mismatch")
    if info and info["legacy_usb_baud"] != info["firmware_usb_baud"]:
        add("legacy_transport_mismatch", "provenance", "WARN", "Legacy host USB baud differs from current firmware source", info,
            "Do not connect the legacy host as a substitute for Pixel; verify the separately selected stack first.")
    def android_build_files():
        wrapper = reader.text("pixel_robot/gradle/wrapper/gradle-wrapper.properties")
        gradle = reader.text("pixel_robot/build.gradle.kts")
        distribution = re.search(r"gradle-([0-9.]+)-bin\.zip", wrapper)
        plugin = re.search(r'id\("com\.android\.application"\) version "([0-9.]+)"', gradle)
        require(distribution is not None and plugin is not None, "unrecognized Gradle/Android plugin declaration")
        return {"wrapper_jar_sha256": sha(reader.read("pixel_robot/gradle/wrapper/gradle-wrapper.jar")),
                "wrapper_script_sha256": sha(reader.read("pixel_robot/gradlew")),
                "gradle_distribution_version": distribution.group(1), "android_plugin_version": plugin.group(1),
                "executed": False}
    run("android_build_files", "android", android_build_files, "Gradle wrapper/build declarations present; no wrapper execution or downloads", "Use the Android build-only commands after toolchain setup authorization.")
    add("android_tools", "android", "INFO", "Tool presence only; no Java/Gradle/ADB command or daemon started",
        {"java": resolved.get("java"), "adb": resolved.get("adb"), "sdk_selected": bool(android_sdk)}, "Select an SDK explicitly with --android-sdk PATH or ANDROID_HOME; build only after setup authorization.")
    if android_sdk:
        sdk = Reader(android_sdk)
        run("android_sdk_platform", "android", lambda: {"android_jar_sha256": sha(sdk.read(f"platforms/android-{info.get('compile_sdk', 37)}/android.jar"))}, "Selected compile SDK platform jar is present; licenses/build usability unverified", "Install missing SDK components yourself through the supported Android SDK tools after reviewing licenses.")
    else:
        add("android_sdk_platform", "android", "BLOCKED", "No Android SDK root selected", action="Pass --android-sdk PATH; preflight never installs SDKs or accepts licenses.")
    add("android_sdk_license_review", "android", "BLOCKED",
        "SDK license acceptance and setup authorization are not established by this read-only inventory",
        {"acceptance_verified": False, "accepted_by_preflight": False},
        "Keep pending SDK/license permission blocked. Review the exact license and obtain operator approval before acceptance or installation; existing platform files are not proof of authorization.")
    add("firmware_tools", "firmware", "INFO", "PlatformIO PATH inventory only", {"pio": resolved.get("pio"), "platform_version_pinned": False}, "Use pio run -e esp32dev only after approved build/dependency setup; never upload as a setup probe.")
    if validation_report:
        run("recorded_validation", "offline_evidence", lambda: consume_validation(validation_report, reader), "Existing canonical reports inspected, not freshly accepted", "Run python -m validation for a source/dependency-bound current result.")
        row = checks[-1]
        if row["status"] == "PASS":
            data = row["data"]
            row["status"] = "INFO" if data["recorded_full_offline_pass"] and not data["listed_source_files_changed"] else "BLOCKED"
    else:
        add("recorded_validation", "offline_evidence", "BLOCKED", "No existing validation report directory supplied", action="Run python -m validation; optionally inspect its output with --validation-report DIR.")
    for stage, detail, action in (
        ("android", "No build, installed APK identity, production ONNX parity or on-device lifecycle result verified", "Use the build-only runbook first; authorize device installation/testing separately."),
        ("firmware", "No reproducible firmware build, flashed identity or motor-disabled transport test verified", "Preserve the installed firmware; verify exact board/build/configuration and watchdog evidence before any authorized flash or test."),
        ("model", "No retained live model evidence qualified; no endpoint contacted", "Obtain explicit endpoint/data/resource authorization, collect completions, then use validation --profile live-model with reviewed inputs."),
        ("isaac", "No GB10/Isaac physics or checkpoint acceptance evidence verified", "Use separately authorized exact-checkpoint simulator evaluation; inspect existing workloads first."),
        ("hardware", "No current robot calibration, power, servo config, transport, stopping or physical trial verified", "Leo must independently verify the actual robot and authorize any physical operation. Saved true flags and historical notes are not new evidence.")):
        add(stage + "_execution_evidence", stage, "BLOCKED", detail, action=action)
    offline = [row for row in checks if row["stage"] == "offline_setup"]
    setup_status = "FAIL" if any(r["status"] == "FAIL" for r in offline) else "BLOCKED" if any(r["status"] == "BLOCKED" for r in offline) else "PASS"
    selected = offline if scope == "offline-setup" else checks
    status = "FAIL" if any(r["status"] == "FAIL" for r in selected) else "BLOCKED" if any(r["status"] == "BLOCKED" for r in selected) else "PASS"
    stages = {}
    for stage in sorted({row["stage"] for row in checks}):
        rows = [row for row in checks if row["stage"] == stage]
        statuses = {row["status"] for row in rows}
        stages[stage] = "FAIL" if "FAIL" in statuses else "BLOCKED" if "BLOCKED" in statuses else "WARN" if "WARN" in statuses else "PASS" if statuses == {"PASS"} else "INFO"
    return {"schema_version": 1, "tool": "deployment_readiness", "scope": scope, "status": status,
            "stages": stages,
            "offline_setup_status": setup_status, "offline_validation_performed": False,
            "deployment_ready": False, "physical_execution": False, "physical_safety_certified": False,
            "checks": checks, "inspected_files": list(reader.inventory.values()),
            "limits": ["PASS is scoped static consistency or prerequisite inventory, never execution acceptance.",
                       "No project code/tools imported or launched; no packages installed; no hardware/network calls.",
                       "Calibration/promotion/transport fields are unverified file claims; original export provenance may be missing.",
                       "File contents/metadata are read; OS access times may change. This is not a hostile-code or adversarial-filesystem sandbox."]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).absolute().parents[1])
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--scope", choices=("all", "offline-setup"), default="all")
    parser.add_argument("--android-sdk", type=Path, default=os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT"))
    parser.add_argument("--node", type=Path, help="locate an existing Node executable; never execute it, matching validation --node")
    parser.add_argument("--jvm-cache", type=Path, help="existing Kotlin dependency cache, matching validation --jvm-cache")
    parser.add_argument("--validation-report", type=Path, help="existing canonical validation result directory; never executed")
    args = parser.parse_args(argv)
    sys.dont_write_bytecode = True
    sys.addaudithook(audit_guard)
    report = inspect(args.root, android_sdk=args.android_sdk, validation_report=args.validation_report, kotlin_cache_dir=args.jvm_cache, node_executable=args.node, scope=args.scope)
    if args.format == "json":
        print(json.dumps(report, indent=2, sort_keys=True, allow_nan=False))
    else:
        print(f"{report['status']}: {args.scope}; offline setup {report['offline_setup_status']}; deployment remains BLOCKED")
        for row in report["checks"]:
            print(f"{row['status']:7} {row['id']}: {row['detail']}")
            if row.get("next_action") and row["status"] in {"BLOCKED", "FAIL", "WARN"}:
                print("        Next: " + row["next_action"])
        print("Static inventory only. No installation, inference, deployment, device access, arming or motion was performed.")
    return {"PASS": 0, "FAIL": 1, "BLOCKED": 2}[report["status"]]
