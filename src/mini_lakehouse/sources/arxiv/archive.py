"""Deterministic container for one day's exact OAI response pages."""

import hashlib
import io
import tarfile
from pathlib import Path

import zstandard


def response_archive_sha256(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def write_response_archive(pages: tuple[bytes, ...], destination: Path) -> str:
    if not pages:
        raise ValueError("An OAI response archive requires at least one page")
    with destination.open("wb") as output:
        compressor = zstandard.ZstdCompressor(level=3)
        with (
            compressor.stream_writer(output, closefd=False) as compressed,
            tarfile.open(fileobj=compressed, mode="w|") as archive,
        ):
            for index, page in enumerate(pages, start=1):
                info = tarfile.TarInfo(name=f"page-{index:06d}.xml")
                info.size = len(page)
                info.mtime = 0
                info.mode = 0o644
                info.uid = 0
                info.gid = 0
                info.uname = ""
                info.gname = ""
                archive.addfile(info, io.BytesIO(page))
    return response_archive_sha256(destination)


def read_response_archive(path: Path) -> tuple[bytes, ...]:
    pages: list[bytes] = []
    with (
        path.open("rb") as source,
        zstandard.ZstdDecompressor().stream_reader(source) as decompressed,
        tarfile.open(fileobj=decompressed, mode="r|") as archive,
    ):
        for index, member in enumerate(archive, start=1):
            expected_name = f"page-{index:06d}.xml"
            if not member.isfile() or member.name != expected_name:
                raise ValueError(
                    f"Invalid OAI response archive member {member.name!r}; "
                    f"expected {expected_name!r}"
                )
            extracted = archive.extractfile(member)
            if extracted is None:
                raise ValueError(f"Cannot read OAI response archive member {member.name!r}")
            pages.append(extracted.read())
    if not pages:
        raise ValueError("An OAI response archive requires at least one page")
    return tuple(pages)
