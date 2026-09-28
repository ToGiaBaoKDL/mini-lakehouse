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
    scope = Path("lakehouse/emr/src/emr_jobs/market_data/rest_scope.py").read_text(encoding="utf-8")
    contract = Path("t0-trading/src/t0_trading/capture/rest_contract.py").read_text(
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
    assert '"get_securities_summary_historical"' in contract
    assert '"get_securities_info_by_index"' in contract
    assert "for capability in REST_CAPABILITIES" in scope
    assert '"membership_indices": expected_membership_indices' in scope
    assert "DATE '{source_date}' AS trade_date" not in source
    assert "to_date(substr(get_json_object(record_json, '$.trading_date'), 1, 10)" in source
    assert "ssi_index_minute_ohlc" in source
    assert 'product.table_identifier("index_bars_1m")' in source
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
    assert '"index_snapshots"' not in job
    assert '"index_bars_1m"' in job
    assert 'F.sha2("message_json", 256)' in landing
    assert "duplicate_keys" in landing
    assert "batch_count_mismatches" in landing
    assert '"error_type": manifest.error_type' in landing
    assert "TradeMessage" in curated
    assert "QuoteMessage" in curated
    assert "FULL_TOP_3" in curated
    assert "size(bid_prices) != 10" in curated
    assert "exists(slice(bid_prices, 4, 7), value -> value != 0)" in curated
    assert "IntervalMessage" not in curated
    assert "ForeignRoomMessage" not in curated
    assert "WHEN NOT MATCHED THEN INSERT *" in curated


def test_market_data_stream_routes_only_supported_symbol_and_status_capture() -> None:
    landing = Path("lakehouse/emr/src/emr_jobs/market_data/stream_landing.py").read_text(
        encoding="utf-8"
    )
    curated = Path("lakehouse/emr/src/emr_jobs/market_data/stream_curated.py").read_text(
        encoding="utf-8"
    )

    assert 'F.col("subscription_context") == F.lit("symbols")' in landing
    assert 'F.col("symbol").isin(*manifest.symbols)' in landing
    assert "manifest.interval_symbols or manifest.symbols" in landing
    assert "(symbol_scope & ~allowed_symbol_scope)" in landing
    assert "(market_scope & ~market_in_scope)" in landing
    assert 'F.col("subscription_context") == F.lit("indices")' not in landing
    assert "manifest.indices" not in landing

    assert curated.count("subscription_context = 'symbols'") == 2
    assert "subscription_context = 'indices'" not in curated
    assert "ssi_stream_index_candidates" not in curated
    assert "'ssi_stream_index' AS source_kind" not in curated
    assert 'product.table_identifier("index_snapshots")' not in curated
    assert '"yyyy/MM/dd HH:mm:ss[.SSSSSS]"' in curated
    assert curated.count('_ssi_local_timestamp("$.trading_time")') == 2
    assert "'yyyy/MM/dd HH:mm:ss')" not in curated
    assert "subscription_context = 'markets'" in curated
    assert "ssi_stream_market_status_candidates" in curated
    assert "SSI Stream market-status trading date is invalid" in curated
    assert 'product.table_identifier("market_status_events")' in curated


def test_market_data_stream_materializes_features_with_the_shared_t0_core() -> None:
    job = Path("lakehouse/emr/src/emr_jobs/market_data/stream_job.py").read_text(encoding="utf-8")
    certifications = Path("lakehouse/emr/src/emr_jobs/t0_trading/certifications.py").read_text(
        encoding="utf-8"
    )
    features = Path("lakehouse/emr/src/emr_jobs/t0_trading/features.py").read_text(encoding="utf-8")
    outcomes = Path("lakehouse/emr/src/emr_jobs/t0_trading/outcomes.py").read_text(encoding="utf-8")
    research = Path("lakehouse/emr/src/emr_jobs/t0_trading/research.py").read_text(encoding="utf-8")
    image = Path("lakehouse/emr/Dockerfile").read_text(encoding="utf-8")

    assert 'curated_product("t0_trading")' in job
    assert "parse_configuration" in job
    assert "resolve_outcomes" in job
    assert "resolve_candidate_arbitration" in job
    assert "trading_config_uri" in job
    assert "certify_market_day" in job
    assert "covers_trading_window" not in job
    assert job.index("publish_landing(") < job.index("publish_certification(")
    assert job.index("publish_certification(") < job.index("publish_features(")
    assert job.index("publish_features(") < job.index("publish_outcomes(")
    assert job.index("publish_outcomes(") < job.index("publish_research(")
    assert job.index('certification.status != "passed"') < job.index("publish_research(")
    assert 'certification.status != "passed"' in job
    assert 'product.table("market_day_certifications")' in certifications
    assert "source.manifest_count < target.manifest_count" in certifications
    assert "source.manifest_count > target.manifest_count" in certifications
    assert "WHEN NOT MATCHED THEN INSERT *" in certifications
    assert "replay_features" in features
    assert "build_feature_audit" in features
    assert 'product.table("outcome_labels")' in outcomes
    assert "label_outcomes" in outcomes
    assert "build_outcome_audit" in outcomes
    assert "outcome_sha256" in outcomes
    assert outcomes.count("require_compatible(") == 1
    assert outcomes.count("insert_missing(") == 1
    assert "build_decision_contexts" in research
    assert "build_decision_contexts_from_observations" in research
    assert "score_buy_first_baselines" in research
    assert "evaluate_buy_first_baselines" in research
    assert 'table="decision_contexts"' in research
    assert 'table="strategy_candidates"' in research
    assert 'table="candidate_arbitrations"' in research
    assert 'table="strategy_evaluations"' in research
    research_publication = research.split("def publish(", maxsplit=1)[1]
    assert research.count("require_compatible(") == 1
    assert research_publication.count("_prepare(") == 4
    assert research_publication.count("insert_missing(") == 4
    assert research_publication.rfind("_prepare(") < research_publication.find("insert_missing(")
    assert (
        research_publication.find(
            'view="t0_decision_context_candidates"',
            research_publication.find("insert_missing("),
        )
        < research_publication.find(
            'view="t0_strategy_candidate_candidates"',
            research_publication.find("insert_missing("),
        )
        < research_publication.find(
            'view="t0_candidate_arbitration_candidates"',
            research_publication.find("insert_missing("),
        )
        < research_publication.find(
            'view="t0_strategy_evaluation_candidates"',
            research_publication.find("insert_missing("),
        )
    )
    assert "publish_decisions" not in job
    assert "snapshot.sha256" in features
    immutable = Path("lakehouse/emr/src/emr_jobs/t0_trading/iceberg.py").read_text(encoding="utf-8")
    landing_reader = Path("lakehouse/emr/src/emr_jobs/t0_trading/landing.py").read_text(
        encoding="utf-8"
    )
    assert "WHEN NOT MATCHED THEN INSERT *" in immutable
    assert 'orderBy("received_at", "stream_session_id", "receive_sequence")' in landing_reader
    assert landing_reader.count('"subscription_context"') == 1
    assert "subscription_context=_subscription_context(row.subscription_context)" in landing_reader
    publication = features.split("def publish(", maxsplit=1)[1]
    assert publication.count("require_compatible(") == 2
    assert publication.count("insert_missing(") == 2
    assert publication.rfind("require_compatible(") < publication.find("insert_missing(")
    assert publication.find("view=window_view", publication.find("insert_missing(")) < (
        publication.find("view=snapshot_view", publication.find("insert_missing("))
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

    assert "price > 0 AND quantity > 0 AND raw_side IN ('B', 'S', 'U')" in curated
    assert "WHEN price = 0 AND quantity = 0 AND raw_side = 'U' THEN 'AUCTION_STATE'" in curated
    assert "WHEN 'S' THEN 'SELL' END AS aggressor_side" in curated
    assert "OR cumulative_volume IS NULL OR record_kind IS NULL" in curated
    assert "WHERE record_kind = 'EXECUTABLE'" in curated
