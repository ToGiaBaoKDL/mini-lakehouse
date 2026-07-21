from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from mini_lakehouse.contracts import load_contracts
from mini_lakehouse.contracts.catalog import CatalogContract
from mini_lakehouse.contracts.policies import DataCompactionPolicyContract
from mini_lakehouse.contracts.registry import PlatformContracts
from mini_lakehouse.contracts.sources import SourceContract
from mini_lakehouse.sources.github_archive.schema import EVENTS_ICEBERG_SCHEMA


def test_repository_contracts_form_a_valid_registry() -> None:
    contracts = load_contracts(Path("contracts"))

    assert contracts.catalog.catalog.name == "prod"
    assert {
        namespace.storage_root
        for namespace in contracts.catalog.namespaces
        if namespace.storage_root is not None
    } == {"landing", "curated", "analytics"}
    assert contracts.source("github_archive").table_identifier("events_raw").iceberg == (
        "landing",
        "github_archive_events_raw",
    )
    assert contracts.product("github").table_identifier("events").iceberg == (
        "curated",
        "github",
        "events",
    )
    assert (
        contracts.domain("engineering").table_identifier("repository_activity_daily").trino("prod")
        == '"prod"."analytics.engineering"."fct_repository_activity_daily"'
    )


def test_policy_contract_rejects_content_the_trino_runner_would_ignore() -> None:
    with pytest.raises(ValidationError, match="compaction_strategy"):
        DataCompactionPolicyContract.model_validate(
            {
                "version": 1,
                "name": "compact",
                "namespace": ["curated"],
                "owner": "data-platform",
                "policy_type": "system.data-compaction",
                "description": "Compaction",
                "content": {
                    "version": "2025-02-03",
                    "enable": True,
                    "config": {
                        "target_file_size_bytes": 134217728,
                        "compaction_strategy": "bin-pack",
                    },
                },
                "targets": [{"type": "namespace", "path": ["curated"]}],
            }
        )


def test_contract_loader_rejects_duplicate_yaml_keys(tmp_path: Path) -> None:
    (tmp_path / "catalog.yaml").write_text(
        "version: 1\nversion: 1\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Duplicate YAML key: version"):
        load_contracts(tmp_path)


def test_contract_loader_rejects_secret_like_keys(tmp_path: Path) -> None:
    (tmp_path / "catalog.yaml").write_text(
        "version: 1\ncredential: do-not-commit\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Secret-like key"):
        load_contracts(tmp_path)


def test_github_archive_iceberg_field_ids_are_stable() -> None:
    assert [
        (field.field_id, field.name, field.required) for field in EVENTS_ICEBERG_SCHEMA.fields
    ] == [
        (1, "event_id", True),
        (2, "event_type", True),
        (3, "actor_id", False),
        (4, "actor_login", False),
        (5, "repository_id", False),
        (6, "repository_name", False),
        (7, "payload_json", True),
        (8, "is_public", True),
        (9, "occurred_at", True),
        (10, "source_file", True),
        (11, "source_hour", True),
        (12, "ingested_at", True),
        (13, "raw_event_json", True),
    ]


def test_registry_accepts_a_second_source_family_without_core_code_changes() -> None:
    payload = load_contracts().model_dump(mode="python")
    payload["sources"] = (
        *payload["sources"],
        {
            "version": 1,
            "name": "warehouse_raw",
            "source_type": "rdbms",
            "owner": "warehouse-source-team",
            "description": "Fixture proving source-family extensibility.",
            "landing_namespace": ["landing"],
            "raw_object_prefix": "rdbms/warehouse_raw/raw",
            "checkpoint": {"kind": "timestamp", "field": "updated_at"},
            "tables": [
                {
                    "key": "orders",
                    "name": "warehouse_raw_orders",
                    "location_prefix": "rdbms/warehouse_raw/orders",
                    "schema_contract": "warehouse.orders.v1",
                    "partitioning": [{"field": "updated_at", "transform": "day"}],
                    "write_mode": "checkpoint_overwrite",
                }
            ],
        },
    )

    expanded = PlatformContracts.model_validate(payload)

    assert expanded.source("warehouse_raw").table_identifier("orders").iceberg == (
        "landing",
        "warehouse_raw_orders",
    )


def _registry_payload() -> dict[str, Any]:
    return load_contracts().model_dump(mode="python")


def test_contract_models_reject_unknown_fields() -> None:
    payload = _registry_payload()["catalog"]
    payload["unmanaged_setting"] = True

    with pytest.raises(ValidationError, match="unmanaged_setting"):
        CatalogContract.model_validate(payload)


@pytest.mark.parametrize("collection", ["sources", "products", "domains", "policies"])
def test_registry_rejects_duplicate_owned_contracts(collection: str) -> None:
    payload = _registry_payload()
    payload[collection] = (*payload[collection], payload[collection][0])

    with pytest.raises(ValidationError, match="unique"):
        PlatformContracts.model_validate(payload)


def test_source_contract_rejects_duplicate_table_identifiers() -> None:
    payload = _registry_payload()["sources"][0]
    payload["tables"] = (*payload["tables"], payload["tables"][0])

    with pytest.raises(ValidationError, match="table keys and names must be unique"):
        SourceContract.model_validate(payload)


def test_source_contract_rejects_a_non_landing_namespace() -> None:
    payload = _registry_payload()
    payload["sources"][0]["landing_namespace"] = ("unknown",)

    with pytest.raises(ValidationError, match="shared landing namespace"):
        PlatformContracts.model_validate(payload)


def test_catalog_rejects_namespaces_outside_lifecycle_roots() -> None:
    payload = _registry_payload()["catalog"]
    payload["namespaces"] = (
        *payload["namespaces"],
        {
            "path": ("sandbox",),
            "owner": "data-platform",
            "description": "Invalid lifecycle root.",
        },
    )

    with pytest.raises(ValidationError, match="outside a lifecycle root"):
        CatalogContract.model_validate(payload)


def test_source_checkpoint_is_a_discriminated_union() -> None:
    payload = _registry_payload()["sources"][0]
    payload["checkpoint"] = {"kind": "custom", "field": "source_hour"}

    with pytest.raises(ValidationError, match="union_tag_invalid"):
        SourceContract.model_validate(payload)


def test_source_contract_rejects_unsafe_object_prefixes() -> None:
    payload = _registry_payload()["sources"][0]
    payload["raw_object_prefix"] = "api/../secrets"

    with pytest.raises(ValidationError, match="normalized relative paths"):
        SourceContract.model_validate(payload)


def test_normalized_registry_is_deterministic_across_loads() -> None:
    first = load_contracts().model_dump_json()
    load_contracts.cache_clear()

    assert load_contracts().model_dump_json() == first
