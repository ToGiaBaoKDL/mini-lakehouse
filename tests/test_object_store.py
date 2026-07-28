from io import BytesIO
from pathlib import Path
from typing import cast

import pytest
from fsspec.spec import AbstractFileSystem

from mini_lakehouse.config.settings import StorageSettings
from mini_lakehouse.storage.object_store import FsspecObjectStore


class _NonClosingBytesIO(BytesIO):
    def close(self) -> None:
        pass


class _Filesystem:
    def __init__(self, *, exists: bool = False, content: bytes = b"") -> None:
        self.object_exists = exists
        self.content = content
        self.calls: list[tuple[str, str]] = []
        self.written = _NonClosingBytesIO()

    def open(self, destination: str, mode: str) -> BytesIO:
        self.calls.append((destination, mode))
        if mode == "rb":
            return BytesIO(self.content)
        if self.object_exists:
            raise FileExistsError(destination)
        return self.written

    def exists(self, _destination: str) -> bool:
        return self.object_exists


def _store(filesystem: _Filesystem) -> FsspecObjectStore:
    return FsspecObjectStore(
        StorageSettings(),
        filesystem=cast(AbstractFileSystem, filesystem),
    )


def test_raw_upload_uses_atomic_create_instead_of_overwrite(tmp_path: Path) -> None:
    filesystem = _Filesystem()
    store = _store(filesystem)
    archive = tmp_path / "archive.json.gz"
    archive.write_bytes(b"archive")

    created = store.upload_if_absent(archive, "s3://landing/archive.json.gz")

    assert created is True
    assert filesystem.calls == [("landing/archive.json.gz", "xb")]
    assert filesystem.written.getvalue() == b"archive"


def test_concurrent_raw_upload_is_an_idempotent_noop(tmp_path: Path) -> None:
    store = _store(_Filesystem(exists=True))
    archive = tmp_path / "archive.json.gz"
    archive.write_bytes(b"archive")

    assert store.upload_if_absent(archive, "s3://landing/archive.json.gz") is False


def test_bounded_object_read_rejects_oversized_content() -> None:
    store = _store(_Filesystem(content=b"artifact"))

    assert store.read_bytes("s3://curated/artifact", max_bytes=8) == b"artifact"

    with pytest.raises(ValueError, match="read limit"):
        store.read_bytes("s3://curated/artifact", max_bytes=7)


def test_object_checksum_streams_through_the_storage_adapter() -> None:
    store = _store(_Filesystem(content=b"artifact"))

    assert store.sha256("s3://curated/artifact") == (
        "c7c5c1d70c5dec4416ab6158afd0b223ef40c29b1dc1f97ed9428b94d4cadb1c"
    )


def test_object_store_rejects_a_uri_owned_by_another_backend() -> None:
    store = _store(_Filesystem())

    with pytest.raises(ValueError, match="Expected a s3 object URI"):
        store.exists("file:///tmp/artifact")
