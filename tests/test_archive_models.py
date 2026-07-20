from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from mini_lakehouse.github_archive.models import ArchiveHour


def test_archive_hour_builds_source_filename_and_partition() -> None:
    archive_hour = ArchiveHour.parse("2025-01-02T03:00:00Z")

    assert archive_hour.filename == "2025-01-02-3.json.gz"
    assert archive_hour.partition_path == "year=2025/month=01/day=02/hour=03"
    assert archive_hour.value == datetime(2025, 1, 2, 3, tzinfo=UTC)


@pytest.mark.parametrize(
    "value",
    ["2025-01-02T03:01:00Z", datetime(2025, 1, 2, 3)],
)
def test_archive_hour_rejects_non_utc_hour_boundaries(value: str | datetime) -> None:
    with pytest.raises(ValidationError):
        ArchiveHour.parse(value)
