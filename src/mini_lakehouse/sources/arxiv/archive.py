"""Deterministic container for one day's exact OAI response pages."""

import hashlib
import io
import tarfile
from pathlib import Path

import zstandard


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
    digest = hashlib.sha256()
    with destination.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()
