#!/usr/bin/env python3
"""Compile and execute production Kotlin host tests without Android SDK or devices.

Network access is opt-in: --download-only prepares pinned Maven Central jars.
The Android/AndroidX/USB API shims deliberately throw. Controller, ONNX,
RobotService and UsbRobotTransport are compile-only; HTTP route tests use
ephemeral loopback sockets and fake callbacks. MainActivity/resources remain
placeholders. Real Android API compatibility, lifecycle and USB are unverified.
Neither an Android build nor device behavior is covered by a host test pass.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import zipfile

PIXEL = Path(__file__).resolve().parents[1]
HOST = PIXEL / "tools" / "host_kotlin"
EXCLUDED = {"MainActivity.kt"}
INSTRUMENTATION_COMPILE_ONLY = (
    "app/src/androidTest/java/com/leo/pixelrobot/policy/Installed3750MonitorTransportTest.kt",
)
SCOPE = "pixel-production-kotlin-host-unit-tests"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def dependencies(cache, download):
    jars = []
    for item in json.loads((HOST / "dependencies.json").read_text()):
        jar = cache / item["file"]
        if not jar.is_file():
            if not download:
                raise RuntimeError(f"Missing {jar.name}; run with --download-only on an authorized network")
            if not shutil.which("curl"):
                raise RuntimeError("curl is needed only for the explicit --download setup")
            cache.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=cache, suffix=".part", delete=False) as output:
                partial = Path(output.name)
            try:
                subprocess.run(["curl", "--fail", "--silent", "--show-error", "--location", "--connect-timeout", "15", "--max-time", "180", item["url"], "--output", str(partial)], check=True)
                if digest(partial) != item["sha256"]:
                    raise RuntimeError(f"Dependency hash mismatch: {jar.name}")
                partial.replace(jar)
            finally:
                partial.unlink(missing_ok=True)
        if digest(jar) != item["sha256"]:
            raise RuntimeError(f"Dependency hash mismatch: {jar.name}; do not use this cache")
        if jar.suffix == ".aar":
            classes = cache / (jar.stem + "-classes.jar")
            if not classes.is_file():
                with zipfile.ZipFile(jar) as archive:
                    payload = archive.read("classes.jar")
                if hashlib.sha256(payload).hexdigest() != item["classes_sha256"]:
                    raise RuntimeError(f"AAR classes hash mismatch: {jar.name}")
                classes.write_bytes(payload)
            if digest(classes) != item["classes_sha256"]:
                raise RuntimeError(f"AAR classes hash mismatch: {jar.name}")
            jars.append(classes)
        else:
            jars.append(jar)
    return jars


def compile_sources(cache, jars, rebuild=False):
    production = sorted(p for p in (PIXEL / "app/src/main/java").rglob("*.kt") if p.name not in EXCLUDED)
    tests = sorted((PIXEL / "app/src/test/java").rglob("*Test.kt"))
    # Explicitly type-check this device harness but never include it in `tests`
    # or the JUnit class list. Its Android boundaries are signature-only shims.
    instrumentation = [PIXEL / name for name in INSTRUMENTATION_COMPILE_ONLY]
    sources = production + tests + instrumentation + sorted(HOST.rglob("*.kt"))
    # Cache identity also binds this compiler's flags/discovery/publication code.
    fingerprint_inputs = sources + [HOST / "dependencies.json", Path(__file__).resolve()]
    def source_fingerprint():
        return hashlib.sha256("\n".join(str(p.relative_to(PIXEL)) + ":" + digest(p) for p in fingerprint_inputs).encode()).hexdigest()
    fingerprint = source_fingerprint()
    compiled = cache / "compiled"
    compiled.mkdir(parents=True, exist_ok=True)
    pointer = compiled / (fingerprint + ".pointer.json")
    build = None
    if not rebuild and pointer.is_file():
        selected = json.loads(pointer.read_text())
        if selected.get("source_fingerprint") != fingerprint:
            raise RuntimeError("Compiled Kotlin cache pointer has a mismatched source identity")
        name = selected.get("directory", "")
        if not isinstance(name, str) or Path(name).name != name or not name.startswith(fingerprint + "-"):
            raise RuntimeError("Invalid compiled Kotlin cache directory")
        build = compiled / name
    classpath = os.pathsep.join(map(str, jars))
    if build is None:
        # Every compiler writes to its own immutable directory. Concurrent cold
        # compiles cannot truncate classes used by another runner/bridge.
        build = Path(tempfile.mkdtemp(prefix=fingerprint + "-", dir=compiled))
        classes = build / "classes"
        classes.mkdir()
        command = ["java", "-Xmx1g", "-cp", classpath, "org.jetbrains.kotlin.cli.jvm.K2JVMCompiler", "-no-stdlib", "-no-reflect", "-jvm-target", "17", "-classpath", classpath, "-d", str(classes), *map(str, sources)]
        result = subprocess.run(command, cwd=PIXEL / "app", capture_output=True, text=True, timeout=180)
        if result.returncode:
            raise ValueError("Kotlin compilation failed:\n" + result.stdout + result.stderr)
        if source_fingerprint() != fingerprint:
            raise RuntimeError("Kotlin sources changed during compilation; rerun after edits finish")
        (build / "classes.sha256.json").write_text(json.dumps({str(p.relative_to(classes)): digest(p) for p in sorted(classes.rglob("*")) if p.is_file()}, sort_keys=True))
        (build / "compiled.ok").write_text(fingerprint + "\n")
        # Publish only a complete verified build. Atomic replacement is safe
        # even when another clean compiler has just published equivalent code.
        with tempfile.NamedTemporaryFile(mode="w", dir=compiled, suffix=".pointer.tmp", delete=False) as output:
            json.dump({"source_fingerprint": fingerprint, "directory": build.name}, output)
            temporary_pointer = Path(output.name)
        temporary_pointer.replace(pointer)
    classes = build / "classes"
    if not classes.is_dir() or (build / "compiled.ok").read_text().strip() != fingerprint:
        raise RuntimeError("Compiled Kotlin cache is incomplete; use --rebuild")
    actual_classes = {str(p.relative_to(classes)): digest(p) for p in sorted(classes.rglob("*")) if p.is_file()}
    if not actual_classes or actual_classes != json.loads((build / "classes.sha256.json").read_text()):
        raise RuntimeError("Compiled Kotlin cache integrity failed; use --rebuild to compile current sources cleanly")
    runtime = os.pathsep.join([str(classes), str(PIXEL / "app/src/test/resources"), classpath])
    return runtime, production, tests, fingerprint


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", type=Path, default=PIXEL / ".tooling/host-kotlin")
    parser.add_argument("--download", action="store_true", help="Explicitly fetch missing pinned dependencies before testing")
    parser.add_argument("--download-only", action="store_true", help="Set up pinned dependencies, without claiming a test pass")
    parser.add_argument("--output", type=Path, help="Write structured result JSON in addition to stdout")
    parser.add_argument("--bridge-command", action="store_true", help="Prepare verified JVM argv without starting the bridge")
    parser.add_argument("--bridge", action="store_true", help="Run compute-only production policy JSONL bridge over stdin/stdout")
    parser.add_argument("--rebuild", action="store_true", help="Force a clean compile for source-bound validation")
    args = parser.parse_args(argv)
    report = {"schema_version": 1, "scope": SCOPE, "status": "blocked", "test_count": 0,
              "hardware_access": False, "android_build_verified": False,
              "excluded_sources": sorted(EXCLUDED),
              "compile_only_android_boundaries": ["AssetManager", "SystemClock", "android_compile/* (throwing Android/AndroidX/USB signatures and MainActivity/resource placeholders)",
                  "android_test_compile/* (throwing instrumentation signatures, never executed)"],
              "compile_only_production_sources": ["PolicyController.kt", "OnnxPolicy.kt", "RobotService.kt", "UsbRobotTransport.kt"],
              "android_api_compatibility_verified": False,
              "android_lifecycle_verified": False,
              "usb_driver_verified": False,
              "main_activity_compiled": False,
              "android_instrumentation_verified": False,
              "compile_only_instrumentation_sources": list(INSTRUMENTATION_COMPILE_ONLY)}
    code = 2
    try:
        jars = dependencies(args.cache_dir.resolve(), args.download or args.download_only)
        if args.download_only:
            report.update(status="setup_complete", dependencies=len(jars))
            code = 0
        else:
            if not shutil.which("java"):
                raise RuntimeError("Java 17+ runtime is required")
            version = subprocess.run(["java", "-version"], capture_output=True, text=True, timeout=10)
            report["java_version"] = (version.stdout + version.stderr).strip()
            major = re.search(r'version "(\d+)', report["java_version"])
            if version.returncode or not major or int(major.group(1)) < 17:
                raise RuntimeError("Java 17+ runtime is required; found " + report["java_version"])
            runtime, production, tests, fingerprint = compile_sources(args.cache_dir.resolve(), jars, args.rebuild)
            report.update(source_fingerprint=fingerprint, compiled_production_sources=[str(p.relative_to(PIXEL)) for p in production])
            bridge_command = [str(Path(shutil.which("java")).resolve()), "-cp", runtime, "com.leo.pixelrobot.host.PolicyFrameBridgeKt", str(PIXEL / "app/src/main/assets")]
            if args.bridge_command:
                report.update(status="bridge_ready", command=bridge_command)
                rendered = json.dumps(report, indent=2) + "\n"
                if args.output:
                    args.output.parent.mkdir(parents=True, exist_ok=True)
                    args.output.write_text(rendered, encoding="utf-8")
                print(rendered, end="")
                return 0
            if args.bridge:
                os.chdir(PIXEL / "app")
                # One process owns stdin/stdout: cancellation must not orphan a
                # Java worker behind the Python launcher.
                os.execv(bridge_command[0], bridge_command)
            names = [str(p.relative_to(PIXEL / "app/src/test/java")).removesuffix(".kt").replace(os.sep, ".") for p in tests]
            names += ["com.leo.pixelrobot.host." + p.stem for p in sorted(HOST.glob("*Test.kt"))]
            report["test_classes"] = sorted(names)
            result = subprocess.run(["java", "-cp", runtime, "com.leo.pixelrobot.host.HostTestRunnerKt", *names], cwd=PIXEL / "app", capture_output=True, text=True, timeout=240)
            if result.stderr:
                report["stderr"] = result.stderr
            try:
                report.update(json.loads(result.stdout))
            except json.JSONDecodeError:
                raise ValueError("Host test process did not return valid JSON: " + result.stdout + result.stderr)
            code = result.returncode
            if report["status"] != "passed" or report["test_count"] <= 0 or report.get("assumption_failures") != 0:
                code = code or 1
    except (RuntimeError, OSError, subprocess.SubprocessError) as error:
        report.update(status="blocked", error=str(error))
    except ValueError as error:
        report.update(status="failed", error=str(error))
        code = 1
    rendered = json.dumps(report, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return code


if __name__ == "__main__":
    sys.exit(main())
