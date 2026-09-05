"""Bounded local outbox for immutable stream capture objects."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from uuid import uuid4

from t0_trading.capture.store import CaptureStore, canonical_json, sha256


class SpoolFullError(RuntimeError):
    """The configured spool capacity cannot accept another capture object."""


class CaptureSpool:
    """Atomically stage capture objects and deliver them through the canonical store."""

    def __init__(self, root: Path, *, max_bytes: int) -> None:
        if max_bytes < 1:
            raise ValueError("spool max_bytes must be positive")
        self._root = root
        self._max_bytes = max_bytes
        self._root.mkdir(parents=True, exist_ok=True)
        for temporary in self._root.rglob(".*.tmp"):
            temporary.unlink()
        self._bytes = sum(path.stat().st_size for path in self._entries())
        if self._bytes > self._max_bytes:
            raise SpoolFullError("existing capture spool exceeds its configured capacity")

    @property
    def pending_bytes(self) -> int:
        return self._bytes

    def _entries(self) -> tuple[Path, ...]:
        return tuple(sorted(path for path in self._root.rglob("*") if path.is_file()))

    def _path(self, key: str) -> Path:
        relative = PurePosixPath(key)
        if not relative.parts or relative.is_absolute() or ".." in relative.parts:
            raise ValueError("spool object key must be a safe relative path")
        if not (key.endswith(".json.gz") or key.endswith("/manifest.json")):
            raise ValueError("spool accepts only capture batches and manifests")
        return self._root.joinpath(*relative.parts)

    def _stage(self, key: str, body: bytes) -> str:
        target = self._path(key)
        digest = sha256(body)
        if target.exists():
            if sha256(target.read_bytes()) != digest:
                raise RuntimeError(f"Local capture spool conflict: {key}")
            return digest
        if self._bytes + len(body) > self._max_bytes:
            raise SpoolFullError("capture spool capacity exceeded")

        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
        try:
            with temporary.open("xb") as output:
                output.write(body)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, target)
            directory = os.open(target.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            temporary.unlink(missing_ok=True)
        self._bytes += len(body)
        return digest

    def stage_capture(self, key: str, body: bytes) -> str:
        return self._stage(key, body)

    def stage_json(self, key: str, value: Mapping[str, object]) -> str:
        return self._stage(key, canonical_json(value))

    def drain(self, store: CaptureStore) -> int:
        """Publish every complete pending object in deterministic key order."""
        published = 0
        for path in self._entries():
            key = path.relative_to(self._root).as_posix()
            body = path.read_bytes()
            digest = sha256(body)
            if key.endswith(".json.gz"):
                _, stored_digest = store.put_capture(key, body)
            else:
                value = json.loads(body)
                if not isinstance(value, dict):
                    raise RuntimeError(f"Spool manifest must be an object: {key}")
                _, stored_digest = store.put_json(key, value)
            if stored_digest != digest:
                raise RuntimeError("Capture store returned an unexpected spool checksum")
            path.unlink()
            self._bytes -= len(body)
            parent = path.parent
            while parent != self._root:
                try:
                    parent.rmdir()
                except OSError:
                    break
                parent = parent.parent
            published += 1
        return published
