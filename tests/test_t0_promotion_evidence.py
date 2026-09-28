"""Daily promotion evidence is derived from selected shadow records, not raw candidates."""

import hashlib
from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import cast

import pytest
from t0_trading.arbitration import (
    CandidateArbitration,
    ShadowArbitrationAuditReport,
    arbitrate_candidates,
)
from t0_trading.configuration import load_configuration
from t0_trading.controls import public_vndirect_dta_costs
from t0_trading.numeric import basis_points
from t0_trading.outcomes import OutcomeLabel
from t0_trading.promotion import evaluate_arbitrated_session
from t0_trading.strategy.baselines import (
    BASELINE_GROUP_NAMES,
    BASELINE_NAMES,
    BaselineCandidate,
    BaselineName,
    GroupEvidence,
)

CONFIGURATION = Path("t0-trading/config/trading.yaml")
TRADE_DATE = date(2026, 9, 28)
DECISION_AT = datetime(2026, 9, 28, 2, 30, tzinfo=UTC)


def _journal_sha(records: Iterable[BaselineCandidate | CandidateArbitration]) -> str:
    digest = hashlib.sha256()
    for record in records:
        digest.update(record.canonical_bytes())
        digest.update(b"\n")
    return digest.hexdigest()


def _candidate(strategy: BaselineName, symbol: str) -> BaselineCandidate:
    return BaselineCandidate(
        strategy=strategy,
        symbol=symbol,
        trade_date=TRADE_DATE,
        decision_at=DECISION_AT,
        feature_snapshot_sha256=hashlib.sha256(f"feature:{symbol}".encode()).hexdigest(),
        peer_feature_snapshot_sha256=(
            hashlib.sha256(f"peer:{symbol}".encode()).hexdigest()
            if strategy == "vic_vhm_relative"
            else None
        ),
        context_snapshot_sha256="1" * 64,
        context_version="decision-context-v3",
        context_configuration_sha256="2" * 64,
        context_data_mode="LIVE",
        market_regime="TREND_UP",
        groups=cast(
            tuple[GroupEvidence, GroupEvidence, GroupEvidence],
            tuple(
                GroupEvidence(name=name, strength=Decimal("0.8"))
                for name in BASELINE_GROUP_NAMES[strategy]
            ),
        ),
        strength=Decimal("0.8"),
        block_reasons=(),
    )


def _outcome(candidate: BaselineCandidate) -> OutcomeLabel:
    entry, exit_price = Decimal(100), Decimal(101)
    return OutcomeLabel(
        outcome_version="top3-taker-markout-v1",
        outcome_configuration_sha256="3" * 64,
        feature_version="microstructure-v1",
        feature_configuration_sha256="4" * 64,
        feature_snapshot_sha256=candidate.feature_snapshot_sha256,
        stream_session_id="session-1",
        symbol=candidate.symbol,
        trade_date=TRADE_DATE,
        decision_at=DECISION_AT,
        action="BUY",
        horizon_seconds=300,
        order_quantity=100,
        entry_at=DECISION_AT + timedelta(milliseconds=500),
        horizon_at=DECISION_AT + timedelta(seconds=300),
        entry_quote_received_at=DECISION_AT,
        entry_receive_sequence=1,
        entry_vwap=entry,
        horizon_quote_received_at=DECISION_AT + timedelta(seconds=299),
        horizon_receive_sequence=2,
        horizon_vwap=exit_price,
        gross_return_bps=basis_points(exit_price - entry, entry),
        reasons=(),
    )


def _evidence():
    configuration = load_configuration(CONFIGURATION)
    policy = configuration.resolve_candidate_arbitration(TRADE_DATE)
    gate = configuration.resolve_promotion_gate(TRADE_DATE)
    assert policy is not None and gate is not None
    candidates = tuple(
        _candidate(strategy, symbol) for symbol in ("VHM", "VIC") for strategy in BASELINE_NAMES
    )
    arbitrations = arbitrate_candidates(candidates, policy)
    selected = tuple(
        candidate
        for candidate in candidates
        if next(item for item in arbitrations if item.candidate_sha256 == candidate.sha256).status
        == "SELECTED"
    )
    audit = ShadowArbitrationAuditReport(
        trade_date=TRADE_DATE,
        baseline_version="buy-first-baselines-v3",
        arbitration_version=policy.version,
        arbitration_configuration_sha256=policy.sha256,
        stream_session_ids=("session-1",),
        capture_evidence_sha256="5" * 64,
        capture_message_count=100,
        gap_count=0,
        manifest_sha256="6" * 64,
        candidate_count=len(candidates),
        candidate_sha256=_journal_sha(candidates),
        arbitration_count=len(arbitrations),
        arbitration_sha256=_journal_sha(arbitrations),
    )
    costs = public_vndirect_dta_costs(
        TRADE_DATE,
        checked_at=datetime(2026, 9, 22, tzinfo=UTC),
    )
    return candidates, arbitrations, tuple(_outcome(item) for item in selected), costs, audit, gate


def test_daily_evidence_scores_only_selected_arbitrations() -> None:
    evidence = _evidence()
    report = evaluate_arbitrated_session(
        *evidence,
        capture_evidence_sha256=evidence[4].capture_evidence_sha256,
    )

    selected = [item for item in report.evaluations if item.selected_count]
    assert {(item.strategy, item.symbol) for item in selected} == {
        ("vic_vhm_relative", "VIC"),
        ("vic_vhm_relative", "VHM"),
    }
    assert sum(item.selected_count for item in report.evaluations) == 2
    assert all(item.eligible_outcome_count == item.selected_count for item in report.evaluations)


def test_daily_evidence_rejects_records_that_differ_from_proven_journal() -> None:
    candidates, arbitrations, outcomes, costs, audit, gate = _evidence()
    mismatched = audit.model_copy(update={"candidate_sha256": "f" * 64})

    with pytest.raises(ValueError, match="proven shadow journals"):
        evaluate_arbitrated_session(
            candidates,
            arbitrations,
            outcomes,
            costs,
            mismatched,
            gate,
            capture_evidence_sha256=audit.capture_evidence_sha256,
        )
