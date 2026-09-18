"""Materialize deterministic shadow decisions from certified feature snapshots."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime

from pyspark.sql import SparkSession
from t0_trading.configuration import (
    DecisionVersion,
    OutcomeVersion,
    StrategyVersion,
    TradingVersion,
)
from t0_trading.decisions import StrategyDecision, replay_decisions
from t0_trading.features import FeatureSnapshot

from emr_jobs.common.contracts import spark_schema
from emr_jobs.common.iceberg import qualified_name
from emr_jobs.t0_trading.iceberg import insert_missing, require_compatible
from lakehouse.contracts.curated import CuratedProductContract


def _rows(
    decisions: Sequence[StrategyDecision],
    *,
    processed_at: datetime,
) -> tuple[dict[str, object], ...]:
    return tuple(
        {
            "schema_version": decision.schema_version,
            "decision_version": decision.decision_version,
            "decision_configuration_sha256": decision.decision_configuration_sha256,
            "strategy": decision.strategy,
            "strategy_version": decision.strategy_version,
            "strategy_configuration_sha256": decision.strategy_configuration_sha256,
            "outcome_version": decision.outcome_version,
            "outcome_configuration_sha256": decision.outcome_configuration_sha256,
            "feature_version": decision.feature_version,
            "feature_configuration_sha256": decision.feature_configuration_sha256,
            "feature_snapshot_sha256": decision.feature_snapshot_sha256,
            "strategy_score_sha256": decision.strategy_score_sha256,
            "symbol": decision.symbol,
            "trade_date": decision.trade_date,
            "decision_at": decision.decision_at,
            "market_session": decision.market_session.value,
            "horizon_seconds": decision.horizon_seconds,
            "signed_score": decision.signed_score,
            "minimum_strength": decision.minimum_strength,
            "action": decision.action,
            "decision_reasons_json": json.dumps(
                decision.reasons,
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            "processed_at": processed_at,
            "decision_sha256": decision.sha256,
        }
        for decision in decisions
    )


def publish(
    spark: SparkSession,
    *,
    product: CuratedProductContract,
    snapshots: Sequence[FeatureSnapshot],
    configuration: TradingVersion,
    strategy_policy: StrategyVersion,
    outcome_policy: OutcomeVersion,
    decision_policy: DecisionVersion,
) -> tuple[StrategyDecision, ...]:
    """Replay and idempotently publish one certified day's shadow decisions."""
    decisions = replay_decisions(
        snapshots,
        configuration,
        strategy_policy,
        outcome_policy,
        decision_policy,
    )
    contract = product.table("shadow_decisions")
    frame = spark.createDataFrame(
        _rows(decisions, processed_at=datetime.now(UTC)),
        spark_schema(contract),
    )
    view = "t0_shadow_decision_candidates"
    target = qualified_name(product.table_identifier("shadow_decisions"))
    frame.createOrReplaceTempView(view)
    require_compatible(
        spark,
        view=view,
        target=target,
        keys=contract.primary_key,
        fingerprint="decision_sha256",
    )
    insert_missing(
        spark,
        view=view,
        target=target,
        keys=contract.primary_key,
    )
    return decisions
