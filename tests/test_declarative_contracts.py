from pathlib import Path

import pytest
from lakehouse.catalog.schema import iceberg_schema
from lakehouse.contracts.base import ColumnContract
from pyiceberg.types import DecimalType

from lakehouse.contracts import load_contracts


def test_repository_contracts_form_one_glue_registry() -> None:
    contracts = load_contracts(Path("lakehouse/contracts"))

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
    assert contracts.source("ssi_fastconnect_rest").raw_object_prefix == (
        "api/ssi_fastconnect_rest/raw"
    )
    assert contracts.source("ssi_fastconnect_stream").raw_object_prefix == (
        "stream/ssi_fastconnect_stream/raw"
    )


def test_primary_keys_compile_to_iceberg_identifier_fields() -> None:
    contracts = load_contracts()
    curated = contracts.curated_product("arxiv").table("papers")
    landing = contracts.source("arxiv").table("oai_records_raw")

    assert iceberg_schema(curated.columns, curated.primary_key).identifier_field_ids == [1]
    assert iceberg_schema(landing.columns, landing.primary_key).identifier_field_ids == [14, 1]


def test_decimal_contract_compiles_with_exact_precision_and_scale() -> None:
    table = load_contracts().curated_product("market_data").table("trade_ticks")
    price = next(
        field
        for field in iceberg_schema(table.columns, table.primary_key).fields
        if field.name == "price"
    )

    assert price.field_type == DecimalType(18, 0)


@pytest.mark.parametrize(
    "payload",
    [
        {"data_type": "decimal"},
        {"data_type": "decimal", "precision": 18},
        {"data_type": "decimal", "precision": 4, "scale": 5},
        {"data_type": "long", "precision": 18, "scale": 0},
    ],
)
def test_decimal_contract_rejects_ambiguous_or_misplaced_parameters(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        ColumnContract.model_validate(
            {
                "field_id": 1,
                "name": "amount",
                "required": True,
                "description": "Exact numeric fixture.",
                **payload,
            }
        )


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


def test_market_data_contracts_are_ssi_only_replayable_and_conformed() -> None:
    contracts = load_contracts()
    product = contracts.curated_product("market_data")

    assert {source.name for source in contracts.sources if "ssi_fastconnect" in source.name} == {
        "ssi_fastconnect_rest",
        "ssi_fastconnect_stream",
    }
    assert {table.name for table in product.tables} == {
        "securities",
        "daily_security_summaries",
        "intraday_bars_1m",
        "trade_ticks",
        "quote_snapshots",
        "quote_levels",
        "index_snapshots",
        "market_status_events",
        "corporate_actions",
    }
    assert all(column.data_type != "double" for table in product.tables for column in table.columns)
    assert product.table("trade_ticks").primary_key == (
        "stream_session_id",
        "receive_sequence",
    )
    assert product.table("quote_levels").primary_key == (
        "stream_session_id",
        "receive_sequence",
        "side",
        "level",
    )
    assert "available_at" in {column.name for column in product.table("intraday_bars_1m").columns}

    market_contracts = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (
            Path("lakehouse/contracts/sources/ssi_fastconnect_rest.yaml"),
            Path("lakehouse/contracts/sources/ssi_fastconnect_stream.yaml"),
            Path("lakehouse/contracts/curated/market_data.yaml"),
        )
    ).lower()
    assert "dnse" not in market_contracts
    assert "historical_1m" not in market_contracts


def test_contract_layout_excludes_cloud_identity_and_maintenance_policy() -> None:
    root = Path("lakehouse/contracts")
    files = {path.relative_to(root).as_posix() for path in root.rglob("*.yaml")}

    assert "access.yaml" not in files
    assert "maintenance.yaml" not in files
    assert files == {
        "curated/arxiv.yaml",
        "curated/github.yaml",
        "curated/market_data.yaml",
        "domains/engineering.yaml",
        "domains/research.yaml",
        "sources/arxiv.yaml",
        "sources/github_archive.yaml",
        "sources/ssi_fastconnect_rest.yaml",
        "sources/ssi_fastconnect_stream.yaml",
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
    for path in Path("lakehouse/contracts").rglob("*.yaml"):
        source = path.read_text(encoding="utf-8").lower()
        assert "password:" not in source
        assert "secret:" not in source
        assert "access_key:" not in source
