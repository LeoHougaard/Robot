"""Fixed offline gates with source-bound JSON evidence and bounded reruns.

This module never installs dependencies, edits source, requests language-model inference, discovers
backends, commits, deploys, or operates a robot. See validation/README.md.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
from typing import Any

from . import REPORT_VERSION

ROOT = Path(__file__).resolve().parent.parent
NODE_VERSION = "24.19.0"
STATUSES = {"PASS", "FAIL", "BLOCKED", "TIMEOUT", "SKIPPED"}
EXTERNAL = {
    "live-model": "No live model was measured. Supply an existing completion-inclusive report, holdout corpus, capabilities, and explicit criteria to the read-only live-model profile.",
    "isaac": "Matched Isaac/GPU evaluation remains unrun; use the documented operator-managed backend workflow with workload/identity checks.",
    "android": "Android build/device/USB and runtime integration remain unrun; no Android tools or device calls are made here.",
    "firmware": "Full board/transport/watchdog acceptance remains outstanding; native fake-bus tests and separately recorded ESP32 compilation do not satisfy this gate. No flash or serial calls are made here.",
    "firmware-host": "Native firmware handler/framing/watchdog assertions require an explicit local ArduinoJson include path and host C++ compiler; this is not a board/transport test.",
    "pixel-capture": "Original recorded Pixel walk capture remains unverified; run --profile pixel-capture with the exact dated local capture present. Synthetic host tools tests do not satisfy this gate.",
    "hardware": "Physical robot acceptance requires Leo's explicit operation and measured evidence; this runner cannot initiate motion or certify physical safety.",
}


@dataclass(frozen=True)
class Step:
    name: str
    interpreter: str = "base"
    timeout: float = 120.0
    needs: tuple[str, ...] = ()
    dependencies: tuple[str, ...] = ()


STEPS = (
    Step("runner-unit"), Step("python-syntax"), Step("llm-unit"), Step("qwen-unit"),
    Step("control-unit"), Step("policy-unit"), Step("onshape-unit"),
    Step("pixel-tools-unit"), Step("preflight-unit"),
    Step("policy-runtime-unit", "runtime", 120, ("numpy==2.2.6",)),
    Step("pixel-jvm", "base", 420), Step("pixel-web-ui"),
    Step("pixel-runtime", "runtime", 300, ("numpy==2.2.6", "onnxruntime==1.24.3"), ("pixel-jvm",)),
    Step("fixtures"), Step("offline-guard-probe"), Step("bash-syntax"),
    Step("baml-generate", "baml", 120, ("baml-py==0.226.2", "pydantic==2.11.7")),
    Step("baml-unit", "baml", 120, ("baml-py==0.226.2", "pydantic==2.11.7"), ("baml-generate",)),
    Step("cpu-torch", "torch", 300, ("torch==2.5.1", "pytest==9.1.1")),
    Step("gait-probe", "torch", 120, ("torch==2.5.1",)),
)

LIVE_STEP = Step("live-model", "base", 120)
CAPTURE_STEP = Step("pixel-capture")
FIRMWARE_HOST_STEP = Step("firmware-host", "base", 180)
PIXEL_CAPTURE_PATH = Path("training/diagnostics/pixel-runs-20260829/robot-run-20260829-141035-982-d14ad623.jsonl")


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def file_digest(path):
    hasher = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


def write_json(path, value):
    """Atomic replace keeps watchers from observing a half-written report."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def clean_environment():
    # Do not forward API keys, cloud credentials, PYTHONPATH, proxy settings,
    # BOUNDARY_API_KEY, pytest options/plugins, or shell startup configuration.
    allowed = {"PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP", "TMPDIR",
               "LANG", "LC_ALL", "LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH"}
    env = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    env.update(PYTHONIOENCODING="utf-8", PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1",
               PYTHONNOUSERSITE="1", PYTHONHASHSEED="0", PYTEST_DISABLE_PLUGIN_AUTOLOAD="1",
               CUDA_VISIBLE_DEVICES="", BAML_LOG="off", BAML_LOG_LEVEL="off", DO_NOT_TRACK="1")
    return env


def git_output(root, *args):
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                            timeout=30, env=clean_environment(), check=False)
    if result.returncode:
        raise ValueError("Git source identity could not be read")
    return result.stdout


def source_snapshot(root=ROOT, *, base_ref="HEAD", exclude=()):
    """Hash tracked and nonignored untracked files; never serialize file contents.

    Gitignore excludes venvs, checkpoints, generated BAML, caches and reports.
    Explicit exclusion covers --output directories outside the normal location.
    Source symlinks are rejected: their targets could change outside this manifest.
    """
    root = Path(root).resolve()
    exclusions = [Path(path).resolve() for path in exclude]
    def omitted(path):
        return any(path == item or item in path.parents for item in exclusions)
    head = git_output(root, "rev-parse", "HEAD").decode().strip()
    base = git_output(root, "rev-parse", "--verify", base_ref + "^{commit}").decode().strip()
    names = set(git_output(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard").split(b"\0"))
    files = []
    for raw in sorted(names):
        if not raw: continue
        name = os.fsdecode(raw)
        path = root / name
        if omitted(path.absolute()): continue
        if path.is_symlink():
            raise ValueError("source symlinks require explicit target provenance; unsupported by this snapshot")
        elif path.is_file():
            files.append({"path": name, "kind": "file", "size": path.stat().st_size,
                          "sha256": file_digest(path)})
        elif not path.exists():
            files.append({"path": name, "kind": "deleted"})
        else:
            raise ValueError("unsupported source entry (e.g. a submodule); explicit validation needed")
    identity = {"head": head, "base": base, "files": files}
    return {**identity, "sha256": digest(identity), "file_count": len(files),
            "scope": "Git tracked and nonignored untracked worktree files; generated/ignored files excluded"}


PROBE = r'''import hashlib, importlib.metadata as m, json, platform, sys
packages = sorted({(d.metadata.get('Name', '').lower().replace('_', '-'), d.version) for d in m.distributions()})
print(json.dumps({'version': list(sys.version_info[:3]), 'implementation': platform.python_implementation(),
'executable': sys.executable, 'prefix': sys.prefix, 'packages': dict(packages)}, sort_keys=True))
'''


def resolve_python(value):
    # Preserve the venv entrypoint symlink. Resolving it would leave the venv.
    found = shutil.which(value)
    return str(Path(found or value).absolute())


def probe_python(executable, timeout=15):
    try:
        result = subprocess.run([executable, "-c", PROBE], capture_output=True, text=True,
                                timeout=timeout, env=clean_environment(), check=False)
        if result.returncode:
            return {"status": "BLOCKED", "reason": "interpreter/dependency probe failed", "executable": executable}
        value = json.loads(result.stdout)
        if not isinstance(value, dict) or not isinstance(value.get("packages"), dict):
            raise ValueError("invalid probe")
        value["binary_sha256"] = file_digest(executable)
        value["status"] = "PASS" if value["version"] >= [3, 10, 0] else "BLOCKED"
        if value["status"] != "PASS": value["reason"] = "Python 3.10 or later required"
        value["sha256"] = digest(value)
        return value
    except subprocess.TimeoutExpired:
        return {"status": "TIMEOUT", "reason": "interpreter/dependency probe timed out", "executable": executable}
    except (OSError, ValueError, KeyError, TypeError):
        return {"status": "BLOCKED", "reason": "interpreter missing or invalid", "executable": executable}


def toolchain_snapshot(args):
    """Fingerprint offline native prerequisites without launching Java or tools.

    Compiled class output is intentionally excluded: checks rebuild it. Inputs
    include source-pinned JAR/AAR bytes, Java identity and selected Node identity.
    Node's version is queried with a bounded local subprocess; Java is not launched.
    """
    plan = build_plan(args.profile, args.interpreters, selected=args.steps)
    names = {row["name"] for row in plan}
    result = {}
    if "pixel-web-ui" in names:
        result["node"] = probe_node(args.node)
    if not names & {"pixel-jvm", "pixel-runtime"}:
        return result
    cache = args.jvm_cache or args.root / "pixel_robot/.tooling/host-kotlin"
    manifest = args.root / "pixel_robot/tools/host_kotlin/dependencies.json"
    result.update(cache=str(cache.absolute()), jars={})
    try:
        entries = json.loads(manifest.read_text(encoding="utf-8"))
        if not isinstance(entries, list): raise ValueError("invalid dependency manifest")
        for entry in entries:
            name = entry["file"]
            if type(name) is not str or Path(name).name != name or Path(name).suffix not in {".jar", ".aar"}:
                raise ValueError("invalid jar filename")
            path = cache / name
            result["jars"][name] = {"expected_sha256": entry["sha256"],
                "sha256": file_digest(path) if path.is_file() else None}
            if name.endswith(".aar"):
                classes_name = Path(name).stem + "-classes.jar"
                classes = cache / classes_name
                result["jars"][classes_name] = {"expected_sha256": entry["classes_sha256"],
                    "sha256": file_digest(classes) if classes.is_file() else None}
    except (OSError, ValueError, KeyError, TypeError):
        result["manifest_error"] = "dependency manifest/cache unavailable or invalid"
    java = shutil.which("java")
    result["java"] = {"path": java}
    if java:
        try:
            binary = Path(java).resolve()
            result["java"]["sha256"] = file_digest(binary)
            release = binary.parent.parent / "release"
            if release.is_file(): result["java"]["release_sha256"] = file_digest(release)
        except OSError:
            result["java"]["reason"] = "Java launcher unreadable"
    return result


def probe_node(value):
    """Bind the selected Node binary and version; never install or run npm."""
    path = shutil.which(value)
    result = {"requested": value, "path": path, "required_version": NODE_VERSION, "status": "BLOCKED"}
    if path is None:
        return {**result, "reason": "Node " + NODE_VERSION + " is required for production-script DOM-stub tests"}
    try:
        result["binary_sha256"] = file_digest(path)
        version = subprocess.run([path, "--version"], capture_output=True, text=True,
            timeout=10, env=clean_environment(), check=False)
        result["version"] = version.stdout.strip()
        if version.returncode == 0 and result["version"] == "v" + NODE_VERSION:
            result["status"] = "PASS"
        else:
            result["reason"] = "requires exact tested Node " + NODE_VERSION
    except subprocess.TimeoutExpired:
        result.update(status="TIMEOUT", reason="Node version probe timed out")
    except OSError:
        result["reason"] = "Node launcher unavailable or unreadable"
    return result


def dependency_problem(step, probe):
    if probe.get("status") != "PASS":
        return probe.get("reason", "interpreter unavailable")
    for requirement in step.needs:
        name, expected = requirement.split("==")
        actual = probe.get("packages", {}).get(name)
        # +cpu / vendor local suffixes identify the same pinned public torch release.
        matched = actual == expected or (name == "torch" and actual and actual.split("+")[0] == expected)
        if not matched:
            return f"requires {requirement}; found {actual or 'not installed'}"
    return None


def process_table():
    """Read PID ancestry/start identity only, never command lines or environment."""
    try:
        result = subprocess.run(["ps", "-A", "-o", "pid=", "-o", "ppid=", "-o", "lstart="],
            capture_output=True, text=True, timeout=5, env=clean_environment(), check=False)
        if result.returncode: return {}
        records = {}
        for line in result.stdout.splitlines():
            parts = line.split(None, 2)
            if len(parts) == 3:
                pid, parent = int(parts[0]), int(parts[1])
                if pid <= 0 or parent < 0: continue
                identity = ("ps-lstart", parts[2])
                if sys.platform.startswith("linux"):
                    try:
                        # /proc field22 is starttime in clock ticks. Read current
                        # ancestry with it so PID reuse cannot inherit an older
                        # ps row's parent or creation identity.
                        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
                        parent, identity = int(fields[1]), ("proc-starttime", fields[19])
                    except (OSError, ValueError, IndexError):
                        continue
                records[pid] = (parent, identity)
        return records
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return {}


def stop_owned_descendants(root_pid):
    """Freeze/record live descendants, including children with their own sessions.

    Only ancestry rooted at this exact launched process is eligible. Re-read
    start identity before signalling to avoid targeting a recycled PID. This is
    cleanup for trusted tests, not a sandbox against deliberate daemonization.
    """
    owned = {}
    for _ in range(3):
        records = process_table()
        frontier = [root_pid]
        discovered = {}
        while frontier:
            parent = frontier.pop()
            for pid, record in records.items():
                if record[0] == parent and pid not in discovered and pid != root_pid:
                    discovered[pid] = record
                    frontier.append(pid)
        new = {pid: record for pid, record in discovered.items() if pid not in owned}
        if not new: break
        for pid, record in new.items():
            if process_table().get(pid) == record:
                try:
                    os.kill(pid, signal.SIGSTOP)
                    owned[pid] = record
                except ProcessLookupError:
                    pass
    current = process_table()
    for pid, record in reversed(list(owned.items())):
        # The parent may already have exited; creation identity still binds the
        # observed child, so compare start identity rather than its current PPID.
        if current.get(pid, (None, None))[1] == record[1]:
            try: os.kill(pid, signal.SIGKILL)
            except ProcessLookupError: pass


def kill_process_tree(process):
    # A terminated group leader can leave grandchildren alive. On POSIX the
    # process group remains addressable after its leader has been reaped.
    if os.name == "posix":
        if process.poll() is None:
            try: os.kill(process.pid, signal.SIGSTOP)
            except ProcessLookupError: pass
            stop_owned_descendants(process.pid)
        try: os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError: pass
    elif os.name == "nt" and process.poll() is None:
        # taskkill /T is essential for check subprocesses, not just their parent.
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        if process.poll() is None: process.kill()
    elif process.poll() is None:
        process.kill()
    process.wait()


def cleanup_descendants(process):
    """Fail an otherwise finished POSIX command that leaked owned children."""
    if os.name != "posix":
        return False  # Windows taskkill handles running parents on timeout only.
    try:
        os.killpg(process.pid, 0)
    except ProcessLookupError:
        return False
    kill_process_tree(process)
    return True


def execute_command(command, *, cwd, log, timeout, env=None):
    """No shell interpolation, bounded execution, one combined log per check."""
    started = time.monotonic()
    try:
        with Path(log).open("w", encoding="utf-8") as stream:
            options = {"start_new_session": True} if os.name == "posix" else {}
            if os.name == "nt": options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
            process = subprocess.Popen(list(command), cwd=str(cwd), env=env or clean_environment(),
                stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT, shell=False, **options)
            try:
                code = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                kill_process_tree(process)
                return {"status": "TIMEOUT", "returncode": process.returncode,
                        "reason": "step timeout exceeded", "duration_seconds": time.monotonic() - started}
            except BaseException:
                kill_process_tree(process)
                raise
        leaked = cleanup_descendants(process)
        evidence = {"status": "PASS" if code == 0 and not leaked else "FAIL", "returncode": code,
                    "duration_seconds": time.monotonic() - started,
                    "process_tree_cleanup": "POSIX process group and observed descendants" if os.name == "posix" else "best effort on timeout"}
        if leaked: evidence["reason"] = "check leaked child processes; group terminated"
        return evidence
    except OSError:
        return {"status": "BLOCKED", "returncode": None, "reason": "command could not start",
                "duration_seconds": time.monotonic() - started}


def read_check_result(path, name):
    path = Path(path)
    if not path.is_file() or path.stat().st_size > 4 * 1024 * 1024:
        raise ValueError("missing or oversized structured check result")
    def no_duplicates(pairs):
        result = {}
        for key, value in pairs:
            if key in result: raise ValueError("duplicate result field")
            result[key] = value
        return result
    result = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=no_duplicates,
                        parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite result")))
    if (not isinstance(result, dict) or type(result.get("schema_version")) is not int or result.get("schema_version") != 1 or result.get("check") != name
            or result.get("status") not in {"PASS", "FAIL", "BLOCKED"} or not isinstance(result.get("details"), dict)):
        raise ValueError("invalid structured check result")
    return result


def aggregate(results):
    statuses = [row["status"] for row in results if row.get("required", True)]
    if not statuses: return "BLOCKED"
    for status in ("TIMEOUT", "FAIL", "BLOCKED"):
        if status in statuses: return status
    return "PASS" if all(status == "PASS" for status in statuses) else "BLOCKED"


def build_plan(profile, interpreters, *, selected=(), timeout=None):
    chosen = list(STEPS if profile in ("offline", "all") else
                  (step for step in STEPS if step.interpreter == "base" and step.name not in {"pixel-jvm", "pixel-web-ui"}) if profile == "core" else ())
    if profile in ("live-model", "all"): chosen.append(LIVE_STEP)
    if profile in ("pixel-capture", "all"): chosen.append(CAPTURE_STEP)
    if profile in ("firmware-host", "all"): chosen.append(FIRMWARE_HOST_STEP)
    if selected:
        unknown = set(selected) - {step.name for step in chosen}
        if unknown: raise ValueError("unknown/out-of-profile step: " + ", ".join(sorted(unknown)))
        required = set(selected)
        # Automatically include prerequisite generation, never reuse stale BAML.
        for step in chosen:
            if step.name in required: required.update(step.dependencies)
        chosen = [step for step in chosen if step.name in required]
    return [{**asdict(step), "timeout": timeout or step.timeout,
             "executable": interpreters[step.interpreter]} for step in chosen]


def run_cycle(args, cycle, *, deadline=None):
    destination = args.output / f"cycle-{cycle:03d}"
    destination.mkdir(parents=True, exist_ok=False)
    plan = build_plan(args.profile, args.interpreters, selected=args.steps, timeout=args.step_timeout)
    report: dict[str, Any] = {
        "schema_version": REPORT_VERSION, "cycle": cycle, "started_at": utc_now(),
        "profile": args.profile, "selection": list(args.steps), "partial_selection": bool(args.steps),
        "network_policy": "No external/model/backend/hardware calls; local actor compute and fake-callback loopback HTTP tests are offline; Node uses DOM/fetch stubs; Python socket/DNS operations denied in fixed checks",
        "physical_execution": False, "physical_safety_certified": False,
        "plan": plan, "plan_sha256": digest(plan), "steps": [],
        "outstanding": {key: {"status": "SKIPPED", "reason": reason} for key, reason in EXTERNAL.items()},
    }
    try:
        before = source_snapshot(args.root, base_ref=args.base_ref, exclude=(args.output,))
        write_json(destination / "source-before.json", before)
        report["source_before"] = {key: before[key] for key in ("head", "base", "sha256", "file_count", "scope")}
        report["source_manifest"] = "source-before.json"
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        report["steps"].append({"name": "source-identity", "status": "BLOCKED", "required": True,
                                "reason": "Source/base identity unavailable: " + type(exc).__name__})
        before = None
    input_before = live_input_snapshot(args)
    if args.profile in ("live-model", "all"):
        report["live_inputs_before"] = input_before
    capture_before = capture_snapshot(args)
    if args.profile in ("pixel-capture", "all"):
        report["capture_input_before"] = capture_before
    firmware_before = firmware_snapshot(args)
    if args.profile in ("firmware-host", "all"):
        report["firmware_inputs_before"] = firmware_before
    roles = sorted({row["interpreter"] for row in plan})
    probes = {role: probe_python(args.interpreters[role]) for role in roles}
    report["interpreters"] = probes
    report["toolchains"] = toolchain_snapshot(args)
    results_by_name = {}
    for specification in plan:
        name = specification["name"]
        step = {"live-model": LIVE_STEP, "pixel-capture": CAPTURE_STEP, "firmware-host": FIRMWARE_HOST_STEP}.get(name)
        if step is None: step = next(step for step in STEPS if step.name == name)
        row = {"name": name, "required": True, "status": "BLOCKED"}
        problem = dependency_problem(step, probes[step.interpreter])
        if name == "pixel-web-ui" and report["toolchains"].get("node", {}).get("status") != "PASS":
            problem = report["toolchains"].get("node", {}).get("reason", "Node prerequisite unavailable")
        if name == "live-model" and not all(input_before.get(key, {}).get("sha256") for key in LIVE_INPUTS):
            problem = "requires readable --model-report, --corpus, --capabilities, --criteria; never starts inference"
        if name == "pixel-capture" and not capture_before.get("sha256"):
            problem = "exact retained Pixel capture is absent; synthetic fixtures cannot substitute"
        if name == "firmware-host" and not firmware_before.get("headers_sha256"):
            problem = "requires --firmware-json-include pointing to local ArduinoJson headers; never downloads"
        if name == "firmware-host" and not firmware_before.get("compiler", {}).get("sha256"):
            problem = "host C++ compiler missing or unreadable; no firmware/device action attempted"
        if before is None: problem = "source identity unavailable"
        failed_dependencies = [key for key in step.dependencies if results_by_name.get(key) != "PASS"]
        if failed_dependencies: problem = "prerequisite did not pass: " + ", ".join(failed_dependencies)
        remaining = deadline - time.monotonic() if deadline else None
        if remaining is not None and remaining <= 0:
            row.update(status="TIMEOUT", reason="overall watch deadline exceeded")
        elif problem:
            row["reason"] = problem
        else:
            result_path, artifact = destination / (name + ".json"), destination / (name + "-artifact.json")
            command = [specification["executable"], "-m", "validation.checks", name,
                "--result", str(result_path), "--artifact", str(artifact),
                "--manifest", str(destination / "source-before.json")]
            if name == "live-model":
                for key in LIVE_INPUTS:
                    command.extend(["--" + key.replace("_", "-"), str(getattr(args, key).absolute())])
                command.extend(["--corpus-role", args.corpus_role])
            if name in ("pixel-jvm", "pixel-runtime") and args.jvm_cache is not None:
                command.extend(["--jvm-cache", str(args.jvm_cache.absolute())])
            if name == "pixel-web-ui":
                command.extend(["--node", report["toolchains"]["node"]["path"]])
            if name == "firmware-host":
                command.extend(["--firmware-json-include", str(args.firmware_json_include.absolute()),
                                "--firmware-compiler", args.firmware_compiler])
            row["command"] = command
            row["log"] = name + ".log"
            timeout = min(specification["timeout"], remaining) if remaining is not None else specification["timeout"]
            execution = execute_command(command, cwd=args.root, log=destination / row["log"], timeout=timeout)
            row.update(execution)
            if row["status"] not in {"TIMEOUT", "BLOCKED"}:
                try:
                    checked = read_check_result(result_path, name)
                    expected_code = {"PASS": 0, "FAIL": 1, "BLOCKED": 2}[checked["status"]]
                    if row.get("reason") == "check leaked child processes; group terminated":
                        raise ValueError("leaked check subprocess")
                    if row["returncode"] != expected_code:
                        raise ValueError("process exit/result disagree")
                    row.update(status=checked["status"], details=checked["details"], result=result_path.name)
                except (ValueError, OSError):
                    row.update(status="FAIL", reason="missing, invalid, or contradictory structured result")
            if artifact.is_file():
                row["artifact"] = {"path": artifact.name, "sha256": file_digest(artifact)}
        results_by_name[name] = row["status"]
        if name in report["outstanding"] and row["status"] != "PASS":
            report["outstanding"][name]["status"] = row["status"]
        report["steps"].append(row)
        print(f"{row['status']:7} {name}" + (f": {row['reason']}" if row.get("reason") else ""), flush=True)
    if args.profile in EXTERNAL or args.profile == "all":
        names = EXTERNAL if args.profile == "all" else [args.profile]
        for name in names:
            if name in ("live-model", "pixel-capture", "firmware-host"): continue
            report["steps"].append({"name": name, "status": "BLOCKED", "required": True,
                                    "reason": EXTERNAL[name]})
            report["outstanding"][name]["status"] = "BLOCKED"
    if args.profile in ("live-model", "all"):
        input_after = live_input_snapshot(args)
        report["live_inputs_after"] = input_after
        stable = input_before == input_after
        report["steps"].append({"name": "live-input-stability", "status": "PASS" if stable else "BLOCKED",
            "required": True, "reason": "input bytes unchanged" if stable else "qualification input changed; rerun required"})
        if results_by_name.get("live-model") == "PASS" and stable:
            report["outstanding"].pop("live-model", None)
    if args.profile in ("pixel-capture", "all"):
        capture_after = capture_snapshot(args)
        report["capture_input_after"] = capture_after
        stable = capture_before == capture_after
        report["steps"].append({"name": "capture-input-stability", "status": "PASS" if stable else "BLOCKED",
            "required": True, "reason": "capture bytes unchanged" if stable else "capture changed; rerun required"})
        if results_by_name.get("pixel-capture") == "PASS" and stable:
            report["outstanding"].pop("pixel-capture", None)
    if args.profile in ("firmware-host", "all"):
        firmware_after = firmware_snapshot(args)
        report["firmware_inputs_after"] = firmware_after
        stable = firmware_before == firmware_after
        report["steps"].append({"name": "firmware-input-stability", "status": "PASS" if stable else "BLOCKED",
            "required": True, "reason": "header/compiler bytes unchanged" if stable else "headers/compiler changed; rerun required"})
        if results_by_name.get("firmware-host") == "PASS" and stable:
            report["outstanding"].pop("firmware-host", None)
    after = None
    if before is not None:
        try:
            after = source_snapshot(args.root, base_ref=args.base_ref, exclude=(args.output,))
            write_json(destination / "source-after.json", after)
            report["source_after"] = {key: after[key] for key in ("head", "base", "sha256", "file_count")}
            unchanged = before["sha256"] == after["sha256"]
            report["steps"].append({"name": "source-stability", "status": "PASS" if unchanged else "BLOCKED",
                "required": True, "reason": "source unchanged" if unchanged else "source changed during validation; rerun required"})
        except (OSError, ValueError, subprocess.TimeoutExpired):
            report["steps"].append({"name": "source-stability", "status": "BLOCKED", "required": True,
                                    "reason": "ending source identity unavailable"})
    ending_probes = {role: probe_python(args.interpreters[role]) for role in roles}
    report["ending_interpreters"] = ending_probes
    report["ending_toolchains"] = toolchain_snapshot(args)
    stable = probes == ending_probes and report["toolchains"] == report["ending_toolchains"]
    report["steps"].append({"name": "dependency-stability", "status": "PASS" if stable else "BLOCKED",
        "required": True, "reason": "dependency inventories unchanged" if stable else "interpreter/dependencies changed; rerun required"})
    report["status"] = aggregate(report["steps"])
    report["all_requested_gates_passed"] = report["status"] == "PASS"
    report["full_offline_passed"] = args.profile == "offline" and not args.steps and report["status"] == "PASS"
    report["finished_at"] = utc_now()
    write_json(destination / "report.json", report)
    write_json(args.output / "latest.json", report)
    summary = human_summary(report)
    (destination / "summary.txt").write_text(summary + "\n", encoding="utf-8")
    print(summary, flush=True)
    return report


def human_summary(report):
    lines = [f"{report['status']}: {report['profile']} validation (cycle {report['cycle']})"]
    if report.get("partial_selection"): lines.append("Selected checks only; full offline validation was not requested.")
    lines += [f"  {row['status']:7} {row['name']}" + (": " + row["reason"] if row.get("reason") else "")
              for row in report["steps"]]
    lines.append("No physical safety or deployment readiness is certified. External gates remain outstanding.")
    return "\n".join(lines)


LIVE_INPUTS = ("model_report", "corpus", "capabilities", "criteria")


def live_input_snapshot(args):
    result = {}
    for key in LIVE_INPUTS:
        path = getattr(args, key, None)
        if path is not None:
            try:
                result[key] = {"path": str(path.absolute()), "sha256": file_digest(path)}
            except OSError:
                result[key] = {"path": str(path.absolute()), "reason": "unreadable"}
    return result


def capture_snapshot(args):
    if args.profile not in ("pixel-capture", "all"):
        return {}
    path = args.root / PIXEL_CAPTURE_PATH
    try:
        return {"path": str(PIXEL_CAPTURE_PATH), "sha256": file_digest(path)}
    except OSError:
        return {"path": str(PIXEL_CAPTURE_PATH), "reason": "unreadable or missing"}


def firmware_snapshot(args):
    if args.profile not in ("firmware-host", "all"):
        return {}
    result = {"include": str(args.firmware_json_include) if args.firmware_json_include else None}
    if args.firmware_json_include is not None:
        try:
            include = args.firmware_json_include.resolve()
            if not (include / "ArduinoJson.h").is_file(): raise ValueError("headers absent")
            headers = []
            for path in sorted(include.rglob("*")):
                # rglob lists but does not traverse directory symlinks. Reject
                # those too: the C++ compiler could otherwise follow unbound bytes.
                if path.is_symlink(): raise ValueError("external header symlinks unsupported")
                if path.is_file():
                    headers.append({"path": str(path.relative_to(include)), "sha256": file_digest(path)})
            result.update(headers=headers, headers_sha256=digest(headers))
        except (OSError, ValueError):
            result["headers_error"] = "ArduinoJson header tree unavailable or unsupported"
    compiler = shutil.which(args.firmware_compiler)
    result["compiler"] = {"requested": args.firmware_compiler, "path": compiler}
    if compiler:
        try: result["compiler"]["sha256"] = file_digest(compiler)
        except OSError: result["compiler"]["reason"] = "unreadable"
    return result


def fingerprint(args):
    try:
        source = source_snapshot(args.root, base_ref=args.base_ref, exclude=(args.output,))["sha256"]
    except (OSError, ValueError, subprocess.TimeoutExpired): source = None
    roles = {row["interpreter"] for row in build_plan(args.profile, args.interpreters, selected=args.steps)}
    return digest({"source": source, "live_inputs": live_input_snapshot(args), "capture": capture_snapshot(args),
                   "toolchains": toolchain_snapshot(args), "firmware": firmware_snapshot(args),
                   "dependencies": {key: probe_python(args.interpreters[key]) for key in sorted(roles)}})


def finite_positive(value):
    number = float(value)
    if not math.isfinite(number) or number <= 0: raise argparse.ArgumentTypeError("must be finite and positive")
    return number


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--profile", choices=("offline", "core", "all", *EXTERNAL), default="offline")
    result.add_argument("--model-report", type=Path, help="existing live evaluator report, never an endpoint")
    result.add_argument("--corpus", type=Path, help="corpus used by existing model report")
    result.add_argument("--capabilities", type=Path, help="capabilities used by existing model report")
    result.add_argument("--criteria", type=Path, help="explicit operator-reviewed qualification criteria JSON")
    result.add_argument("--corpus-role", choices=("development", "holdout"), default="development")
    result.add_argument("--python", default=sys.executable, help="base Python entrypoint; preserve venv path")
    result.add_argument("--baml-python", help="pinned BAML Python; defaults to --python")
    result.add_argument("--torch-python", help="pinned CPU Torch/pytest Python; defaults to --python")
    result.add_argument("--runtime-python", help="pinned CPU NumPy/ONNX runtime-test Python; defaults to --python")
    result.add_argument("--jvm-cache", type=Path, help="existing pinned Kotlin/JUnit cache; never downloads")
    result.add_argument("--node", default="node", help="Node " + NODE_VERSION + " for production-script DOM-stub tests; never downloads")
    result.add_argument("--firmware-json-include", type=Path, help="existing ArduinoJson/src for firmware-host profile; never downloads")
    result.add_argument("--firmware-compiler", default="g++", help="host C++ compiler for firmware-host; defaults g++")
    result.add_argument("--output", type=Path, help="new evidence directory (default validation/results/TIMESTAMP)")
    result.add_argument("--base-ref", default="HEAD", help="local Git commit baseline to bind; never fetched")
    result.add_argument("--steps", nargs="+", default=[], help="explicit partial subset; prerequisites included")
    result.add_argument("--step-timeout", type=finite_positive, help="override per-step seconds")
    result.add_argument("--plan", action="store_true", help="print deterministic plan; execute no checks")
    result.add_argument("--watch", action="store_true", help="wait for edits/dependency changes and rerun; never edit or call a model")
    result.add_argument("--max-cycles", type=int, default=10, help="maximum runs including first (watch only)")
    result.add_argument("--watch-timeout", type=finite_positive, default=1800, help="total watch seconds including checks")
    result.add_argument("--interval", type=finite_positive, default=2, help="change polling seconds")
    result.add_argument("--rerun-unchanged", action="store_true", help="retry after interval without waiting for changes (still bounded)")
    return result


def main(argv=None):
    cli = parser()
    args = cli.parse_args(argv)
    if args.max_cycles < 1: cli.error("--max-cycles must be positive")
    if any(getattr(args, key) is not None for key in LIVE_INPUTS) and args.profile not in ("live-model", "all"):
        cli.error("live evidence input flags require --profile live-model or all")
    if args.rerun_unchanged and not args.watch: cli.error("--rerun-unchanged requires --watch")
    args.root = ROOT
    args.interpreters = {"base": resolve_python(args.python),
        "baml": resolve_python(args.baml_python or args.python), "torch": resolve_python(args.torch_python or args.python),
        "runtime": resolve_python(args.runtime_python or args.python)}
    try: plan = build_plan(args.profile, args.interpreters, selected=args.steps, timeout=args.step_timeout)
    except ValueError as exc: cli.error(str(exc))
    if args.plan:
        print(json.dumps({"schema_version": REPORT_VERSION, "profile": args.profile, "steps": plan,
            "plan_sha256": digest(plan), "outstanding": EXTERNAL}, indent=2, sort_keys=True))
        return 0
    args.output = (args.output or ROOT / "validation/results" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")).absolute()
    if args.output.exists() and any(args.output.iterdir()): cli.error("--output must be absent or empty; never overwrite prior evidence")
    args.output.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + args.watch_timeout if args.watch else None
    cycle, history = 0, []
    stop_reason = "run_once"
    try:
        while True:
            previous = fingerprint(args) if args.watch else None
            cycle += 1
            report = run_cycle(args, cycle, deadline=deadline)
            history.append({"cycle": cycle, "status": report["status"], "report": f"cycle-{cycle:03d}/report.json",
                            "source_sha256": report.get("source_before", {}).get("sha256")})
            if report["all_requested_gates_passed"]:
                stop_reason = "all_requested_gates_passed"
                break
            if not args.watch: break
            if cycle >= args.max_cycles:
                stop_reason = "max_cycles_reached"
                break
            print("Waiting for source/dependency changes; fix locally or in Codex. No automatic edits.", flush=True)
            while time.monotonic() < deadline:
                time.sleep(min(args.interval, max(0, deadline - time.monotonic())))
                if args.rerun_unchanged or fingerprint(args) != previous: break
            if time.monotonic() >= deadline:
                stop_reason = "watch_timeout"
                break
    except KeyboardInterrupt:
        stop_reason = "interrupted"
        report = {"status": "BLOCKED", "all_requested_gates_passed": False}
    status = "TIMEOUT" if stop_reason == "watch_timeout" else report["status"]
    write_json(args.output / "loop.json", {"schema_version": REPORT_VERSION, "status": status,
        "stop_reason": stop_reason, "cycles": history, "all_requested_gates_passed": status == "PASS"})
    print(f"Evidence: {args.output}\nStopped: {stop_reason}", flush=True)
    return {"PASS": 0, "FAIL": 1, "BLOCKED": 2, "TIMEOUT": 3}[status]


if __name__ == "__main__":
    raise SystemExit(main())
