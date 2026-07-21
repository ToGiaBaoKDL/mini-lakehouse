from io import BytesIO
from pathlib import Path
from typing import cast

from fsspec.spec import AbstractFileSystem

from mini_lakehouse.config.settings import StorageSettings
from mini_lakehouse.storage.object_store import FsspecObjectStore


class _NonClosingBytesIO(BytesIO):
    def close(self) -> None:
        pass


class _Filesystem:
    def __init__(self, *, exists: bool = False) -> None:
        self.object_exists = exists
        self.calls: list[tuple[str, str]] = []
        self.written = _NonClosingBytesIO()

    def open(self, destination: str, mode: str) -> BytesIO:
        self.calls.append((destination, mode))
        if self.object_exists:
            raise FileExistsError(destination)
        return self.written

    def exists(self, _destination: str) -> bool:
        return self.object_exists

    def _strip_protocol(self, uri: str) -> str:
        return uri.removeprefix("s3://")


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
