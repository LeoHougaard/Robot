"""Open raw Pixel JSONL or a verified Pixel training-capture ZIP."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import io
import json
from pathlib import Path
from pathlib import PurePosixPath
from typing import Iterator, TextIO
import zipfile


MAX_CAPTURE_BYTES = 600 * 1024 * 1024


def _safe_entry(name: str) -> bool:
    normalized = name.replace("\\", "/")
    parts = PurePosixPath(normalized).parts
    return (
        not normalized.startswith("/")
        and ".." not in parts
        and not (parts and ":" in parts[0])
    )


def verify_training_capture(path: Path) -> dict:
    with zipfile.ZipFile(path) as archive:
        listed_names = archive.namelist()
        names = set(listed_names)
        if len(names) != len(listed_names):
            raise ValueError("training capture contains duplicate ZIP entries")
        if "manifest.json" not in names:
            raise ValueError("training capture has no manifest.json")
        if any(not _safe_entry(name) for name in names):
            raise ValueError("training capture contains an unsafe ZIP entry")
        try:
            manifest = json.loads(archive.read("manifest.json"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError(f"invalid training capture manifest: {error}") from error
        if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
            raise ValueError("unsupported training capture manifest")
        files = manifest.get("files")
        if not isinstance(files, list):
            raise ValueError("training capture file manifest is missing")
        declared_total = 0
        for item in files:
            if not isinstance(item, dict) or not isinstance(item.get("path"), str):
                raise ValueError("invalid training capture file record")
            entry = item["path"]
            if entry not in names:
                raise ValueError(f"training capture is missing {entry}")
            declared_size = item.get("size_bytes")
            if not isinstance(declared_size, int) or declared_size < 0:
                raise ValueError(f"invalid size for {entry}")
            declared_total += declared_size
            if declared_total > MAX_CAPTURE_BYTES:
                raise ValueError("training capture exceeds the 600 MiB safety limit")
            digest = hashlib.sha256()
            size = 0
            with archive.open(entry) as stream:
                while chunk := stream.read(64 * 1024):
                    digest.update(chunk)
                    size += len(chunk)
            if size != declared_size:
                raise ValueError(f"size mismatch for {entry}")
            if digest.hexdigest() != item.get("sha256"):
                raise ValueError(f"SHA-256 mismatch for {entry}")
        run_entry = manifest.get("run_entry")
        if not isinstance(run_entry, str) or run_entry not in names:
            raise ValueError("training capture run_entry is missing")
        if not _safe_entry(run_entry):
            raise ValueError("training capture run_entry is unsafe")
        return manifest


@contextmanager
def open_run_text(
    source: Path,
    verified_manifest: dict | None = None,
) -> Iterator[tuple[TextIO, str]]:
    if source.suffix.lower() != ".zip":
        with source.open("r", encoding="utf-8") as stream:
            yield stream, str(source)
        return

    manifest = verified_manifest or verify_training_capture(source)
    run_entry = manifest["run_entry"]
    with zipfile.ZipFile(source) as archive:
        with archive.open(run_entry) as raw:
            with io.TextIOWrapper(raw, encoding="utf-8") as stream:
                yield stream, f"{source}!/{run_entry}"
