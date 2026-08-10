from pathlib import Path

import pytest
from lakehouse.catalog.schema import iceberg_schema
from lakehouse.contracts import load_contracts


def test_repository_contracts_form_one_glue_registry() -> None:
    contracts = load_contracts(Path("platform/contracts"))

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
    assert contracts.domain("research").database == "analytics_research"


def test_primary_keys_compile_to_iceberg_identifier_fields() -> None:
    contracts = load_contracts()
    curated = contracts.curated_product("arxiv").table("papers")
    landing = contracts.source("arxiv").table("oai_records_raw")

    assert iceberg_schema(curated.columns, curated.primary_key).identifier_field_ids == [1]
    assert iceberg_schema(landing.columns, landing.primary_key).identifier_field_ids == [14, 1]


def test_arxiv_publication_marker_has_manifest_lineage() -> None:
    table = load_contracts().source("arxiv").table("oai_publications")

    assert table.primary_key == ("datestamp_date",)
    assert {column.name for column in table.columns} == {
        "datestamp_date",
        "raw_manifest_key",
        "raw_manifest_sha256",
        "page_count",
        "record_count",
        "published_at",
    }


def test_contract_layout_excludes_cloud_identity_and_maintenance_policy() -> None:
    root = Path("platform/contracts")
    files = {path.relative_to(root).as_posix() for path in root.rglob("*.yaml")}

    assert "access.yaml" not in files
    assert "maintenance.yaml" not in files
    assert files == {
        "curated/arxiv.yaml",
        "curated/github.yaml",
        "domains/engineering.yaml",
        "domains/research.yaml",
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
    for path in Path("platform/contracts").rglob("*.yaml"):
        source = path.read_text(encoding="utf-8").lower()
        assert "password:" not in source
        assert "secret:" not in source
        assert "access_key:" not in source
