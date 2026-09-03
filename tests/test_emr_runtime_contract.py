import ast
from pathlib import Path


def test_emr_artifacts_are_built_in_the_pinned_runtime() -> None:
    dockerfile = Path("lakehouse/emr/Dockerfile").read_text(encoding="utf-8")

    assert "amazonlinux:2023-minimal@sha256:" in dockerfile
    assert "dnf install -y --setopt=install_weak_deps=0 python3.11" in dockerfile
    assert ":latest" not in dockerfile
    assert "venv-pack" in dockerfile
    assert "python.tar.gz" in dockerfile

    makefile = Path("make/data.mk").read_text(encoding="utf-8")
    package = Path("lakehouse/emr/release/package").read_text(encoding="utf-8")
    assert "lakehouse/emr/.venv/lib/python" not in makefile
    assert "lakehouse/emr/release/package" in makefile
    assert 'lakehouse/emr/Dockerfile"' in package
    assert "emr_jobs.zip" not in makefile


def test_emr_artifact_sources_are_python_311_compatible() -> None:
    runtime_sources = (
        *Path("lakehouse/catalog/src/lakehouse").rglob("*.py"),
        *Path("lakehouse/emr/src").rglob("*.py"),
        *Path("lakehouse/emr/entrypoints").glob("*.py"),
    )

    for path in runtime_sources:
        ast.parse(
            path.read_text(encoding="utf-8"),
            filename=str(path),
            feature_version=(3, 11),
        )


def test_emr_entrypoints_are_thin_python_adapters() -> None:
    entrypoints = sorted(Path("lakehouse/emr/entrypoints").glob("*.py"))
    assert {path.name for path in entrypoints} == {
        "arxiv_metadata.py",
        "github_archive.py",
        "iceberg_maintenance.py",
        "market_data_rest.py",
        "market_data_stream.py",
    }
    for path in entrypoints:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        assert any(
            isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("emr_jobs.")
            for node in tree.body
        )
        assert not any(
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and ("CREATE TABLE" in node.value or "CREATE DATABASE" in node.value)
            for node in ast.walk(tree)
        )


def test_emr_uses_one_shared_iceberg_catalog_boundary() -> None:
    common = Path("lakehouse/emr/src/emr_jobs/common/iceberg.py").read_text(encoding="utf-8")
    entrypoints = "\n".join(
        path.read_text(encoding="utf-8") for path in Path("lakehouse/emr/entrypoints").glob("*.py")
    )
    terraform = Path("infra/terraform/aws/modules/emr_serverless/variables.tf").read_text(
        encoding="utf-8"
    )

    assert "from lakehouse.catalog import CATALOG_NAME" in common
    assert "catalog_name" not in entrypoints
    assert 'default     = "glue"' in terraform


def test_spark_contract_adapter_supports_every_declared_numeric_type() -> None:
    adapter = Path("lakehouse/emr/src/emr_jobs/common/contracts.py").read_text(encoding="utf-8")

    assert '"double": DoubleType()' in adapter
    assert 'if column.data_type == "decimal"' in adapter
    assert "return DecimalType(column.precision, column.scale)" in adapter


def test_market_data_publication_uses_sdk_summary_fields() -> None:
    source = Path("lakehouse/emr/src/emr_jobs/market_data/rest_curated.py").read_text(
        encoding="utf-8"
    )

    for sdk_field, curated_field in {
        "total_deal": "deal_volume",
        "total_deal_value": "deal_value",
        "total_foreign_buy": "foreign_buy_volume",
        "total_foreign_buy_value": "foreign_buy_value",
        "total_foreign_sell": "foreign_sell_volume",
        "total_foreign_sell_value": "foreign_sell_value",
        "remain_foreign_room": "remaining_foreign_room",
        "total_foreign_room": "total_foreign_room",
        "open_interest": "open_interest",
        "settlement_price": "settlement_price",
    }.items():
        assert f"'$.{sdk_field}'" in source
        assert f"AS {curated_field}" in source

    assert "CAST(NULL AS bigint) AS foreign_buy_volume" not in source
    assert "CAST(NULL AS bigint) AS deal_volume" not in source


def test_market_data_manifest_uses_the_source_owned_raw_prefix() -> None:
    job = Path("lakehouse/emr/src/emr_jobs/market_data/rest_job.py").read_text(encoding="utf-8")
    manifest = Path("lakehouse/emr/src/emr_jobs/market_data/rest_manifest.py").read_text(
        encoding="utf-8"
    )

    assert "source.raw_object_prefix" in job
    assert "RAW_PREFIX" not in manifest


def test_market_data_stream_replay_uses_verified_sdk_models_and_top_three_quotes() -> None:
    job = Path("lakehouse/emr/src/emr_jobs/market_data/stream_job.py").read_text(encoding="utf-8")
    landing = Path("lakehouse/emr/src/emr_jobs/market_data/stream_landing.py").read_text(
        encoding="utf-8"
    )
    curated = Path("lakehouse/emr/src/emr_jobs/market_data/stream_curated.py").read_text(
        encoding="utf-8"
    )

    assert "source.raw_object_prefix" in job
    assert '"trade_ticks", "quote_snapshots", "quote_levels"' in job
    assert 'F.sha2("message_json", 256)' in landing
    assert "duplicate_keys" in landing
    assert "batch_count_mismatches" in landing
    assert "TradeMessage" in curated
    assert "QuoteMessage" in curated
    assert "FULL_TOP_3" in curated
    assert "size(bid_prices) != 10" in curated
    assert "exists(slice(bid_prices, 4, 7), value -> value != 0)" in curated
    assert "IntervalMessage" not in curated
    assert "ForeignRoomMessage" not in curated
    assert "WHEN NOT MATCHED THEN INSERT *" in curated
