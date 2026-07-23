"""Parse exact OAI response pages into typed landing records."""

import hashlib
import json
import re
from datetime import UTC, date, datetime
from xml.etree import ElementTree

import pyarrow as pa

from mini_lakehouse.contracts import PlatformContracts, arrow_schema, load_contracts
from mini_lakehouse.sources.arxiv.models import OaiDay

_OAI = "http://www.openarchives.org/OAI/2.0/"
_ARXIV = "http://arxiv.org/OAI/arXiv/"
_OAI_IDENTIFIER_PREFIX = "oai:arXiv.org:"
_WHITESPACE = re.compile(r"\s+")


def _normalized_text(element: ElementTree.Element | None) -> str | None:
    if element is None or element.text is None:
        return None
    value = _WHITESPACE.sub(" ", element.text).strip()
    return value or None


def _date(element: ElementTree.Element | None) -> date | None:
    value = _normalized_text(element)
    return date.fromisoformat(value) if value else None


def _metadata_value(metadata: ElementTree.Element | None, name: str) -> str | None:
    if metadata is None:
        return None
    return _normalized_text(metadata.find(f"{{{_ARXIV}}}{name}"))


def _authors(metadata: ElementTree.Element | None) -> str:
    values: list[dict[str, str | None]] = []
    if metadata is not None:
        parent = metadata.find(f"{{{_ARXIV}}}authors")
        for author in parent.findall(f"{{{_ARXIV}}}author") if parent is not None else ():
            values.append(
                {
                    "keyname": _normalized_text(author.find(f"{{{_ARXIV}}}keyname")),
                    "forenames": _normalized_text(author.find(f"{{{_ARXIV}}}forenames")),
                    "suffix": _normalized_text(author.find(f"{{{_ARXIV}}}suffix")),
                    "affiliation": _normalized_text(author.find(f"{{{_ARXIV}}}affiliation")),
                }
            )
    return json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def parse_oai_pages(
    pages: tuple[bytes, ...],
    day: OaiDay,
    *,
    raw_object_key: str,
    raw_object_sha256: str,
    contracts: PlatformContracts | None = None,
    ingested_at: datetime | None = None,
) -> pa.Table:
    registry = contracts or load_contracts()
    table_contract = registry.source("arxiv").table("oai_records_raw")
    schema = arrow_schema(table_contract.columns)
    landed_at = ingested_at or datetime.now(UTC)
    records: list[dict[str, object]] = []

    for page in pages:
        root = ElementTree.fromstring(page)
        for record in root.findall(f"{{{_OAI}}}ListRecords/{{{_OAI}}}record"):
            header = record.find(f"{{{_OAI}}}header")
            if header is None:
                raise ValueError("OAI record is missing its header")
            oai_identifier = _normalized_text(header.find(f"{{{_OAI}}}identifier"))
            datestamp = _date(header.find(f"{{{_OAI}}}datestamp"))
            if oai_identifier is None or datestamp is None:
                raise ValueError("OAI record header is missing identifier or datestamp")
            if datestamp != day.value:
                raise ValueError(
                    f"OAI returned datestamp {datestamp} outside requested day {day.iso}"
                )
            metadata_wrapper = record.find(f"{{{_OAI}}}metadata")
            metadata = (
                metadata_wrapper.find(f"{{{_ARXIV}}}arXiv")
                if metadata_wrapper is not None
                else None
            )
            arxiv_id = _metadata_value(metadata, "id")
            if arxiv_id is None:
                if not oai_identifier.startswith(_OAI_IDENTIFIER_PREFIX):
                    raise ValueError(f"Unexpected OAI identifier: {oai_identifier!r}")
                arxiv_id = oai_identifier.removeprefix(_OAI_IDENTIFIER_PREFIX)
            categories = (_metadata_value(metadata, "categories") or "").split()
            record_xml = ElementTree.tostring(record, encoding="utf-8")
            records.append(
                {
                    "oai_identifier": oai_identifier,
                    "arxiv_id": arxiv_id,
                    "title": _metadata_value(metadata, "title"),
                    "abstract": _metadata_value(metadata, "abstract"),
                    "authors_json": _authors(metadata),
                    "categories": " ".join(categories) or None,
                    "primary_category": categories[0] if categories else None,
                    "license_uri": _metadata_value(metadata, "license"),
                    "doi": _metadata_value(metadata, "doi"),
                    "journal_ref": _metadata_value(metadata, "journal-ref"),
                    "comments": _metadata_value(metadata, "comments"),
                    "created_date": _date(
                        metadata.find(f"{{{_ARXIV}}}created") if metadata is not None else None
                    ),
                    "updated_date": _date(
                        metadata.find(f"{{{_ARXIV}}}updated") if metadata is not None else None
                    ),
                    "datestamp_date": datestamp,
                    "set_specs": " ".join(
                        value
                        for element in header.findall(f"{{{_OAI}}}setSpec")
                        if (value := _normalized_text(element)) is not None
                    )
                    or None,
                    "pdf_url": f"https://arxiv.org/pdf/{arxiv_id}",
                    "is_deleted": header.attrib.get("status") == "deleted",
                    "raw_object_key": raw_object_key,
                    "raw_object_sha256": raw_object_sha256,
                    "record_index": len(records),
                    "record_sha256": hashlib.sha256(record_xml).hexdigest(),
                    "ingested_at": landed_at,
                }
            )
    return pa.Table.from_pylist(records, schema=schema)
