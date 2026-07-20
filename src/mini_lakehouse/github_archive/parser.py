import gzip
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pyarrow as pa
from pydantic import ValidationError

from mini_lakehouse.github_archive.models import ArchiveHour, GithubArchiveEvent

LANDING_ARROW_SCHEMA = pa.schema(
    [
        pa.field("event_id", pa.string(), nullable=False),
        pa.field("event_type", pa.string(), nullable=False),
        pa.field("actor_id", pa.int64()),
        pa.field("actor_login", pa.string()),
        pa.field("repository_id", pa.int64()),
        pa.field("repository_name", pa.string()),
        pa.field("payload_json", pa.string(), nullable=False),
        pa.field("is_public", pa.bool_(), nullable=False),
        pa.field("occurred_at", pa.timestamp("us", tz="UTC"), nullable=False),
        pa.field("source_file", pa.string(), nullable=False),
        pa.field("source_hour", pa.timestamp("us", tz="UTC"), nullable=False),
        pa.field("ingested_at", pa.timestamp("us", tz="UTC"), nullable=False),
        pa.field("raw_event_json", pa.string(), nullable=False),
    ]
)


@dataclass(frozen=True, slots=True)
class ParsedArchive:
    table: pa.Table
    rejected_row_count: int


def parse_archive(
    path: Path,
    archive_hour: ArchiveHour,
    *,
    max_error_ratio: float,
) -> ParsedArchive:
    ingested_at = datetime.now(UTC)
    records: list[dict[str, object]] = []
    rejected = 0
    total = 0

    with gzip.open(path, "rt", encoding="utf-8") as source:
        for line in source:
            total += 1
            raw_line = line.rstrip("\n")
            try:
                event = GithubArchiveEvent.model_validate_json(raw_line)
            except ValidationError:
                rejected += 1
                continue
            records.append(
                event.to_record(
                    source_file=archive_hour.filename,
                    source_hour=archive_hour.value,
                    ingested_at=ingested_at,
                    raw_event_json=raw_line,
                )
            )

    if total == 0:
        raise ValueError(f"Archive is empty: {path}")
    if rejected / total > max_error_ratio:
        raise ValueError(
            f"Rejected {rejected}/{total} rows, exceeding max error ratio {max_error_ratio}"
        )
    if not records:
        raise ValueError(f"Archive contains no valid rows: {path}")

    return ParsedArchive(
        table=pa.Table.from_pylist(records, schema=LANDING_ARROW_SCHEMA),
        rejected_row_count=rejected,
    )
