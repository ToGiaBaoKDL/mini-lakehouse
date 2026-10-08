"""Materialize context-aware buy-first research facts from certified evidence."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from t0_trading.arbitration import CandidateArbitration, arbitrate_candidates
from t0_trading.capture.membership import BreadthMembershipSnapshot
from t0_trading.configuration import (
    BreadthVersion,
    CandidateArbitrationVersion,
    ContextVersion,
    RegimeVersion,
    TradingVersion,
)
from t0_trading.context import (
    DecisionContext,
    IndexObservation,
    build_decision_contexts,
    build_decision_contexts_from_observations,
)
from t0_trading.features import FeatureSnapshot
from t0_trading.identity import canonical_json
from t0_trading.market.events import StreamEnvelope
from t0_trading.outcomes import OutcomeLabel
from t0_trading.simulation.presets import (
    PUBLIC_VNDIRECT_DTA_CHECKED_AT,
    public_vndirect_dta_costs,
)
from t0_trading.strategy.baseline_audit import BaselineAuditReport, evaluate_buy_first_baselines
from t0_trading.strategy.baselines import BaselineCandidate, score_buy_first_baselines

from lakehouse.contracts.curated import CuratedProductContract

if TYPE_CHECKING:
    from pyspark.sql import SparkSession


def _json(value: object) -> str:
    return canonical_json(value).decode("utf-8")


def _historical_index_observations(
    spark: SparkSession,
    *,
    market_product: CuratedProductContract,
    snapshots: Sequence[FeatureSnapshot],
    configuration: TradingVersion,
    context_policy: ContextVersion,
) -> tuple[IndexObservation, ...]:
    from emr_jobs.common.iceberg import qualified_name

    trade_date = snapshots[0].trade_date
    target = qualified_name(market_product.table_identifier("index_bars_1m"))
    codes = spark.createDataFrame(
        [(value,) for value in configuration.market.indices], "index_code string"
    )
    codes.createOrReplaceTempView("t0_required_context_indices")
    rows = spark.sql(
        f"""
        SELECT index_code, bar_start, close_value, source_record_sha256
        FROM (
            SELECT bars.*, row_number() OVER (
                PARTITION BY index_code, bar_start
                ORDER BY revision DESC, available_at DESC
            ) AS _rank
            FROM {target} bars
            JOIN t0_required_context_indices required USING (index_code)
            WHERE trade_date = DATE '{trade_date.isoformat()}' AND is_final
        ) ranked
        WHERE _rank = 1
        ORDER BY bar_start, index_code
        """
    ).collect()
    availability_delay = timedelta(seconds=context_policy.historical_proxy_interval_seconds)
    return tuple(
        IndexObservation(
            index=row["index_code"],
            value=row["close_value"],
            observed_at=(
                row["bar_start"].replace(tzinfo=UTC) + availability_delay
                if row["bar_start"].tzinfo is None
                else row["bar_start"].astimezone(UTC) + availability_delay
            ),
            source_kind="ssi_rest_index_1m_historical",
            source_record_sha256=row["source_record_sha256"],
        )
        for row in rows
    )


def _contexts(
    spark: SparkSession,
    *,
    market_product: CuratedProductContract,
    snapshots: Sequence[FeatureSnapshot],
    stream_envelopes: Iterable[StreamEnvelope],
    configuration: TradingVersion,
    context_policy: ContextVersion,
    breadth_policy: BreadthVersion | None = None,
    breadth_membership: BreadthMembershipSnapshot | None = None,
    regime_policy: RegimeVersion | None = None,
) -> tuple[DecisionContext, ...]:
    live = build_decision_contexts(
        snapshots,
        stream_envelopes,
        configuration,
        context_policy,
        breadth_policy=breadth_policy,
        breadth_membership=breadth_membership,
        regime_policy=regime_policy,
    )
    if regime_policy is not None or any(
        item.value is not None for context in live for item in context.indices
    ):
        return live
    historical = _historical_index_observations(
        spark,
        market_product=market_product,
        snapshots=snapshots,
        configuration=configuration,
        context_policy=context_policy,
    )
    if not historical:
        return live
    proxy = build_decision_contexts_from_observations(
        snapshots,
        historical,
        configuration,
        context_policy,
        data_mode="HISTORICAL_PROXY",
    )
    return tuple(
        context.model_copy(update={"breadth": live_context.breadth})
        for context, live_context in zip(proxy, live, strict=True)
    )


def _context_rows(
    contexts: Sequence[DecisionContext], processed_at: datetime
) -> tuple[dict[str, object], ...]:
    return tuple(
        {
            "schema_version": context.schema_version,
            "context_version": context.context_version,
            "context_configuration_sha256": context.context_configuration_sha256,
            "feature_configuration_sha256": context.feature_configuration_sha256,
            "trade_date": context.trade_date,
            "decision_at": context.decision_at,
            "data_mode": context.data_mode,
            "market_confirmation_strength": context.market_confirmation_strength,
            "regime": context.regime,
            "market_basis": context.market_basis,
            "market_reference_index": context.market_reference_index,
            "regime_policy_version": context.regime_policy_version,
            "regime_policy_sha256": context.regime_policy_sha256,
            "reasons_json": _json(context.reasons),
            "zones_json": _json([item.model_dump(mode="json") for item in context.zones]),
            "indices_json": _json([item.model_dump(mode="json") for item in context.indices]),
            "breadth_json": _json([item.model_dump(mode="json") for item in context.breadth])
            if context.breadth
            else None,
            "processed_at": processed_at,
            "context_sha256": context.sha256,
            "market_statuses_json": _json(
                [item.model_dump(mode="json") for item in context.market_statuses]
            ),
        }
        for context in contexts
    )


def _candidate_rows(
    candidates: Sequence[BaselineCandidate], processed_at: datetime
) -> tuple[dict[str, object], ...]:
    return tuple(
        {
            "schema_version": candidate.schema_version,
            "baseline_version": candidate.baseline_version,
            "strategy": candidate.strategy,
            "symbol": candidate.symbol,
            "trade_date": candidate.trade_date,
            "decision_at": candidate.decision_at,
            "feature_snapshot_sha256": candidate.feature_snapshot_sha256,
            "peer_feature_snapshot_sha256": candidate.peer_feature_snapshot_sha256,
            "context_snapshot_sha256": candidate.context_snapshot_sha256,
            "context_version": candidate.context_version,
            "context_configuration_sha256": candidate.context_configuration_sha256,
            "context_data_mode": candidate.context_data_mode,
            "market_regime": candidate.market_regime,
            "groups_json": _json([item.model_dump(mode="json") for item in candidate.groups]),
            "strength": candidate.strength,
            "is_candidate": candidate.is_candidate,
            "block_reasons_json": _json(candidate.block_reasons),
            "processed_at": processed_at,
            "candidate_sha256": candidate.sha256,
        }
        for candidate in candidates
    )


def _evaluation_rows(
    report: BaselineAuditReport, processed_at: datetime
) -> tuple[dict[str, object], ...]:
    common: dict[str, object] = {
        "baseline_version": report.baseline_version,
        "trade_date": report.trade_date,
        "capture_evidence_sha256": report.capture_evidence_sha256,
        "feature_configuration_sha256": report.feature_configuration_sha256,
        "context_configuration_sha256": report.context_configuration_sha256,
        "context_data_mode": report.context_data_mode,
        "outcome_configuration_sha256": report.outcome_configuration_sha256,
        "cost_policy_sha256": report.cost_policy_sha256,
        "cost_policy_json": _json(report.cost_policy.model_dump(mode="json")),
        "processed_at": processed_at,
        "audit_sha256": report.sha256,
    }
    rows: list[dict[str, object]] = []
    for item in report.evaluations:
        rows.append(
            common
            | {
                "evaluation_scope": "OVERALL",
                "regime": "ALL",
                **item.model_dump(mode="python"),
            }
        )
    for item in report.regime_evaluations:
        rows.append(
            common
            | {
                "evaluation_scope": "REGIME",
                "regime": item.regime,
                "strategy": item.strategy,
                "symbol": item.symbol,
                "horizon_seconds": item.horizon_seconds,
                "observed_count": None,
                "raw_candidate_count": None,
                "candidate_count": item.candidate_count,
                "eligible_outcome_count": item.eligible_outcome_count,
                "positive_gross_count": None,
                "positive_net_count": item.positive_net_count,
                "candidate_rate": None,
                "outcome_coverage_rate": None,
                "average_gross_return_bps": item.average_gross_return_bps,
                "average_conditional_net_return_bps": (item.average_conditional_net_return_bps),
            }
        )
    return tuple(rows)


def _arbitration_rows(
    decisions: Sequence[CandidateArbitration], processed_at: datetime
) -> tuple[dict[str, object], ...]:
    return tuple(
        {
            **decision.model_dump(mode="python"),
            "processed_at": processed_at,
            "arbitration_sha256": decision.sha256,
        }
        for decision in decisions
    )


def _prepare(
    spark: SparkSession,
    *,
    product: CuratedProductContract,
    table: str,
    rows: Sequence[dict[str, object]],
    view: str,
    fingerprint: str,
) -> tuple[str, tuple[str, ...]]:
    from emr_jobs.common.contracts import spark_schema
    from emr_jobs.common.iceberg import qualified_name
    from emr_jobs.t0_trading.iceberg import require_compatible

    contract = product.table(table)
    spark.createDataFrame(rows, spark_schema(contract)).createOrReplaceTempView(view)
    target = qualified_name(product.table_identifier(table))
    require_compatible(
        spark,
        view=view,
        target=target,
        keys=contract.primary_key,
        fingerprint=fingerprint,
    )
    return target, contract.primary_key


def publish(
    spark: SparkSession,
    *,
    market_product: CuratedProductContract,
    product: CuratedProductContract,
    snapshots: Sequence[FeatureSnapshot],
    labels: Sequence[OutcomeLabel],
    stream_envelopes: Iterable[StreamEnvelope],
    configuration: TradingVersion,
    context_policy: ContextVersion,
    arbitration_policy: CandidateArbitrationVersion | None,
    capture_evidence_sha256: str,
    breadth_policy: BreadthVersion | None = None,
    breadth_membership: BreadthMembershipSnapshot | None = None,
    regime_policy: RegimeVersion | None = None,
) -> tuple[
    tuple[DecisionContext, ...],
    tuple[BaselineCandidate, ...],
    tuple[CandidateArbitration, ...],
    BaselineAuditReport,
]:
    """Build and idempotently publish one certified context-aware research matrix."""
    from emr_jobs.t0_trading.iceberg import insert_missing

    contexts = _contexts(
        spark,
        market_product=market_product,
        snapshots=snapshots,
        stream_envelopes=stream_envelopes,
        configuration=configuration,
        context_policy=context_policy,
        breadth_policy=breadth_policy,
        breadth_membership=breadth_membership,
        regime_policy=regime_policy,
    )
    candidates = score_buy_first_baselines(snapshots, contexts)
    if any(
        candidate.context_snapshot_sha256 is None
        or candidate.context_version is None
        or candidate.context_configuration_sha256 is None
        or candidate.context_data_mode is None
        for candidate in candidates
    ):
        raise RuntimeError("materialized strategy candidates require context lineage")
    arbitrations = (
        arbitrate_candidates(candidates, arbitration_policy)
        if arbitration_policy is not None
        else ()
    )
    report = evaluate_buy_first_baselines(
        candidates,
        labels,
        public_vndirect_dta_costs(
            snapshots[0].trade_date,
            checked_at=PUBLIC_VNDIRECT_DTA_CHECKED_AT,
        ),
        capture_evidence_sha256=capture_evidence_sha256,
    )
    if report.context_configuration_sha256 is None or report.context_data_mode is None:
        raise RuntimeError("materialized strategy evaluations require context lineage")
    processed_at = datetime.now(UTC)
    context_target, context_keys = _prepare(
        spark,
        product=product,
        table="decision_contexts",
        rows=_context_rows(contexts, processed_at),
        view="t0_decision_context_candidates",
        fingerprint="context_sha256",
    )
    candidate_target, candidate_keys = _prepare(
        spark,
        product=product,
        table="strategy_candidates",
        rows=_candidate_rows(candidates, processed_at),
        view="t0_strategy_candidate_candidates",
        fingerprint="candidate_sha256",
    )
    arbitration_prepared = (
        _prepare(
            spark,
            product=product,
            table="candidate_arbitrations",
            rows=_arbitration_rows(arbitrations, processed_at),
            view="t0_candidate_arbitration_candidates",
            fingerprint="arbitration_sha256",
        )
        if arbitrations
        else None
    )
    evaluation_target, evaluation_keys = _prepare(
        spark,
        product=product,
        table="strategy_evaluations",
        rows=_evaluation_rows(report, processed_at),
        view="t0_strategy_evaluation_candidates",
        fingerprint="audit_sha256",
    )
    # Publish lineage inputs first. The terminal evaluation row is the completion
    # boundary: consumers must not treat orphan context/candidate/arbitration rows as a full audit.
    insert_missing(
        spark,
        view="t0_decision_context_candidates",
        target=context_target,
        keys=context_keys,
    )
    insert_missing(
        spark,
        view="t0_strategy_candidate_candidates",
        target=candidate_target,
        keys=candidate_keys,
    )
    if arbitration_prepared is not None:
        arbitration_target, arbitration_keys = arbitration_prepared
        insert_missing(
            spark,
            view="t0_candidate_arbitration_candidates",
            target=arbitration_target,
            keys=arbitration_keys,
        )
    insert_missing(
        spark,
        view="t0_strategy_evaluation_candidates",
        target=evaluation_target,
        keys=evaluation_keys,
    )
    return contexts, candidates, arbitrations, report
