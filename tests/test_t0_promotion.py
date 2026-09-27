"""Promotion evaluates the exact prospective arbitration-selected population."""

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from t0_trading.configuration import load_configuration
from t0_trading.identity import sha256
from t0_trading.promotion import (
    ArbitratedSessionEvaluation,
    ArbitratedSessionReport,
    evaluate_promotion_gate,
)

CONFIGURATION = Path("t0-trading/config/trading.yaml")
FIRST_DATE = date(2026, 9, 28)


def _policies():
    configuration = load_configuration(CONFIGURATION)
    evaluation = configuration.resolve_baseline_evaluation(FIRST_DATE, "PROMOTION")
    gate = configuration.resolve_promotion_gate(FIRST_DATE)
    arbitration = configuration.resolve_candidate_arbitration(FIRST_DATE)
    assert gate is not None and arbitration is not None
    return evaluation, gate, arbitration


def _session(trade_date: date, *, net_bps: str = "5") -> ArbitratedSessionReport:
    _, gate, arbitration = _policies()
    return ArbitratedSessionReport(
        trade_date=trade_date,
        baseline_version="buy-first-baselines-v3",
        arbitration_version=arbitration.version,
        arbitration_configuration_sha256=arbitration.sha256,
        feature_configuration_sha256="1" * 64,
        context_configuration_sha256="2" * 64,
        outcome_configuration_sha256="3" * 64,
        cost_policy_sha256="4" * 64,
        capture_evidence_sha256=sha256(f"capture:{trade_date}".encode()),
        shadow_audit_sha256=sha256(f"shadow:{trade_date}".encode()),
        capture_gap_count=0,
        evaluations=tuple(
            ArbitratedSessionEvaluation(
                strategy=target.strategy,
                symbol=target.symbol,
                horizon_seconds=target.horizon_seconds,
                selected_count=1,
                eligible_outcome_count=1,
                positive_net_count=int(Decimal(net_bps) > 0),
                average_conditional_net_return_bps=Decimal(net_bps),
            )
            for target in gate.targets
        ),
    )


def test_promotion_stays_pending_until_the_full_session_policy_exists() -> None:
    evaluation, gate, arbitration = _policies()
    sessions = {
        FIRST_DATE + timedelta(days=index): _session(FIRST_DATE + timedelta(days=index))
        for index in range(25)
    }

    report = evaluate_promotion_gate(sessions, evaluation, gate, arbitration)

    assert report.status == "PENDING"
    assert report.reasons == ("INSUFFICIENT_SESSIONS",)
    assert report.required_session_count == 26
    assert report.capital_authorized is False
    assert all(item.status == "PENDING" for item in report.targets)


def test_complete_profitable_selected_holdout_passes_for_paper_only() -> None:
    evaluation, gate, arbitration = _policies()
    sessions = {
        FIRST_DATE + timedelta(days=index): _session(FIRST_DATE + timedelta(days=index))
        for index in range(26)
    }

    report = evaluate_promotion_gate(sessions, evaluation, gate, arbitration)

    assert report.status == "PASS"
    assert report.reasons == ()
    assert len(report.holdout_dates) == 5
    assert len(report.shadow_audit_sha256s) == 5
    assert all(item.status == "PASS" for item in report.targets)
    assert all(item.selected_count == 5 for item in report.targets)
    assert report.capital_authorized is False


def test_bad_selected_holdout_performance_fails() -> None:
    evaluation, gate, arbitration = _policies()
    sessions = {
        FIRST_DATE + timedelta(days=index): _session(
            FIRST_DATE + timedelta(days=index),
            net_bps="-20" if index >= 21 else "5",
        )
        for index in range(26)
    }

    report = evaluate_promotion_gate(sessions, evaluation, gate, arbitration)

    assert report.status == "FAIL"
    assert all(item.status == "FAIL" for item in report.targets)
    assert all("AVERAGE_NET_RETURN" in item.reasons for item in report.targets)


def test_session_arbitration_lineage_must_match_prospective_policy() -> None:
    evaluation, gate, arbitration = _policies()
    sessions = {
        FIRST_DATE + timedelta(days=index): _session(FIRST_DATE + timedelta(days=index))
        for index in range(26)
    }
    first = next(iter(sessions))
    sessions[first] = sessions[first].model_copy(
        update={"arbitration_configuration_sha256": "f" * 64}
    )

    with pytest.raises(ValueError, match="prospective policies"):
        evaluate_promotion_gate(sessions, evaluation, gate, arbitration)
