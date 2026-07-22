import json
import os
import subprocess
from datetime import UTC, datetime, time, timedelta
from pathlib import Path

import pyarrow as pa
import pytest

from mini_lakehouse.config import get_settings
from mini_lakehouse.contracts import arrow_schema, load_contracts
from mini_lakehouse.curated_products.github.service import GithubCurationService
from mini_lakehouse.platform.trino import TrinoExecutor
from mini_lakehouse.sources.github_archive.models import ArchiveHour
from mini_lakehouse.sources.github_archive.repository import GithubArchiveRepository
from mini_lakehouse.storage.iceberg import load_iceberg_catalog

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_LAKEHOUSE_INTEGRATION") != "1",
        reason="set RUN_LAKEHOUSE_INTEGRATION=1 with a clean Compose stack running",
    ),
]

_EVENT_ID = "mini-lakehouse-e2e-merge-key"
_ACTOR_ID = 9_000_000_001
_REPOSITORY_ID = 9_000_000_002


def _event(
    *,
    source_hour: ArchiveHour,
    occurred_at: datetime,
    ingested_at: datetime,
    actor_login: str,
    repository_name: str,
    push_commit_count: int,
) -> pa.Table:
    events_contract = load_contracts().source("github_archive").table("events_raw")
    payload = {"size": push_commit_count}
    raw_event = {
        "id": _EVENT_ID,
        "type": "PushEvent",
        "actor": {"id": _ACTOR_ID, "login": actor_login},
        "repo": {"id": _REPOSITORY_ID, "name": repository_name},
        "payload": payload,
        "public": True,
        "created_at": occurred_at.isoformat(),
    }
    return pa.Table.from_pylist(
        [
            {
                "event_id": _EVENT_ID,
                "event_type": "PushEvent",
                "actor_id": _ACTOR_ID,
                "actor_login": actor_login,
                "repository_id": _REPOSITORY_ID,
                "repository_name": repository_name,
                "payload_json": json.dumps(payload, separators=(",", ":")),
                "is_public": True,
                "occurred_at": occurred_at,
                "source_file": source_hour.filename,
                "source_hour": source_hour.value,
                "ingested_at": ingested_at,
                "raw_event_json": json.dumps(raw_event, separators=(",", ":")),
            }
        ],
        schema=arrow_schema(events_contract.columns),
    )


def _run_dbt(*arguments: str) -> None:
    result = subprocess.run(
        [
            "dbt",
            *arguments,
            "--project-dir",
            "dbt/analytics",
            "--profiles-dir",
            "dbt/analytics",
        ],
        cwd=Path.cwd(),
        text=True,
        capture_output=True,
        timeout=600,
        check=False,
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"


def test_landing_curation_merge_and_analytics_build_end_to_end() -> None:
    settings = get_settings()
    current_hour = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    # Future synthetic checkpoints avoid overwriting a real GitHub Archive hour in a local stack.
    first_hour = ArchiveHour(value=current_hour + timedelta(hours=24))
    second_hour = ArchiveHour(value=current_hour + timedelta(hours=25))
    first_occurred_at = datetime.combine(
        current_hour.date() - timedelta(days=1),
        time(23, 45),
        UTC,
    )
    second_occurred_at = datetime.combine(current_hour.date(), time(0, 15), UTC)

    with load_iceberg_catalog(settings) as catalog:
        landing = GithubArchiveRepository(settings, catalog=catalog)
        landing.write_hour(
            _event(
                source_hour=first_hour,
                occurred_at=first_occurred_at,
                ingested_at=current_hour,
                actor_login="e2e-old",
                repository_name="e2e/old-name",
                push_commit_count=2,
            ),
            first_hour.value,
        )
        landing.write_hour(
            _event(
                source_hour=second_hour,
                occurred_at=second_occurred_at,
                ingested_at=current_hour + timedelta(minutes=1),
                actor_login="e2e-current",
                repository_name="e2e/current-name",
                push_commit_count=3,
            ),
            second_hour.value,
        )

    contracts = load_contracts(settings.contracts_dir)
    product = contracts.curated_product("github")
    domain = contracts.domain("engineering")
    with TrinoExecutor(settings.trino) as executor:
        curation = GithubCurationService(settings, executor=executor, contracts=contracts)
        curation.curate(first_hour)
        curation.curate(second_hour)

        events = executor.execute(
            f"""
            SELECT event_date_utc, source_hour, repository_name, actor_login
            FROM {product.table_identifier("events").trino(settings.trino.catalog)}
            WHERE event_id = ?
            """,
            (_EVENT_ID,),
        )
        assert events.rows == (
            (
                second_occurred_at.date(),
                second_hour.value,
                "e2e/current-name",
                "e2e-current",
            ),
        )

    _run_dbt("source", "freshness")
    _run_dbt("build")

    repository_mart_relation = domain.table_identifier("repository_activity_daily").trino(
        settings.trino.catalog
    )
    contributor_mart_relation = domain.table_identifier("contributor_activity_daily").trino(
        settings.trino.catalog
    )
    with TrinoExecutor(settings.trino) as executor:
        repository_mart = executor.execute(
            f"""
            SELECT activity_date, event_count, pushed_commit_count, repository_name
            FROM {repository_mart_relation}
            WHERE repository_id = ?
            """,
            (_REPOSITORY_ID,),
        )
        contributor_mart = executor.execute(
            f"""
            SELECT activity_date, event_count, actor_login
            FROM {contributor_mart_relation}
            WHERE actor_id = ?
            """,
            (_ACTOR_ID,),
        )

    assert repository_mart.rows == ((second_occurred_at.date(), 1, 3, "e2e/current-name"),)
    assert contributor_mart.rows == ((second_occurred_at.date(), 1, "e2e-current"),)
