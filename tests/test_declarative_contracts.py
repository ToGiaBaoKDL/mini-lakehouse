import shutil
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from mini_lakehouse.contracts import iceberg_schema, load_contracts
from mini_lakehouse.contracts.maintenance import MaintenanceContract
from mini_lakehouse.contracts.platform import PlatformContract
from mini_lakehouse.contracts.registry import PlatformContracts
from mini_lakehouse.contracts.sources import SourceContract


def test_repository_contracts_form_a_valid_registry() -> None:
    contracts = load_contracts(Path("contracts"))
    arxiv_source = contracts.source("arxiv")
    arxiv_processor = contracts.processor("arxiv_glm_ocr")

    assert contracts.platform.catalog.name == "prod"
    assert {identity.name for identity in contracts.access.service_identities} == {
        "prefect_ingestion",
        "trino_engine",
    }
    assert {role.name for role in contracts.access.catalog_roles} == {
        "prefect_ingestion",
        "trino_engine",
    }
    prefect_role = next(
        role for role in contracts.access.catalog_roles if role.name == "prefect_ingestion"
    )
    namespace_grants = {
        grant.namespace: {privilege.value for privilege in grant.privileges}
        for grant in prefect_role.grants
        if grant.type == "namespace"
    }
    assert set(namespace_grants) == {
        ("landing",),
        ("curated",),
    }
    assert all(
        {"TABLE_WRITE_DATA", "TABLE_ADD_SNAPSHOT", "TABLE_SET_SNAPSHOT_REF"} <= privileges
        for privileges in namespace_grants.values()
    )
    assert {namespace.path for namespace in contracts.platform.namespaces} == {
        ("landing",),
        ("curated",),
        ("analytics",),
    }
    assert contracts.source("github_archive").table_identifier("events_raw").iceberg == (
        "landing",
        "github_archive_events_raw",
    )
    assert arxiv_source.storage_prefix == "api/arxiv"
    assert arxiv_source.raw_object_prefix == "api/arxiv/raw/oai"
    assert arxiv_source.table_storage_prefix("oai_records_raw") == (
        "api/arxiv/tables/oai_records_raw"
    )
    assert (
        arxiv_processor.runner.kaggle.runner_dataset_prefix == "mini-lakehouse-arxiv-glm-ocr-runner"
    )
    assert arxiv_processor.batch.max_documents == 2
    assert arxiv_processor.inference.max_workers == 8
    assert arxiv_processor.runner.default_provider == "kaggle"
    assert arxiv_processor.runner.kaggle.model_resources.model.name == "mini-lakehouse-glm-ocr"
    assert (
        arxiv_processor.runner.kaggle.model_resources.layout_model.name
        == "mini-lakehouse-pp-doclayout-v3"
    )
    assert arxiv_processor.runner.modal.gpu == "A100-40GB"
    assert contracts.curated_product("github").table_identifier("events").iceberg == (
        "curated",
        "github",
        "events",
    )
    assert contracts.curated_product("arxiv").table("ocr_document_runs").primary_key == (
        "batch_id",
        "request_id",
    )
    ocr_batch_columns = {
        column.name: column
        for column in contracts.curated_product("arxiv").table("ocr_batches").columns
    }
    assert "kernel_slug" not in ocr_batch_columns
    assert all(
        ocr_batch_columns[name].required for name in ("job_json", "provider", "provider_reference")
    )
    engineering = contracts.domain("engineering")
    assert engineering.analytics_namespace == ("analytics", "engineering")
    assert "tables" not in type(engineering).model_fields


def test_contract_layout_has_one_file_per_platform_concern() -> None:
    contract_files = {
        path.relative_to("contracts").as_posix() for path in Path("contracts").rglob("*.yaml")
    }

    assert contract_files == {
        "access.yaml",
        "curated/arxiv.yaml",
        "curated/github.yaml",
        "domains/engineering.yaml",
        "maintenance.yaml",
        "platform.yaml",
        "processors/arxiv_glm_ocr.yaml",
        "sources/arxiv.yaml",
        "sources/github_archive.yaml",
    }
    assert not Path("contracts/catalog.yaml").exists()
    assert not Path("contracts/curated_products").exists()
    assert not Path("contracts/policies").exists()


def test_contracts_do_not_expose_unowned_generic_escape_hatches() -> None:
    contracts = load_contracts()

    assert "properties" not in type(contracts.platform.catalog).model_fields
    assert all("owner" not in type(tier).model_fields for tier in contracts.maintenance.tiers)


def test_entity_contract_collections_can_start_empty(tmp_path: Path) -> None:
    root = tmp_path / "contracts"
    shutil.copytree("contracts", root)
    for folder in ("sources", "curated", "processors", "domains"):
        for contract_file in (root / folder).glob("*.yaml"):
            contract_file.unlink()
    maintenance_path = root / "maintenance.yaml"
    maintenance = yaml.safe_load(maintenance_path.read_text(encoding="utf-8"))
    for tier in maintenance["tiers"]:
        tier["optimizations"] = []
    maintenance_path.write_text(yaml.safe_dump(maintenance), encoding="utf-8")

    contracts = load_contracts(root)

    assert contracts.sources == ()
    assert contracts.curated == ()
    assert contracts.processors == ()
    assert contracts.domains == ()


def test_maintenance_contract_rejects_unknown_optimization_fields() -> None:
    with pytest.raises(ValidationError, match="compaction_strategy"):
        MaintenanceContract.model_validate(
            {
                "version": 1,
                "tiers": [
                    {
                        "tier": tier,
                        "metadata_retention": {
                            "delete_after_commit": True,
                            "previous_versions_max": 30,
                        },
                        "snapshot_max_age_days": 14,
                        "orphan_file_max_age_days": 30,
                        "optimizations": (
                            [
                                {
                                    "name": "compact",
                                    "target": {
                                        "type": "namespace",
                                        "path": [tier],
                                    },
                                    "partition_field": "event_date",
                                    "lookback_days": 7,
                                    "target_file_size_bytes": 134217728,
                                    "compaction_strategy": "bin-pack",
                                }
                            ]
                            if tier == "curated"
                            else []
                        ),
                    }
                    for tier in ("landing", "curated", "analytics")
                ],
            }
        )


def test_contract_loader_rejects_duplicate_yaml_keys(tmp_path: Path) -> None:
    (tmp_path / "platform.yaml").write_text(
        "version: 1\nversion: 1\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Duplicate YAML key: version"):
        load_contracts(tmp_path)


def test_contract_loader_rejects_secret_like_keys(tmp_path: Path) -> None:
    (tmp_path / "platform.yaml").write_text(
        "version: 1\ncredential: do-not-commit\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Secret-like key"):
        load_contracts(tmp_path)


def test_github_archive_iceberg_field_ids_are_stable() -> None:
    events_contract = load_contracts().source("github_archive").table("events_raw")
    events_schema = iceberg_schema(events_contract.columns, events_contract.primary_key)

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
    landing_contract = contracts.source("arxiv").table("oai_records_raw")
    document_runs_contract = contracts.curated_product("arxiv").table("ocr_document_runs")
    landing = iceberg_schema(landing_contract.columns, landing_contract.primary_key)
    document_runs = iceberg_schema(
        document_runs_contract.columns,
        document_runs_contract.primary_key,
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
                    "description": "Raw warehouse order updates.",
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
                    "partitioning": [
                        {
                            "field_id": 1000,
                            "field": "updated_at",
                            "transform": "day",
                        }
                    ],
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
    payload = _registry_payload()["platform"]
    payload["unmanaged_setting"] = True

    with pytest.raises(ValidationError, match="unmanaged_setting"):
        PlatformContract.model_validate(payload)


@pytest.mark.parametrize(
    "collection",
    ["sources", "curated", "processors", "domains"],
)
def test_registry_rejects_duplicate_owned_contracts(collection: str) -> None:
    payload = _registry_payload()
    payload[collection] = (*payload[collection], payload[collection][0])

    with pytest.raises(ValidationError, match="unique"):
        PlatformContracts.model_validate(payload)


def test_registry_rejects_managed_table_policy_partition_drift() -> None:
    payload = _registry_payload()
    landing = next(tier for tier in payload["maintenance"]["tiers"] if tier["tier"] == "landing")
    landing["optimizations"][0]["partition_field"] = "activity_date"

    with pytest.raises(ValidationError, match="bounds optimize by"):
        PlatformContracts.model_validate(payload)


def test_registry_rejects_access_to_an_unknown_namespace() -> None:
    payload = _registry_payload()
    role = payload["access"]["catalog_roles"][0]
    role["grants"] = (
        *role["grants"],
        {
            "type": "namespace",
            "namespace": ("unknown",),
            "privileges": ("TABLE_READ_DATA",),
        },
    )

    with pytest.raises(ValidationError, match="references unknown namespace"):
        PlatformContracts.model_validate(payload)


def test_source_contract_rejects_duplicate_table_identifiers() -> None:
    payload = _registry_payload()["sources"][0]
    payload["tables"] = (*payload["tables"], payload["tables"][0])

    with pytest.raises(ValidationError, match="table keys and names must be unique"):
        SourceContract.model_validate(payload)


def test_catalog_rejects_namespaces_outside_lifecycle_roots() -> None:
    payload = _registry_payload()["platform"]
    payload["namespaces"] = (
        *payload["namespaces"],
        {
            "path": ("sandbox",),
            "owner": "data-platform",
            "description": "Invalid lifecycle root.",
        },
    )

    with pytest.raises(ValidationError, match="landing, curated, and analytics roots"):
        PlatformContract.model_validate(payload)


def test_maintenance_contract_requires_each_tier_once() -> None:
    payload = _registry_payload()["maintenance"]
    payload["tiers"] = (
        payload["tiers"][0],
        payload["tiers"][0],
        payload["tiers"][2],
    )

    with pytest.raises(ValidationError, match="must be unique"):
        MaintenanceContract.model_validate(payload)


def test_source_checkpoint_rejects_unknown_kinds() -> None:
    payload = _registry_payload()["sources"][0]
    payload["checkpoint"] = {"kind": "custom", "field": "source_hour"}

    with pytest.raises(ValidationError, match="literal_error"):
        SourceContract.model_validate(payload)


@pytest.mark.parametrize("raw_subpath", ("../secrets", "pages//raw", "pages/./raw"))
def test_source_contract_rejects_unsafe_raw_subpaths(raw_subpath: str) -> None:
    payload = load_contracts().source("arxiv").model_dump(mode="python")
    payload["raw_subpath"] = raw_subpath

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
        for product in contracts.curated
        for table in product.tables
    )


def test_platform_and_storage_code_are_source_agnostic() -> None:
    source_names = {source.name for source in load_contracts().sources}
    modules = [
        *Path("src/mini_lakehouse/platform").rglob("*.py"),
        *Path("src/mini_lakehouse/storage").rglob("*.py"),
    ]

    for module in modules:
        content = module.read_text(encoding="utf-8")
        assert all(source_name not in content for source_name in source_names), module
