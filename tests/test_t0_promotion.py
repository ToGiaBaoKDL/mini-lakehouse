"""Promotion evaluates the exact prospective arbitration-selected population."""

import json
from collections.abc import Mapping
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import pytest
from t0_trading.arbitration import ShadowArbitrationAuditReport
from t0_trading.configuration import load_configuration
from t0_trading.execution.readiness import load_paper_readiness
from t0_trading.identity import canonical_json, sha256
from t0_trading.operations import operational_status
from t0_trading.promotion import (
    ArbitratedSessionEvaluation,
    ArbitratedSessionReport,
    evaluate_promotion_gate,
    load_session_evidence,
    publish_gate_evidence,
    publish_session_evidence,
)
from t0_trading.strategy.baselines import BaselineName

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
                strategy=cast(BaselineName, target.strategy),
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


class _Store:
    def __init__(self) -> None:
        self.values: dict[str, bytes] = {}

    def uri(self, key: str) -> str:
        return f"s3://landing/{key}"

    def read_json(self, key: str) -> dict[str, Any] | None:
        body = self.values.get(key)
        return None if body is None else json.loads(body)

    def list_keys(self, prefix: str) -> tuple[str, ...]:
        return tuple(sorted(key for key in self.values if key.startswith(f"{prefix}/")))

    def put_json(self, key: str, value: Mapping[str, Any]) -> tuple[str, str]:
        body = canonical_json(value)
        current = self.values.setdefault(key, body)
        if current != body:
            raise RuntimeError("immutable conflict")
        return key, sha256(body)


def _shadow_audit(trade_date: date, session: ArbitratedSessionReport):
    return ShadowArbitrationAuditReport(
        trade_date=trade_date,
        baseline_version=session.baseline_version,
        arbitration_version=session.arbitration_version,
        arbitration_configuration_sha256=session.arbitration_configuration_sha256,
        stream_session_ids=("session-1",),
        capture_evidence_sha256=session.capture_evidence_sha256,
        capture_message_count=1,
        gap_count=0,
        manifest_sha256="5" * 64,
        candidate_count=1,
        candidate_sha256="6" * 64,
        arbitration_count=1,
        arbitration_sha256="7" * 64,
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


def test_daily_evidence_and_as_of_gate_are_immutable_and_reloadable() -> None:
    evaluation, gate, arbitration = _policies()
    store = _Store()
    session = _session(FIRST_DATE)
    shadow = _shadow_audit(FIRST_DATE, session)
    session = session.model_copy(
        update={"shadow_audit_sha256": sha256(canonical_json(shadow.model_dump(mode="json")))}
    )

    first = publish_session_evidence(store, shadow, session)
    assert publish_session_evidence(store, shadow, session) == first
    sessions = load_session_evidence(store, gate, as_of_date=FIRST_DATE)
    assert sessions == {FIRST_DATE: session}

    report = evaluate_promotion_gate(sessions, evaluation, gate, arbitration)
    key, digest = publish_gate_evidence(store, as_of_date=FIRST_DATE, report=report)
    assert key.endswith("as_of_date=2026-09-28/promotion_gate.json")
    assert digest == report.sha256
    assert report.status == "PENDING"


def _stored_evidence(count: int, *, net_bps: str = "5"):
    store = _Store()
    evaluation, gate, arbitration = _policies()
    for index in range(count):
        day = FIRST_DATE + timedelta(days=index)
        session = _session(day, net_bps=net_bps)
        audit = _shadow_audit(day, session)
        session = session.model_copy(
            update={"shadow_audit_sha256": sha256(canonical_json(audit.model_dump(mode="json")))}
        )
        publish_session_evidence(store, audit, session)
    latest = FIRST_DATE + timedelta(days=count - 1)
    report = evaluate_promotion_gate(
        load_session_evidence(store, gate, as_of_date=latest), evaluation, gate, arbitration
    )
    publish_gate_evidence(store, as_of_date=latest, report=report)
    return store, latest


@pytest.mark.parametrize(
    "count,net,mode", [(25, "5", "OBSERVE_ONLY"), (26, "-5", "OBSERVE_ONLY"), (26, "5", "PAPER")]
)
def test_runtime_readiness_recomputes_latest_complete_gate(count: int, net: str, mode: str) -> None:
    store, previous = _stored_evidence(count, net_bps=net)
    config = load_configuration(CONFIGURATION)
    result = load_paper_readiness(
        store,
        config,
        trade_date=previous + timedelta(days=1),
        previous_session=previous,
        enabled=True,
    )
    assert result.mode == mode
    assert result.capital_authorized is False
    disabled = load_paper_readiness(
        store, config, trade_date=previous + timedelta(days=1), previous_session=previous
    )
    assert disabled.mode == "OBSERVE_ONLY"


def test_runtime_does_not_fall_back_to_old_pass_or_accept_corrupt_audit() -> None:
    store, previous = _stored_evidence(26)
    config = load_configuration(CONFIGURATION)
    missing = load_paper_readiness(
        store,
        config,
        trade_date=previous + timedelta(days=2),
        previous_session=previous + timedelta(days=1),
        enabled=True,
    )
    assert missing.mode == "OBSERVE_ONLY"
    assert missing.reason == "MISSING_PREVIOUS_SESSION_GATE"
    audit_key = next(key for key in store.values if key.endswith("/shadow_audit.json"))
    store.values[audit_key] = b"{}"
    corrupt = load_paper_readiness(
        store,
        config,
        trade_date=previous + timedelta(days=1),
        previous_session=previous,
        enabled=True,
    )
    assert corrupt.mode == "OBSERVE_ONLY"
    assert corrupt.reason.startswith("INVALID_OR_UNAVAILABLE_EVIDENCE")


def test_operational_status_does_not_treat_absent_live_data_as_healthy() -> None:
    report = operational_status(
        _Store(),
        load_configuration(CONFIGURATION),
        trade_date=FIRST_DATE + timedelta(days=1),
        previous_session=FIRST_DATE,
    )
    assert report.journal == report.publication == "MISSING"
    assert report.ledger_reconciled is None
    assert report.live_freshness == "NOT_OBSERVED"
    assert report.security_status == "NOT_CERTIFIED"
    assert report.readiness.mode == "OBSERVE_ONLY"


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
