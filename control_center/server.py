"""Loopback-only web server for the Robot Training control center."""

from __future__ import annotations

import argparse
import json
import mimetypes
from pathlib import Path
import secrets
import subprocess
import sys
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
import webbrowser

from .model import FIELD_GROUPS, load_profile, profile_hash, save_profile, validate_profile


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = Path(__file__).resolve().parent
STATIC_ROOT = PACKAGE_ROOT / "static"
PROFILES_ROOT = PACKAGE_ROOT / "profiles"
CACHE_ROOT = PACKAGE_ROOT / "cache"
TOKEN_PATH = CACHE_ROOT / "session-token"
DEFAULT_PROFILE = "assembly-1-12dof"
MAX_BODY = 2 * 1024 * 1024


def _training_task_name(task: object) -> str:
    """Normalize playback task ids to the training task they visualize."""
    value = str(task or "")
    return value.replace("-Direct-Play-v0", "-Direct-v0").replace(
        "-Direct-Validation-v0", "-Direct-v0"
    )


def video_metadata_matches(metadata: object, fields: dict[str, str]) -> bool:
    if not isinstance(metadata, dict):
        return False
    return (
        _training_task_name(metadata.get("task")) == _training_task_name(fields.get("task"))
        and metadata.get("profile_id") == fields.get("profile")
        and metadata.get("surface") == fields.get("surface")
    )


def load_or_create_session_token(path: Path = TOKEN_PATH) -> str:
    """Keep loopback authentication valid when the local UI server restarts."""
    if path.is_file():
        token = path.read_text(encoding="utf-8").strip()
        if len(token) >= 32 and all(
            char.isalnum() or char in "-_" for char in token
        ):
            return token
    token = secrets.token_urlsafe(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(token + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return token


class ControlCenter:
    def __init__(self, profile_id: str) -> None:
        self._lock = threading.RLock()
        self.profile_id = profile_id
        self.last_action: dict[str, object] | None = None
        self._status_cache: tuple[float, dict[str, object]] | None = None

    @staticmethod
    def profile_path(profile_id: str) -> Path:
        if not profile_id or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for char in profile_id):
            raise ValueError("Invalid profile id.")
        path = (PROFILES_ROOT / f"{profile_id}.json").resolve()
        path.relative_to(PROFILES_ROOT.resolve())
        return path

    def list_profiles(self) -> list[dict[str, object]]:
        result = []
        for path in sorted(PROFILES_ROOT.glob("*.json")):
            try:
                profile = load_profile(path)
                validation = validate_profile(profile, for_launch=True)
                result.append(
                    {
                        # The filename identifies an editable launch profile. Several
                        # curriculum profiles may intentionally share one robot id so
                        # their V2 checkpoints remain in the same compatible namespace.
                        "id": path.stem,
                        "name": profile["display_name"],
                        "joint_count": profile["robot"]["expected_joint_count"],
                        "launch_ready": not validation["errors"],
                    }
                )
            except (OSError, ValueError, json.JSONDecodeError):
                continue
        return result

    def select_profile(self, profile_id: str) -> dict[str, object]:
        path = self.profile_path(profile_id)
        if not path.is_file():
            raise ValueError(f"Profile does not exist: {profile_id}")
        load_profile(path)
        with self._lock:
            self.profile_id = profile_id
        return self.bootstrap()

    def bootstrap(self) -> dict[str, object]:
        with self._lock:
            profile = load_profile(self.profile_path(self.profile_id))
        return {
            "profile": profile,
            "selected_profile_id": self.profile_id,
            "profile_hash": profile_hash(profile),
            "profiles": self.list_profiles(),
            "groups": FIELD_GROUPS,
            "validation": validate_profile(profile),
            "launch_validation": validate_profile(profile, for_launch=True),
            "last_action": self.last_action,
        }

    def save(self, profile: object) -> dict[str, object]:
        if not isinstance(profile, dict):
            raise ValueError("Profile must be a JSON object.")
        current_profile = load_profile(self.profile_path(self.profile_id))
        if profile.get("profile_id") != current_profile.get("profile_id"):
            raise ValueError("The profile id cannot be changed while editing. Clone the file to create a new profile.")
        result = validate_profile(profile)
        if result["errors"]:
            return {"ok": False, "validation": result}
        with self._lock:
            save_profile(self.profile_path(self.profile_id), profile)
        return {
            "ok": True,
            "profile_hash": profile_hash(profile),
            "validation": result,
            "launch_validation": validate_profile(profile, for_launch=True),
        }

    @staticmethod
    def _run_script(name: str, arguments: list[str] | None = None, timeout: int = 180) -> dict[str, object]:
        script = (ROOT / name).resolve()
        script.relative_to(ROOT)
        if not script.is_file():
            raise RuntimeError(f"Required launcher was not found: {name}")
        command = [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            *(arguments or []),
        ]
        completed = subprocess.run(
            command,
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        output = "\n".join(part.strip() for part in (completed.stdout, completed.stderr) if part.strip())
        return {"ok": completed.returncode == 0, "exit_code": completed.returncode, "output": output}

    def status(self, *, force: bool = False) -> dict[str, object]:
        now = time.monotonic()
        with self._lock:
            if not force and self._status_cache and now - self._status_cache[0] < 4:
                return self._status_cache[1]
        try:
            result = self._run_script("Get-SimpleDogTrainingStatus.ps1", timeout=30)
        except (OSError, subprocess.TimeoutExpired, RuntimeError) as exc:
            result = {"ok": False, "exit_code": -1, "output": str(exc)}
        text = str(result["output"])
        parsed: dict[str, str] = {}
        for line in text.splitlines():
            if ":" in line:
                key, value = line.split(":", 1)
                parsed[key.strip().lower().replace(" ", "_")] = value.strip()
        status = {**result, "fields": parsed, "checked_at": time.time()}
        with self._lock:
            self._status_cache = (now, status)
        return status

    def action(self, action: str) -> dict[str, object]:
        if action == "refresh":
            return self.status(force=True)
        if action == "start_training":
            profile = load_profile(self.profile_path(self.profile_id))
            validation = validate_profile(profile, for_launch=True)
            if validation["errors"]:
                return {"ok": False, "validation": validation, "output": "Training was not started because the profile is not launch-ready."}
            result = self._run_script(
                "Start-SimpleDogTraining.ps1",
                ["-ControlProfile", str(self.profile_path(self.profile_id))],
                timeout=240,
            )
        elif action == "stop_training":
            result = self._run_script("Stop-SimpleDogTraining.ps1", timeout=90)
        elif action == "refresh_video":
            CACHE_ROOT.mkdir(parents=True, exist_ok=True)
            destination = CACHE_ROOT / "latest-training.mp4"
            result = self._run_script(
                "Get-SimpleDogTrainingVideo.ps1",
                ["-Destination", str(destination)],
                timeout=120,
            )
            if not result["ok"]:
                profile = load_profile(self.profile_path(self.profile_id))
                render_result = self._run_script(
                    "Render-SimpleDogTrainingVideo.ps1",
                    ["-VideoLength", str(profile["training"]["video_length"])],
                    timeout=600,
                )
                if render_result["ok"]:
                    result = self._run_script(
                        "Get-SimpleDogTrainingVideo.ps1",
                        ["-Destination", str(destination)],
                        timeout=120,
                    )
                else:
                    result = render_result
            if result["ok"]:
                metadata_path = destination.with_suffix(destination.suffix + ".metadata.json")
                try:
                    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as exc:
                    result = {
                        "ok": False,
                        "exit_code": -1,
                        "output": f"Training video metadata could not be read: {exc}",
                    }
                else:
                    fields = self.status(force=True).get("fields", {})
                    if not isinstance(fields, dict) or not video_metadata_matches(metadata, fields):
                        result = {
                            "ok": False,
                            "exit_code": -1,
                            "output": "Newest rollout does not match the current task, robot profile, and surface.",
                        }
                    else:
                        result["video_url"] = "/api/video/latest"
                        result["video_metadata"] = metadata
        else:
            raise ValueError(f"Unknown action: {action}")
        with self._lock:
            self.last_action = {"action": action, "at": time.time(), **result}
            self._status_cache = None
        return result


class Handler(BaseHTTPRequestHandler):
    server_version = "RobotControlCenter/1"

    @property
    def app(self) -> ControlCenter:
        return self.server.app  # type: ignore[attr-defined]

    @property
    def token(self) -> str:
        return self.server.token  # type: ignore[attr-defined]

    def log_message(self, format: str, *args: object) -> None:
        sys.stderr.write("control-center: " + (format % args) + "\n")

    def _json(self, payload: object, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        return secrets.compare_digest(self.headers.get("X-Control-Token", ""), self.token)

    def _read_json(self) -> object:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > MAX_BODY:
            raise ValueError("Request body is empty or too large.")
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def _serve_static(self, relative: str) -> None:
        relative = relative or "index.html"
        path = (STATIC_ROOT / relative).resolve()
        try:
            path.relative_to(STATIC_ROOT.resolve())
        except ValueError:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        if not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        body = path.read_bytes()
        media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", media_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(body)

    def _serve_video(self) -> None:
        path = CACHE_ROOT / "latest-training.mp4"
        if not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND, "No training video has been copied yet.")
            return
        size = path.stat().st_size
        start, end = 0, size - 1
        range_header = self.headers.get("Range", "")
        if range_header.startswith("bytes="):
            bounds = range_header.removeprefix("bytes=").split("-", 1)
            if bounds[0]:
                start = max(0, min(int(bounds[0]), size - 1))
            if len(bounds) > 1 and bounds[1]:
                end = max(start, min(int(bounds[1]), size - 1))
        length = end - start + 1
        self.send_response(HTTPStatus.PARTIAL_CONTENT if range_header else HTTPStatus.OK)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if range_header:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        with path.open("rb") as handle:
            handle.seek(start)
            remaining = length
            while remaining:
                chunk = handle.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    def do_GET(self) -> None:  # noqa: N802
        route = urlparse(self.path)
        if route.path == "/api/video/latest":
            query_token = parse_qs(route.query).get("token", [""])[0]
            if not (
                self._authorized()
                or secrets.compare_digest(query_token, self.token)
            ):
                self.send_error(HTTPStatus.UNAUTHORIZED)
                return
            self._serve_video()
            return
        if route.path.startswith("/api/"):
            if not self._authorized():
                self._json({"error": "Unauthorized local request."}, HTTPStatus.UNAUTHORIZED)
                return
            try:
                if route.path == "/api/bootstrap":
                    self._json(self.app.bootstrap())
                elif route.path == "/api/status":
                    self._json(self.app.status(force=parse_qs(route.query).get("force") == ["1"]))
                else:
                    self._json({"error": "Not found."}, HTTPStatus.NOT_FOUND)
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        self._serve_static(route.path.lstrip("/"))

    def do_POST(self) -> None:  # noqa: N802
        if not self._authorized():
            self._json({"error": "Unauthorized local request."}, HTTPStatus.UNAUTHORIZED)
            return
        try:
            payload = self._read_json()
            if self.path == "/api/profile/save":
                result = self.app.save(payload)
            elif self.path == "/api/profile/select":
                if not isinstance(payload, dict) or not isinstance(payload.get("profile_id"), str):
                    raise ValueError("profile_id is required.")
                result = self.app.select_profile(payload["profile_id"])
            elif self.path == "/api/action":
                if not isinstance(payload, dict) or not isinstance(payload.get("action"), str):
                    raise ValueError("action is required.")
                result = self.app.action(payload["action"])
            else:
                self._json({"error": "Not found."}, HTTPStatus.NOT_FOUND)
                return
            self._json(result)
        except subprocess.TimeoutExpired:
            self._json({"error": "The backend command timed out."}, HTTPStatus.GATEWAY_TIMEOUT)
        except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
            self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--profile", default=DEFAULT_PROFILE)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        raise SystemExit("The control center may bind only to loopback.")

    app = ControlCenter(args.profile)
    app.bootstrap()
    token = load_or_create_session_token()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.app = app  # type: ignore[attr-defined]
    server.token = token  # type: ignore[attr-defined]
    url = f"http://127.0.0.1:{server.server_port}/?token={token}"
    print(f"Robot Control Center: {url}", flush=True)
    print("Press Ctrl+C to stop the local UI server. Training continues independently on the GB10.", flush=True)
    if not args.no_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
