"""Capture one ArXiv OAI datestamp day as an immutable content snapshot."""

import gzip
import hashlib
from datetime import date
from io import BytesIO
from urllib.parse import urlencode
from xml.etree import ElementTree

from lakehouse.contracts.captures import ArxivOaiManifest, ArxivOaiPage, arxiv_snapshot
from loguru import logger

from lakehouse_ingest.http import session
from lakehouse_ingest.storage import S3CaptureStore

OAI_ENDPOINT = "https://oaipmh.arxiv.org/oai"
OAI_NAMESPACE = "http://www.openarchives.org/OAI/2.0/"
RAW_PREFIX = "api/arxiv/raw/oai"


def _text(element: ElementTree.Element | None) -> str | None:
    if element is None or element.text is None:
        return None
    value = element.text.strip()
    return value or None


def _harvest(source_date: date, max_pages: int) -> list[bytes]:
    pages: list[bytes] = []
    token: str | None = None
    seen_tokens: set[str] = set()
    with session() as http:
        for _ in range(max_pages):
            parameters = (
                {
                    "verb": "ListRecords",
                    "metadataPrefix": "arXiv",
                    "from": source_date.isoformat(),
                    "until": source_date.isoformat(),
                }
                if token is None
                else {"verb": "ListRecords", "resumptionToken": token}
            )
            with http.get(f"{OAI_ENDPOINT}?{urlencode(parameters)}", timeout=(10, 180)) as response:
                response.raise_for_status()
                payload = response.content
            root = ElementTree.fromstring(payload)
            errors = root.findall(f"{{{OAI_NAMESPACE}}}error")
            if errors and errors[0].attrib.get("code") != "noRecordsMatch":
                raise RuntimeError(
                    f"ArXiv OAI error {errors[0].attrib.get('code', 'unknown')}: "
                    f"{_text(errors[0]) or ''}"
                )
            pages.append(payload)
            logger.info("Captured ArXiv OAI page {}", len(pages))
            token = _text(
                root.find(f"{{{OAI_NAMESPACE}}}ListRecords/{{{OAI_NAMESPACE}}}resumptionToken")
            )
            if not token:
                return pages
            if token in seen_tokens:
                raise RuntimeError("ArXiv returned a repeated OAI resumption token")
            seen_tokens.add(token)
    raise RuntimeError(f"ArXiv exceeded the {max_pages}-page daily safety limit")


def _terminal_manifest_key(source_date: date) -> str:
    return f"{RAW_PREFIX}/datestamp={source_date.isoformat()}/manifest.json"


def _reuse_capture(store: S3CaptureStore, source_date: date) -> str | None:
    manifest_key = _terminal_manifest_key(source_date)
    body = store.read_manifest(manifest_key)
    if body is None:
        return None
    manifest = ArxivOaiManifest.model_validate_json(body)
    if manifest.source_date != source_date:
        raise RuntimeError("ArXiv terminal manifest does not match its datestamp")
    day_prefix = f"{RAW_PREFIX}/datestamp={source_date.isoformat()}"
    for item in manifest.pages:
        expected_key = f"{day_prefix}/snapshot={manifest.snapshot}/page-{item.page:06d}.xml.gz"
        metadata = store.head(item.key)
        if (
            item.key != expected_key
            or metadata is None
            or metadata.get("ContentLength") != item.size_bytes
            or metadata.get("Metadata", {}).get("sha256") != item.sha256
        ):
            raise RuntimeError(f"Invalid completed ArXiv capture: {item.key}")
    logger.info("Reusing completed ArXiv OAI capture for {}", source_date)
    return store.uri(manifest_key)


def capture_day(
    store: S3CaptureStore,
    source_date: date,
    *,
    max_pages: int = 100,
) -> str:
    existing = _reuse_capture(store, source_date)
    if existing is not None:
        return existing

    pages = [
        gzip.compress(page, compresslevel=6, mtime=0) for page in _harvest(source_date, max_pages)
    ]
    digests = [hashlib.sha256(page).hexdigest() for page in pages]
    snapshot = arxiv_snapshot(digests)
    prefix = f"{RAW_PREFIX}/datestamp={source_date.isoformat()}/snapshot={snapshot}"
    manifest_pages: list[ArxivOaiPage] = []
    for index, (body, digest) in enumerate(zip(pages, digests, strict=True), start=1):
        key = f"{prefix}/page-{index:06d}.xml.gz"
        store.put_file(
            key,
            BytesIO(body),
            size_bytes=len(body),
            sha256=digest,
            content_type="application/xml",
            content_encoding="gzip",
        )
        manifest_pages.append(
            ArxivOaiPage(page=index, key=key, size_bytes=len(body), sha256=digest)
        )

    manifest = ArxivOaiManifest(
        source_date=source_date,
        snapshot=snapshot,
        pages=tuple(manifest_pages),
    )
    return store.put_manifest(
        _terminal_manifest_key(source_date),
        manifest.model_dump_json().encode(),
    )
