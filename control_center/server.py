"""Loopback-only web server for the Robot Training control center."""

from __future__ import annotations

import argparse
import json
import mimetypes
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
import webbrowser

from .model import FIELD_GROUPS, load_profile, profile_hash, save_profile, validate_profile
from training.video_camera import select_video_camera_sample


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = Path(__file__).resolve().parent
STATIC_ROOT = PACKAGE_ROOT / "static"
PROFILES_ROOT = PACKAGE_ROOT / "profiles"
CACHE_ROOT = PACKAGE_ROOT / "cache"
TOKEN_PATH = CACHE_ROOT / "session-token"
REVIEW_INDEX_PATH = CACHE_ROOT / "review-index.json"
DEFAULT_PROFILE = "assembly-1-12dof"
MAX_BODY = 2 * 1024 * 1024
REVIEW_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,96}$")


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


def load_review_videos(path: Path = REVIEW_INDEX_PATH) -> list[dict[str, object]]:
    """Return only manifest entries whose ids and local video files are safe."""
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    entries = payload.get("videos", []) if isinstance(payload, dict) else []
    if not isinstance(entries, list):
        return []
    cache_root = path.parent.resolve()
    reviews: list[dict[str, object]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        review_id = entry.get("id")
        filename = entry.get("file")
        if not isinstance(review_id, str) or not REVIEW_ID_PATTERN.fullmatch(review_id):
            continue
        if not isinstance(filename, str) or Path(filename).name != filename:
            continue
        video_path = (path.parent / filename).resolve()
        try:
            video_path.relative_to(cache_root)
        except ValueError:
            continue
        if video_path.suffix.lower() != ".mp4" or not video_path.is_file():
            continue
        clean = {key: value for key, value in entry.items() if key != "file"}
        clean["video_url"] = f"/api/video/review/{review_id}"
        reviews.append(clean)
    return reviews


class ControlCenter:
    def __init__(self, profile_id: str) -> None:
        self._lock = threading.RLock()
        self.profile_id = profile_id
        self.last_action: dict[str, object] | None = None
        self.video_metadata: dict[str, object] | None = None
        self._status_cache: tuple[float, dict[str, object]] | None = None
        self._last_rendered_sample_index: int | None = None
        self._last_presented_sample_index: int | None = None
        self._review_sample_cache: dict[int, dict[str, str]] = {}
        self._review_render_queue: list[int] = []
        self._review_presentation_queue: list[int] = []
        for archive in CACHE_ROOT.glob("current-v4-review-sample-*.mp4"):
            match = re.fullmatch(r"current-v4-review-sample-([0-4])\.mp4", archive.name)
            if match and archive.is_file() and archive.stat().st_size > 0:
                index = int(match.group(1))
                self._review_sample_cache[index] = {
                    "path": str(archive),
                    "source": f"cached-review://current-v4/sample-{index}",
                }

    def _next_review_sample_index(self) -> int:
        """Render all five randomized robot samples before repeating one."""

        missing = [
            index for index in range(5) if index not in self._review_sample_cache
        ]
        if missing:
            return secrets.choice(missing)
        if not self._review_render_queue:
            remaining = list(range(5))
            choices = [
                index
                for index in remaining
                if index != self._last_rendered_sample_index
            ] or remaining
            while remaining:
                index = secrets.choice(choices)
                self._review_render_queue.append(index)
                remaining.remove(index)
                choices = remaining
        return self._review_render_queue.pop(0)

    def _next_cached_review_sample_index(self) -> int:
        """Return every cached robot once per randomized presentation cycle."""

        available = set(self._review_sample_cache)
        self._review_presentation_queue = [
            index
            for index in self._review_presentation_queue
            if index in available
        ]
        if not self._review_presentation_queue:
            remaining = list(available)
            choices = [
                index
                for index in remaining
                if index != self._last_presented_sample_index
            ] or remaining
            while remaining:
                index = secrets.choice(choices)
                self._review_presentation_queue.append(index)
                remaining.remove(index)
                choices = remaining
        return self._review_presentation_queue.pop(0)

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
                        "id": profile["profile_id"],
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
            "profile_hash": profile_hash(profile),
            "profiles": self.list_profiles(),
            "groups": FIELD_GROUPS,
            "validation": validate_profile(profile),
            "launch_validation": validate_profile(profile, for_launch=True),
            "last_action": self.last_action,
            "video_metadata": self.video_metadata,
            "review_videos": load_review_videos(),
        }

    def save(self, profile: object) -> dict[str, object]:
        if not isinstance(profile, dict):
            raise ValueError("Profile must be a JSON object.")
        if profile.get("profile_id") != self.profile_id:
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
            rendered_sample_index: int | None = None
            presented_cached_review = False
            status = self.status(force=True)
            status_fields = status.get("fields", {})
            if not isinstance(status_fields, dict):
                status_fields = {}
            outputs = str(status_fields.get("outputs", ""))
            experiment_match = re.search(
                r"(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})$", outputs
            )
            expected_experiment = (
                experiment_match.group(1) if experiment_match else ""
            )
            profile = load_profile(self.profile_path(self.profile_id))
            requested_sample_index = self._next_review_sample_index()
            if status_fields.get("training") != "running":
                # Kit can retain unusable camera/runtime state after a
                # completed headless recording. Each explicit stopped-state
                # fetch gets a clean workload container before it renders. A
                # rare Kit startup can stall before frame one, so retry only
                # that same requested sample after another clean restart.
                render_result: dict[str, object] = {
                    "ok": False,
                    "exit_code": -1,
                    "output": "The rollout renderer did not start.",
                }
                for _attempt in range(3):
                    render_result = self._run_script(
                        "Stop-IsaacLab.ps1", timeout=120
                    )
                    if not render_result["ok"]:
                        continue
                    render_result = self._run_script(
                        "Start-IsaacLab.ps1", timeout=240
                    )
                    if not render_result["ok"]:
                        continue
                    render_result = self._run_script(
                        "Render-SimpleDogTrainingVideo.ps1",
                        [
                            "-VideoLength",
                            str(profile["training"]["video_length"]),
                            "-ValidationSample",
                            str(requested_sample_index),
                        ],
                        timeout=720,
                    )
                    if render_result["ok"]:
                        break
                if render_result["ok"]:
                    sample_match = re.search(
                        r"Validation sample:\s*([1-5])/5",
                        str(render_result["output"]),
                    )
                    if sample_match:
                        rendered_sample_index = int(sample_match.group(1)) - 1
                        self._last_rendered_sample_index = rendered_sample_index
                    result = self._run_script(
                        "Get-SimpleDogTrainingVideo.ps1",
                        ["-Destination", str(destination)],
                        timeout=120,
                    )
                else:
                    result = render_result
            else:
                # Render the newest retained checkpoint in a short-lived
                # Isaac container. The trainer remains active in its original
                # container, so the video belongs to the current run without
                # interrupting scratch optimization.
                render_result = self._run_script(
                    "Render-SimpleDogTrainingVideo.ps1",
                    [
                        "-VideoLength",
                        str(profile["training"]["video_length"]),
                        "-ValidationSample",
                        str(requested_sample_index),
                    ],
                    timeout=720,
                )
                if render_result["ok"]:
                    rendered_sample_index = requested_sample_index
                    self._last_rendered_sample_index = rendered_sample_index
                    video_arguments = ["-Destination", str(destination)]
                    if expected_experiment:
                        video_arguments.extend(
                            ["-ExpectedExperiment", expected_experiment]
                        )
                    result = self._run_script(
                        "Get-SimpleDogTrainingVideo.ps1",
                        video_arguments,
                        timeout=120,
                    )
                elif self._review_sample_cache:
                    rendered_sample_index = self._next_cached_review_sample_index()
                    cached = self._review_sample_cache[rendered_sample_index]
                    shutil.copy2(cached["path"], destination)
                    presented_cached_review = True
                    self._last_presented_sample_index = rendered_sample_index
                    result = {
                        "ok": True,
                        "exit_code": 0,
                        "output": (
                            f"The current run has no renderable checkpoint yet.\n"
                            f"Copied randomized completed review: {destination}\n"
                            f"Source video: {cached['source']}\n"
                            f"Live renderer: {render_result['output']}"
                        ),
                    }
                else:
                    result = render_result
            if result["ok"]:
                result["video_url"] = "/api/video/latest"
                source_match = re.search(
                    r"^Source video:\s*(.+)$",
                    str(result["output"]),
                    flags=re.MULTILINE,
                )
                source = source_match.group(1).strip() if source_match else ""
                source_experiment_match = re.search(
                    r"/(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})/", source
                )
                source_experiment = (
                    source_experiment_match.group(1)
                    if source_experiment_match
                    else "unknown"
                )
                step_match = re.search(r"(?:step|episode)-(\d+)", source)
                step = int(step_match.group(1)) if step_match else 0
                profile = load_profile(self.profile_path(self.profile_id))
                sample = select_video_camera_sample(
                    (
                        rendered_sample_index
                        * int(profile["training"]["video_interval"])
                        if rendered_sample_index is not None
                        else step
                    ),
                    int(profile["training"]["video_interval"]),
                    (
                        5
                        if rendered_sample_index is not None
                        else int(profile["training"]["num_envs"])
                    ),
                )
                self.video_metadata = {
                    "source": source,
                    "experiment": source_experiment,
                    "step": step,
                    "env_index": sample.env_index,
                    "view_index": sample.view_index,
                    "terrain_sample_index": rendered_sample_index,
                    "matches_active_run": (
                        not expected_experiment
                        or source_experiment == expected_experiment
                    ),
                }
                if rendered_sample_index is not None:
                    if not presented_cached_review:
                        archive = CACHE_ROOT / (
                            f"current-v4-review-sample-{rendered_sample_index}.mp4"
                        )
                        if archive.resolve() != destination.resolve():
                            shutil.copy2(destination, archive)
                        self._review_sample_cache[rendered_sample_index] = {
                            "path": str(archive),
                            "source": source,
                        }
                        self._review_presentation_queue.clear()
                    self._last_presented_sample_index = rendered_sample_index
                result["video_metadata"] = self.video_metadata
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

    def _serve_video(self, path: Path) -> None:
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
        if route.path == "/api/video/latest" or route.path.startswith("/api/video/review/"):
            query_token = parse_qs(route.query).get("token", [""])[0]
            if not (
                self._authorized()
                or secrets.compare_digest(query_token, self.token)
            ):
                self.send_error(HTTPStatus.UNAUTHORIZED)
                return
            if route.path == "/api/video/latest":
                video_path = CACHE_ROOT / "latest-training.mp4"
            else:
                review_id = route.path.removeprefix("/api/video/review/")
                review = next(
                    (item for item in load_review_videos() if item.get("id") == review_id),
                    None,
                )
                if review is None:
                    self.send_error(HTTPStatus.NOT_FOUND, "Review video was not found.")
                    return
                manifest = json.loads(REVIEW_INDEX_PATH.read_text(encoding="utf-8"))
                source = next(item for item in manifest["videos"] if item.get("id") == review_id)
                video_path = CACHE_ROOT / str(source["file"])
            self._serve_video(video_path)
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
