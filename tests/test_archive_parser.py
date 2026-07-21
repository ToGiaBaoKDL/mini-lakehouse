import gzip
import json
from pathlib import Path

import pytest

from mini_lakehouse.sources.github_archive.models import ArchiveHour
from mini_lakehouse.sources.github_archive.parser import parse_archive


def _event(event_id: str = "123") -> dict[str, object]:
    return {
        "id": event_id,
        "type": "PushEvent",
        "actor": {"id": 7, "login": "octocat"},
        "repo": {"id": 9, "name": "octo/repo"},
        "payload": {"size": 3},
        "public": True,
        "created_at": "2025-01-02T03:04:05Z",
    }


def _write_archive(path: Path, lines: list[str]) -> None:
    with gzip.open(path, "wt", encoding="utf-8") as output:
        output.write("\n".join(lines))
        output.write("\n")


def test_parse_archive_preserves_raw_json_and_uses_real_timestamps(tmp_path: Path) -> None:
    archive_path = tmp_path / "events.json.gz"
    raw_json = json.dumps(_event())
    _write_archive(archive_path, [raw_json])

    parsed = parse_archive(
        archive_path,
        ArchiveHour.parse("2025-01-02T03:00:00Z"),
        max_error_ratio=0,
    )

    assert parsed.table.num_rows == 1
    assert parsed.rejected_row_count == 0
    record = parsed.table.to_pylist()[0]
    assert record["event_id"] == "123"
    assert record["payload_json"] == '{"size":3}'
    assert record["raw_event_json"] == raw_json
    assert str(parsed.table.schema.field("occurred_at").type) == "timestamp[us, tz=UTC]"


def test_parse_archive_fails_when_reject_ratio_exceeds_contract(tmp_path: Path) -> None:
    archive_path = tmp_path / "events.json.gz"
    _write_archive(archive_path, [json.dumps(_event()), "not-json"])

    with pytest.raises(ValueError, match="exceeding max error ratio"):
        parse_archive(
            archive_path,
            ArchiveHour.parse("2025-01-02T03:00:00Z"),
            max_error_ratio=0.1,
        )
