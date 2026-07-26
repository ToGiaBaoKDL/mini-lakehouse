from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from mini_lakehouse.contracts import iceberg_schema, load_contracts
from mini_lakehouse.contracts.catalog import CatalogContract
from mini_lakehouse.contracts.policies import DataCompactionPolicyContract
from mini_lakehouse.contracts.registry import PlatformContracts
from mini_lakehouse.contracts.sources import SourceContract


def test_repository_contracts_form_a_valid_registry() -> None:
    contracts = load_contracts(Path("contracts"))
    arxiv_source = contracts.source("arxiv")
    arxiv_processor = contracts.processor("arxiv_glm_ocr")

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
    assert arxiv_source.storage_prefix == "api/arxiv"
    assert arxiv_source.raw_object_prefix == "api/arxiv/raw/oai"
    assert arxiv_source.table_storage_prefix("oai_records_raw") == (
        "api/arxiv/tables/oai_records_raw"
    )
    assert arxiv_processor.runner.model_resources.model.name == "mini-lakehouse-glm-ocr"
    assert (
        arxiv_processor.runner.model_resources.layout_model.name == "mini-lakehouse-pp-doclayout-v3"
    )
    assert contracts.curated_product("github").table_identifier("events").iceberg == (
        "curated",
        "github",
        "events",
    )
    assert contracts.curated_product("arxiv").table("ocr_document_runs").primary_key == (
        "batch_id",
        "request_id",
    )
    assert (
        contracts.domain("engineering").table_identifier("repository_activity_daily").trino("prod")
        == '"prod"."analytics.engineering"."fct_repository_activity_daily"'
    )
    assert contracts.domain("engineering").table("repository_activity_daily").grain == (
        "activity_date",
        "repository_id",
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
    events_contract = load_contracts().source("github_archive").table("events_raw")
    events_schema = iceberg_schema(events_contract.columns)

    assert [(field.field_id, field.name, field.required) for field in events_schema.fields] == [
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


def test_arxiv_lineage_field_ids_are_stable() -> None:
    contracts = load_contracts()
    landing = iceberg_schema(contracts.source("arxiv").table("oai_records_raw").columns)
    document_runs = iceberg_schema(
        contracts.curated_product("arxiv").table("ocr_document_runs").columns
    )

    assert landing.find_field("raw_object_key").field_id == 18
    assert landing.find_field("raw_object_sha256").field_id == 19
    assert landing.find_field("record_sha256").field_id == 21
    assert document_runs.find_field("request_id").field_id == 1
    assert document_runs.find_field("source_record_sha256").field_id == 5
    assert document_runs.find_field("processing_id").field_id == 10


def test_registry_accepts_a_second_source_family_without_core_code_changes() -> None:
    payload = load_contracts().model_dump(mode="python")
    payload["sources"] = (
        *payload["sources"],
        {
            "version": 1,
            "name": "warehouse_raw",
            "source_type": "rdbms",
            "owner": "warehouse-source-team",
            "contact": {
                "name": "Warehouse Source Team",
                "email": "warehouse-source-team@example.com",
            },
            "description": "Fixture proving source-family extensibility.",
            "checkpoint": {"kind": "timestamp", "field": "updated_at"},
            "tables": [
                {
                    "key": "orders",
                    "name": "warehouse_raw_orders",
                    "schema_version": 1,
                    "columns": [
                        {
                            "field_id": 1,
                            "name": "updated_at",
                            "data_type": "timestamptz",
                            "required": True,
                            "description": "Source update checkpoint.",
                        }
                    ],
                    "partitioning": [{"field": "updated_at", "transform": "day"}],
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


@pytest.mark.parametrize(
    "collection",
    ["sources", "curated_products", "processors", "domains", "policies"],
)
def test_registry_rejects_duplicate_owned_contracts(collection: str) -> None:
    payload = _registry_payload()
    payload[collection] = (*payload[collection], payload[collection][0])

    with pytest.raises(ValidationError, match="unique"):
        PlatformContracts.model_validate(payload)


def test_registry_rejects_analytics_policy_partition_drift() -> None:
    payload = _registry_payload()
    analytics_compaction = next(
        policy
        for policy in payload["policies"]
        if policy["name"] == "mlh-analytics-engineering-compact-data-files"
    )
    analytics_compaction["execution"]["partition_field"] = "source_hour"

    with pytest.raises(ValidationError, match="bounds optimize by"):
        PlatformContracts.model_validate(payload)


def test_source_contract_rejects_duplicate_table_identifiers() -> None:
    payload = _registry_payload()["sources"][0]
    payload["tables"] = (*payload["tables"], payload["tables"][0])

    with pytest.raises(ValidationError, match="table keys and names must be unique"):
        SourceContract.model_validate(payload)


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


def test_source_contract_rejects_unsafe_raw_subpaths() -> None:
    payload = load_contracts().source("arxiv").model_dump(mode="python")
    payload["raw_subpath"] = "../secrets"

    with pytest.raises(ValidationError, match="normalized relative paths"):
        SourceContract.model_validate(payload)


def test_source_contract_derives_raw_object_boundary() -> None:
    payload = load_contracts().source("arxiv").model_dump(mode="python")
    payload["raw_subpath"] = "archive/pages"

    source = SourceContract.model_validate(payload)

    assert source.raw_object_prefix == "api/arxiv/raw/archive/pages"


def test_normalized_registry_is_deterministic_across_loads() -> None:
    first = load_contracts().model_dump_json()
    load_contracts.cache_clear()

    assert load_contracts().model_dump_json() == first


def test_managed_table_contracts_do_not_duplicate_physical_locations() -> None:
    contracts = load_contracts()

    assert all(
        "location_prefix" not in table.model_fields_set
        for source in contracts.sources
        for table in source.tables
    )
    assert all(
        "location_prefix" not in table.model_fields_set
        for product in contracts.curated_products
        for table in product.tables
    )
