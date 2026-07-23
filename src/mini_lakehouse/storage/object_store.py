import hashlib
import shutil
from pathlib import Path
from typing import Any, BinaryIO, Protocol, cast

import fsspec
from fsspec.spec import AbstractFileSystem

from mini_lakehouse.config.settings import StorageSettings


class ObjectStore(Protocol):
    def exists(self, uri: str) -> bool: ...

    def sha256(self, uri: str) -> str: ...

    def upload(self, source: Path, destination_uri: str) -> None: ...

    def upload_if_absent(self, source: Path, destination_uri: str) -> bool: ...

    def download(self, source_uri: str, destination: Path) -> None: ...


class FsspecObjectStore:
    def __init__(
        self,
        settings: StorageSettings,
        filesystem: AbstractFileSystem | None = None,
    ) -> None:
        self._settings = settings
        options: dict[str, Any] = {
            "client_kwargs": {"region_name": settings.region},
            "config_kwargs": {
                "s3": {"addressing_style": "path" if settings.path_style_access else "virtual"}
            },
        }
        if settings.endpoint_url is not None:
            options["client_kwargs"]["endpoint_url"] = settings.endpoint_url
        access_key = settings.secret_value(settings.access_key)
        secret_key = settings.secret_value(settings.secret_key)
        if access_key is not None and secret_key is not None:
            options.update({"key": access_key, "secret": secret_key})
        self._filesystem = filesystem or fsspec.filesystem(settings.backend, **options)

    def _path(self, uri: str) -> str:
        return cast(
            str,
            self._filesystem._strip_protocol(uri),  # pyright: ignore[reportPrivateUsage]
        )

    def exists(self, uri: str) -> bool:
        return bool(self._filesystem.exists(self._path(uri)))

    def sha256(self, uri: str) -> str:
        digest = hashlib.sha256()
        with self._filesystem.open(self._path(uri), "rb") as raw_input_file:
            input_file = cast(BinaryIO, raw_input_file)
            for chunk in iter(lambda: input_file.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def upload(self, source: Path, destination_uri: str) -> None:
        with (
            source.open("rb") as source_file,
            self._filesystem.open(self._path(destination_uri), "wb") as raw_destination_file,
        ):
            destination_file = cast(BinaryIO, raw_destination_file)
            shutil.copyfileobj(source_file, destination_file, length=1024 * 1024)

    def upload_if_absent(self, source: Path, destination_uri: str) -> bool:
        try:
            with (
                source.open("rb") as source_file,
                self._filesystem.open(self._path(destination_uri), "xb") as raw_destination_file,
            ):
                destination_file = cast(BinaryIO, raw_destination_file)
                shutil.copyfileobj(source_file, destination_file, length=1024 * 1024)
        except FileExistsError:
            return False
        except OSError:
            # Some S3-compatible stores translate HTTP 412 to a generic OSError.
            # Treat it as the expected concurrent-create outcome only after the
            # canonical object is visible; unrelated write failures still surface.
            if self.exists(destination_uri):
                return False
            raise
        return True

    def download(self, source_uri: str, destination: Path) -> None:
        with (
            self._filesystem.open(self._path(source_uri), "rb") as raw_input_file,
            destination.open("wb") as output_file,
        ):
            input_file = cast(BinaryIO, raw_input_file)
            shutil.copyfileobj(input_file, output_file, length=1024 * 1024)


def create_object_store(settings: StorageSettings) -> ObjectStore:
    return FsspecObjectStore(settings)
