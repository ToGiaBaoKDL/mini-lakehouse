from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from mini_lakehouse.sources.github_archive.models import ArchiveHour


def test_archive_hour_builds_source_filename_and_partition() -> None:
    archive_hour = ArchiveHour.parse("2025-01-02T03:00:00Z")

    assert archive_hour.filename == "2025-01-02-3.json.gz"
    assert archive_hour.partition_path == "year=2025/month=01/day=02/hour=03"
    assert archive_hour.value == datetime(2025, 1, 2, 3, tzinfo=UTC)


def test_archive_window_defaults_to_the_previous_complete_hour() -> None:
    now = datetime(2025, 1, 2, 4, 37, tzinfo=UTC)

    start, end = ArchiveHour.resolve_window(None, None, now=now)

    assert start.value == datetime(2025, 1, 2, 3, tzinfo=UTC)
    assert end == start


def test_archive_window_accepts_one_hour_or_an_inclusive_range() -> None:
    single_start, single_end = ArchiveHour.resolve_window("2025-01-02T03:00:00Z", None)
    range_start, range_end = ArchiveHour.resolve_window(
        "2025-01-02T03:00:00Z",
        "2025-01-02T05:00:00Z",
    )

    assert single_end == single_start
    assert range_start.value == datetime(2025, 1, 2, 3, tzinfo=UTC)
    assert range_end.value == datetime(2025, 1, 2, 5, tzinfo=UTC)


def test_archive_window_rejects_ambiguous_or_reversed_ranges() -> None:
    with pytest.raises(ValueError, match="end requires start"):
        ArchiveHour.resolve_window(None, "2025-01-02T05:00:00Z")
    with pytest.raises(ValueError, match="start must be less"):
        ArchiveHour.resolve_window(
            "2025-01-02T05:00:00Z",
            "2025-01-02T03:00:00Z",
        )


@pytest.mark.parametrize(
    "value",
    ["2025-01-02T03:01:00Z", datetime(2025, 1, 2, 3)],
)
def test_archive_hour_rejects_non_utc_hour_boundaries(value: str | datetime) -> None:
    with pytest.raises(ValidationError):
        ArchiveHour.parse(value)
