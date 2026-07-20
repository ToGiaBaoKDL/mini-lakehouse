import shutil
from pathlib import Path
from typing import Any, Protocol

import fsspec

from mini_lakehouse.config.settings import StorageSettings


class ObjectStore(Protocol):
    def exists(self, uri: str) -> bool: ...

    def upload(self, source: Path, destination_uri: str) -> None: ...

    def download(self, source_uri: str, destination: Path) -> None: ...


class FsspecObjectStore:
    def __init__(self, settings: StorageSettings) -> None:
        self._settings = settings
        options: dict[str, Any] = {
            "key": settings.secret_value(settings.access_key),
            "secret": settings.secret_value(settings.secret_key),
            "client_kwargs": {
                "endpoint_url": settings.endpoint_url,
                "region_name": settings.region,
            },
            "config_kwargs": {"s3": {"addressing_style": "path"}},
        }
        self._filesystem = fsspec.filesystem(settings.backend, **options)

    def _path(self, uri: str) -> str:
        return self._filesystem._strip_protocol(uri)  # pyright: ignore[reportPrivateUsage]

    def exists(self, uri: str) -> bool:
        return bool(self._filesystem.exists(self._path(uri)))

    def upload(self, source: Path, destination_uri: str) -> None:
        with (
            source.open("rb") as input_file,
            self._filesystem.open(self._path(destination_uri), "wb") as output_file,
        ):
            shutil.copyfileobj(input_file, output_file, length=1024 * 1024)

    def download(self, source_uri: str, destination: Path) -> None:
        with (
            self._filesystem.open(self._path(source_uri), "rb") as input_file,
            destination.open("wb") as output_file,
        ):
            shutil.copyfileobj(input_file, output_file, length=1024 * 1024)


def create_object_store(settings: StorageSettings) -> ObjectStore:
    return FsspecObjectStore(settings)
