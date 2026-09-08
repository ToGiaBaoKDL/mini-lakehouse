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


def test_emr_uses_utc_for_timestamp_transport() -> None:
    spark = Path("lakehouse/emr/src/emr_jobs/common/spark.py").read_text(encoding="utf-8")

    assert '.config("spark.sql.session.timeZone", "UTC")' in spark


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
    assert 'scopes.get("get_securities_summary_historical", set())' in source
    assert "return False" not in source


def test_market_data_manifest_uses_the_source_owned_raw_prefix() -> None:
    job = Path("lakehouse/emr/src/emr_jobs/market_data/rest_job.py").read_text(encoding="utf-8")
    manifest = Path("lakehouse/emr/src/emr_jobs/market_data/rest_manifest.py").read_text(
        encoding="utf-8"
    )

    assert "source.raw_object_prefix" in job
    assert "RAW_PREFIX" not in manifest


def test_market_data_stream_replay_uses_verified_sdk_models_and_top_three_quotes() -> None:
    job = Path("lakehouse/emr/src/emr_jobs/market_data/stream_job.py").read_text(encoding="utf-8")
    capture = Path("lakehouse/emr/src/emr_jobs/market_data/stream_capture.py").read_text(
        encoding="utf-8"
    )
    landing = Path("lakehouse/emr/src/emr_jobs/market_data/stream_landing.py").read_text(
        encoding="utf-8"
    )
    curated = Path("lakehouse/emr/src/emr_jobs/market_data/stream_curated.py").read_text(
        encoding="utf-8"
    )

    assert "source.raw_object_prefix" in job
    assert "StreamSessionReader.from_uri" in capture
    assert "stream_manifest_uris" in capture
    assert not Path("lakehouse/emr/src/emr_jobs/market_data/stream_manifest.py").exists()
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


def test_market_data_stream_materializes_features_with_the_shared_t0_core() -> None:
    job = Path("lakehouse/emr/src/emr_jobs/market_data/stream_job.py").read_text(encoding="utf-8")
    features = Path("lakehouse/emr/src/emr_jobs/t0_trading/features.py").read_text(encoding="utf-8")
    image = Path("lakehouse/emr/Dockerfile").read_text(encoding="utf-8")

    assert 'curated_product("t0_trading")' in job
    assert "parse_configuration" in job
    assert "trading_config_uri" in job
    assert "replay_features" in features
    assert "build_feature_audit" in features
    assert 'orderBy("receive_sequence")' in features
    assert "snapshot.sha256" in features
    assert "WHEN NOT MATCHED THEN INSERT *" in features
    publication = features.split("def publish(", maxsplit=1)[1]
    assert publication.count("_require_compatible(") == 2
    assert publication.count("_insert_missing(") == 2
    assert publication.rfind("_require_compatible(") < publication.find("_insert_missing(")
    assert publication.find("view=window_view", publication.find("_insert_missing(")) < (
        publication.find("view=snapshot_view", publication.find("_insert_missing("))
    )
    assert "t0-trading/config/trading.yaml /output/trading.yaml" in image


def test_emr_release_tracks_its_t0_feature_inputs() -> None:
    workflow = Path(".github/workflows/release-emr-jobs.yml").read_text(encoding="utf-8")

    assert "- t0-trading/config/trading.yaml" in workflow
    assert "- t0-trading/pyproject.toml" in workflow
    assert "- t0-trading/src/**" in workflow


def test_market_data_stream_replay_separates_auction_state_from_executable_trades() -> None:
    curated = Path("lakehouse/emr/src/emr_jobs/market_data/stream_curated.py").read_text(
        encoding="utf-8"
    )

    assert "price > 0 AND quantity > 0 AND raw_side IN ('B', 'S')" in curated
    assert "WHEN price = 0 AND quantity = 0 AND raw_side = 'U' THEN 'AUCTION_STATE'" in curated
    assert "OR cumulative_volume IS NULL OR record_kind IS NULL" in curated
    assert "WHERE record_kind = 'EXECUTABLE'" in curated
