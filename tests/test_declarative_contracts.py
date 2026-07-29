from pathlib import Path

import pytest

from lakehouse_platform.contracts import load_contracts
from lakehouse_platform.platform.catalog.schema import iceberg_schema


def test_repository_contracts_form_one_glue_registry() -> None:
    contracts = load_contracts(Path("contracts"))

    assert contracts.source("github_archive").table_identifier("events_raw").iceberg == (
        "landing_github_archive",
        "events_raw",
    )
    assert contracts.source("arxiv").raw_object_prefix == "api/arxiv/raw/oai"
    assert contracts.curated_product("github").table_identifier("events").iceberg == (
        "curated_github",
        "events",
    )
    assert contracts.domain("engineering").database == "analytics_engineering"


def test_primary_keys_compile_to_iceberg_identifier_fields() -> None:
    table = load_contracts().curated_product("arxiv").table("papers")
    schema = iceberg_schema(table.columns, table.primary_key)

    assert schema.identifier_field_ids == [1]


def test_contract_layout_excludes_cloud_identity_and_maintenance_policy() -> None:
    files = {path.relative_to("contracts").as_posix() for path in Path("contracts").rglob("*.yaml")}

    assert "access.yaml" not in files
    assert "maintenance.yaml" not in files
    assert files == {
        "curated/arxiv.yaml",
        "curated/github.yaml",
        "domains/engineering.yaml",
        "sources/arxiv.yaml",
        "sources/github_archive.yaml",
    }


def test_contract_loader_rejects_duplicate_yaml_keys(tmp_path: Path) -> None:
    sources = tmp_path / "sources"
    sources.mkdir()
    (sources / "duplicate.yaml").write_text(
        "version: 1\nversion: 1\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Duplicate YAML key"):
        load_contracts(tmp_path)


def test_contracts_never_contain_secrets() -> None:
    for path in Path("contracts").rglob("*.yaml"):
        source = path.read_text(encoding="utf-8").lower()
        assert "password:" not in source
        assert "secret:" not in source
        assert "access_key:" not in source
