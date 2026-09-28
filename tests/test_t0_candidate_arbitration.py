"""Candidate arbitration stays deterministic, prospective, and outcome-blind."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import cast

import pytest
from t0_trading.arbitration import CandidateArbitrator, arbitrate_candidates
from t0_trading.configuration import load_configuration
from t0_trading.strategy.baselines import (
    BASELINE_GROUP_NAMES,
    BaselineCandidate,
    BaselineName,
    GroupEvidence,
)

CONFIGURATION = Path("t0-trading/config/trading.yaml")
TRADE_DATE = date(2026, 9, 28)
DECISION_AT = datetime(2026, 9, 28, 2, 30, tzinfo=UTC)


def _candidate(
    strategy: BaselineName,
    symbol: str,
    decision_at: datetime = DECISION_AT,
    *,
    strength: str = "0.5",
    blocked: bool = False,
) -> BaselineCandidate:
    reasons = ("test_block",) if blocked else ()
    value = None if blocked else Decimal(strength)
    return BaselineCandidate(
        strategy=strategy,
        symbol=symbol,
        trade_date=TRADE_DATE,
        decision_at=decision_at,
        feature_snapshot_sha256=("a" if symbol == "VIC" else "b") * 64,
        peer_feature_snapshot_sha256=(
            "c" * 64 if strategy == "vic_vhm_relative" and not blocked else None
        ),
        groups=cast(
            tuple[GroupEvidence, GroupEvidence, GroupEvidence],
            tuple(
                GroupEvidence(name=name, strength=value) for name in BASELINE_GROUP_NAMES[strategy]
            ),
        ),
        strength=Decimal(0) if blocked else Decimal(strength),
        block_reasons=reasons,
    )


def _clock(decision_at: datetime = DECISION_AT) -> tuple[BaselineCandidate, ...]:
    return tuple(
        _candidate(strategy, symbol, decision_at)
        for symbol in ("VIC", "VHM")
        for strategy in ("mean_reversion", "momentum_pullback", "vic_vhm_relative")
    )


def test_arbitration_is_deterministic_and_enforces_strategy_and_symbol_conflicts() -> None:
    policy = load_configuration(CONFIGURATION).resolve_candidate_arbitration(TRADE_DATE)
    assert policy is not None
    candidates = (
        *_clock(),
        *_clock(DECISION_AT + timedelta(seconds=5)),
        *_clock(DECISION_AT + timedelta(seconds=300)),
    )

    first = arbitrate_candidates(candidates, policy)
    repeated = arbitrate_candidates(tuple(reversed(candidates)), policy)

    assert first == repeated
    assert len(first) == len(candidates)
    assert len({item.sha256 for item in first}) == len(first)
    by_clock = {
        clock: tuple(item for item in first if item.decision_at == clock)
        for clock in {item.decision_at for item in first}
    }
    for clock in (DECISION_AT, DECISION_AT + timedelta(seconds=300)):
        selected = [item for item in by_clock[clock] if item.status == "SELECTED"]
        assert all(item.action == "BUY" and item.horizon_seconds == 300 for item in selected)
        assert [(item.strategy, item.priority) for item in selected] == [
            ("vic_vhm_relative", 0),
            ("vic_vhm_relative", 1),
        ]
        assert all(
            item.rejection_reason == "LOWER_STRATEGY_PRIORITY"
            for item in by_clock[clock]
            if item.status == "REJECTED"
        )
    assert all(
        item.rejection_reason == "SYMBOL_COOLDOWN"
        for item in by_clock[DECISION_AT + timedelta(seconds=5)]
    )


def test_arbitration_fails_closed_without_a_complete_configured_matrix() -> None:
    policy = load_configuration(CONFIGURATION).resolve_candidate_arbitration(TRADE_DATE)
    assert policy is not None

    with pytest.raises(ValueError, match="complete configured strategy matrix"):
        arbitrate_candidates(_clock()[:-1], policy)

    stateful = CandidateArbitrator(policy)
    stateful.decide(_clock(DECISION_AT + timedelta(seconds=5)))
    with pytest.raises(ValueError, match="strictly increasing"):
        stateful.decide(_clock())


def test_arbitration_uses_policy_not_hardcoded_symbols_or_strategies() -> None:
    policy = load_configuration(CONFIGURATION).resolve_candidate_arbitration(TRADE_DATE)
    assert policy is not None
    one_slot = policy.model_copy(update={"maximum_selections_per_clock": 1})
    candidates = list(_clock())
    relative = next(
        item for item in candidates if item.symbol == "VIC" and item.strategy == "vic_vhm_relative"
    )
    candidates[candidates.index(relative)] = _candidate(
        "vic_vhm_relative",
        "VIC",
        blocked=True,
    )

    results = arbitrate_candidates(candidates, one_slot)
    selected = [item for item in results if item.status == "SELECTED"]

    assert [(item.symbol, item.strategy) for item in selected] == [("VHM", "vic_vhm_relative")]
    vic_momentum = next(
        item for item in results if item.symbol == "VIC" and item.strategy == "momentum_pullback"
    )
    assert vic_momentum.rejection_reason == "CLOCK_CAPACITY"
    vic_mean = next(
        item for item in results if item.symbol == "VIC" and item.strategy == "mean_reversion"
    )
    assert vic_mean.rejection_reason == "CLOCK_CAPACITY"
    blocked = next(
        item for item in results if item.symbol == "VIC" and item.strategy == "vic_vhm_relative"
    )
    assert blocked.rejection_reason == "UPSTREAM_BLOCKED"
