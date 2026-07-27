import json

from mini_lakehouse.config import Settings
from mini_lakehouse.contracts import load_contracts
from mini_lakehouse.platform.desired_state import compile_desired_state


def test_desired_state_is_deterministic_and_contains_no_secrets() -> None:
    contracts = load_contracts()

    first = compile_desired_state(Settings(), contracts)
    second = compile_desired_state(Settings(), contracts)
    payload = json.dumps(first.catalog.management_payload(), sort_keys=True)

    assert first == second
    assert len(first.contract_digest) == 64
    assert "minioadmin" not in payload
    assert "secretpassword" not in payload


def test_desired_state_compiles_every_platform_owned_table_once() -> None:
    contracts = load_contracts()

    state = compile_desired_state(Settings(), contracts)

    expected_count = sum(len(source.tables) for source in contracts.sources) + sum(
        len(product.tables) for product in contracts.curated
    )
    assert len(state.managed_tables) == expected_count
    assert len({table.identifier for table in state.managed_tables}) == expected_count
    assert len({table.location for table in state.managed_tables}) == expected_count


def test_desired_table_state_keeps_stable_iceberg_partition_ids() -> None:
    state = compile_desired_state(Settings(), load_contracts())
    github_landing = next(
        table
        for table in state.managed_tables
        if table.identifier == ("landing", "github_archive_events_raw")
    )
    github_curated = next(
        table
        for table in state.managed_tables
        if table.identifier == ("curated", "github", "events")
    )

    assert github_landing.partitioning[0].field_id == 1000
    assert github_landing.location == ("s3://landing/api/github_archive/tables/events_raw")
    assert github_curated.partitioning[0].field_id == 1000
    assert github_curated.location == "s3://curated/github/events"
