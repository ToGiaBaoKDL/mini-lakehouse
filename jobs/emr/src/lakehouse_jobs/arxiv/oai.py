"""OAI-PMH harvesting and record parsing."""

import hashlib
import json
import re
from datetime import date, datetime
from urllib.parse import urlencode
from xml.etree import ElementTree

from loguru import logger

from lakehouse_jobs.common.http import http_session

OAI_NAMESPACE = "http://www.openarchives.org/OAI/2.0/"
ARXIV_NAMESPACE = "http://arxiv.org/OAI/arXiv/"
OAI_IDENTIFIER_PREFIX = "oai:arXiv.org:"
WHITESPACE = re.compile(r"\s+")


def text(element: ElementTree.Element | None) -> str | None:
    if element is None or element.text is None:
        return None
    value = WHITESPACE.sub(" ", element.text).strip()
    return value or None


def element_date(element: ElementTree.Element | None) -> date | None:
    value = text(element)
    return date.fromisoformat(value) if value else None


def metadata_value(metadata: ElementTree.Element | None, name: str) -> str | None:
    return text(metadata.find(f"{{{ARXIV_NAMESPACE}}}{name}")) if metadata is not None else None


def authors_json(metadata: ElementTree.Element | None) -> str:
    parent = metadata.find(f"{{{ARXIV_NAMESPACE}}}authors") if metadata is not None else None
    authors = [
        {
            "keyname": text(author.find(f"{{{ARXIV_NAMESPACE}}}keyname")),
            "forenames": text(author.find(f"{{{ARXIV_NAMESPACE}}}forenames")),
            "suffix": text(author.find(f"{{{ARXIV_NAMESPACE}}}suffix")),
            "affiliation": text(author.find(f"{{{ARXIV_NAMESPACE}}}affiliation")),
        }
        for author in (parent.findall(f"{{{ARXIV_NAMESPACE}}}author") if parent is not None else ())
    ]
    return json.dumps(authors, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def harvest(source_date: str, max_pages: int) -> list[bytes]:
    pages: list[bytes] = []
    token = None
    seen_tokens: set[str] = set()
    with http_session() as http:
        for _ in range(max_pages):
            parameters = (
                {
                    "verb": "ListRecords",
                    "metadataPrefix": "arXiv",
                    "from": source_date,
                    "until": source_date,
                }
                if token is None
                else {"verb": "ListRecords", "resumptionToken": token}
            )
            response = http.get(
                f"https://oaipmh.arxiv.org/oai?{urlencode(parameters)}",
                timeout=(10, 180),
            )
            response.raise_for_status()
            payload = response.content
            root = ElementTree.fromstring(payload)
            errors = root.findall(f"{{{OAI_NAMESPACE}}}error")
            if errors and errors[0].attrib.get("code") != "noRecordsMatch":
                raise RuntimeError(
                    f"ArXiv OAI error {errors[0].attrib.get('code', 'unknown')}: "
                    f"{text(errors[0]) or ''}"
                )
            pages.append(payload)
            logger.info("Harvested OAI page {}", len(pages))
            token = text(
                root.find(f"{{{OAI_NAMESPACE}}}ListRecords/{{{OAI_NAMESPACE}}}resumptionToken")
            )
            if not token:
                return pages
            if token in seen_tokens:
                raise RuntimeError("ArXiv returned a repeated OAI resumption token")
            seen_tokens.add(token)
    raise RuntimeError(f"ArXiv exceeded the {max_pages}-page daily safety limit")


def parse_records(
    pages: list[bytes],
    *,
    source_day: date,
    page_objects: list[tuple[str, str]],
    ingested_at: datetime,
) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    identifiers: set[str] = set()
    for page, (raw_object_key, raw_object_sha256) in zip(
        pages,
        page_objects,
        strict=True,
    ):
        root = ElementTree.fromstring(page)
        for record in root.findall(f"{{{OAI_NAMESPACE}}}ListRecords/{{{OAI_NAMESPACE}}}record"):
            header = record.find(f"{{{OAI_NAMESPACE}}}header")
            if header is None:
                raise RuntimeError("OAI record is missing its header")
            oai_identifier = text(header.find(f"{{{OAI_NAMESPACE}}}identifier"))
            datestamp = element_date(header.find(f"{{{OAI_NAMESPACE}}}datestamp"))
            if oai_identifier is None or datestamp != source_day:
                raise RuntimeError("OAI returned a record outside the requested datestamp day")
            if oai_identifier in identifiers:
                raise RuntimeError(f"OAI returned duplicate identifier {oai_identifier!r}")
            identifiers.add(oai_identifier)
            wrapper = record.find(f"{{{OAI_NAMESPACE}}}metadata")
            metadata = wrapper.find(f"{{{ARXIV_NAMESPACE}}}arXiv") if wrapper is not None else None
            arxiv_id = metadata_value(metadata, "id")
            if arxiv_id is None:
                if not oai_identifier.startswith(OAI_IDENTIFIER_PREFIX):
                    raise RuntimeError(f"Unexpected OAI identifier: {oai_identifier}")
                arxiv_id = oai_identifier.removeprefix(OAI_IDENTIFIER_PREFIX)
            categories = (metadata_value(metadata, "categories") or "").split()
            record_xml = ElementTree.tostring(record, encoding="utf-8")
            records.append(
                {
                    "oai_identifier": oai_identifier,
                    "arxiv_id": arxiv_id,
                    "title": metadata_value(metadata, "title"),
                    "abstract": metadata_value(metadata, "abstract"),
                    "authors_json": authors_json(metadata),
                    "categories": " ".join(categories) or None,
                    "primary_category": categories[0] if categories else None,
                    "license_uri": metadata_value(metadata, "license"),
                    "doi": metadata_value(metadata, "doi"),
                    "journal_ref": metadata_value(metadata, "journal-ref"),
                    "comments": metadata_value(metadata, "comments"),
                    "created_date": element_date(
                        metadata.find(f"{{{ARXIV_NAMESPACE}}}created")
                        if metadata is not None
                        else None
                    ),
                    "updated_date": element_date(
                        metadata.find(f"{{{ARXIV_NAMESPACE}}}updated")
                        if metadata is not None
                        else None
                    ),
                    "datestamp_date": datestamp,
                    "set_specs": " ".join(
                        value
                        for element in header.findall(f"{{{OAI_NAMESPACE}}}setSpec")
                        if (value := text(element)) is not None
                    )
                    or None,
                    "pdf_url": f"https://arxiv.org/pdf/{arxiv_id}",
                    "is_deleted": header.attrib.get("status") == "deleted",
                    "raw_object_key": raw_object_key,
                    "raw_object_sha256": raw_object_sha256,
                    "record_index": len(records),
                    "record_sha256": hashlib.sha256(record_xml).hexdigest(),
                    "ingested_at": ingested_at,
                }
            )
    return records
