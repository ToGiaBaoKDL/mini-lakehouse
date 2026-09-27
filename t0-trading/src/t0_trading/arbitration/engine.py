"""Deterministic, outcome-blind arbitration of buy-first candidates."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from datetime import date, datetime, timedelta

from t0_trading.arbitration.model import CandidateArbitration, RejectionReason
from t0_trading.configuration import CandidateArbitrationVersion
from t0_trading.strategy.baselines import BaselineCandidate


class CandidateArbitrator:
    """Stateful selector shared by replay and the live shadow clock."""

    def __init__(self, policy: CandidateArbitrationVersion) -> None:
        self._policy = policy
        self._rules = {rule.strategy: rule for rule in policy.rules}
        self._last_selected: dict[str, tuple[datetime, str]] = {}
        self._last_clock: tuple[date, datetime] | None = None

    def _result(
        self,
        candidate: BaselineCandidate,
        *,
        reason: RejectionReason | None = None,
        priority: int | None = None,
        selected_candidate_sha256: str | None = None,
    ) -> CandidateArbitration:
        selected = reason is None
        rule = self._rules[candidate.strategy]
        return CandidateArbitration(
            arbitration_version=self._policy.version,
            arbitration_configuration_sha256=self._policy.sha256,
            candidate_version=candidate.baseline_version,
            candidate_sha256=candidate.sha256,
            strategy=candidate.strategy,
            symbol=candidate.symbol,
            trade_date=candidate.trade_date,
            decision_at=candidate.decision_at,
            horizon_seconds=rule.horizon_seconds,
            strength=candidate.strength,
            status="SELECTED" if selected else "REJECTED",
            rejection_reason=reason,
            priority=priority,
            selected_candidate_sha256=(candidate.sha256 if selected else selected_candidate_sha256),
        )

    def decide(self, candidates: Sequence[BaselineCandidate]) -> tuple[CandidateArbitration, ...]:
        """Arbitrate one complete decision clock without consulting outcomes."""
        if not candidates:
            raise ValueError("candidate arbitration requires a complete decision clock")
        clocks = {(item.trade_date, item.decision_at) for item in candidates}
        if len(clocks) != 1:
            raise ValueError("candidate arbitration call must contain exactly one decision clock")
        trade_date, decision_at = next(iter(clocks))
        clock = (trade_date, decision_at)
        if self._last_clock is not None and clock <= self._last_clock:
            raise ValueError("candidate arbitration clocks must be strictly increasing")
        if not self._policy.contains(trade_date):
            raise ValueError("candidate arbitration policy does not cover the trade date")
        if {item.baseline_version for item in candidates} != {self._policy.candidate_version}:
            raise ValueError("candidate arbitration policy and candidate versions differ")
        if len({item.sha256 for item in candidates}) != len(candidates):
            raise ValueError("candidate arbitration inputs must be unique")

        by_symbol: dict[str, list[BaselineCandidate]] = defaultdict(list)
        for candidate in candidates:
            by_symbol[candidate.symbol].append(candidate)
        expected_strategies = set(self._rules)
        if any(
            len(items) != len(expected_strategies)
            or {item.strategy for item in items} != expected_strategies
            for items in by_symbol.values()
        ):
            raise ValueError("candidate arbitration requires a complete configured strategy matrix")
        self._last_clock = clock

        decisions: dict[str, CandidateArbitration] = {}
        winners: list[BaselineCandidate] = []
        contenders_by_winner: dict[str, tuple[BaselineCandidate, ...]] = {}
        for symbol in sorted(by_symbol):
            eligible: list[BaselineCandidate] = []
            for candidate in by_symbol[symbol]:
                rule = self._rules[candidate.strategy]
                if not candidate.is_candidate:
                    decisions[candidate.sha256] = self._result(
                        candidate,
                        reason="UPSTREAM_BLOCKED",
                    )
                elif candidate.strength < rule.minimum_strength:
                    decisions[candidate.sha256] = self._result(
                        candidate,
                        reason="BELOW_MINIMUM_STRENGTH",
                    )
                else:
                    eligible.append(candidate)
            if not eligible:
                continue
            previous = self._last_selected.get(symbol)
            if previous is not None and decision_at < previous[0] + timedelta(
                seconds=self._policy.cooldown_seconds
            ):
                for candidate in eligible:
                    decisions[candidate.sha256] = self._result(
                        candidate,
                        reason="SYMBOL_COOLDOWN",
                        selected_candidate_sha256=previous[1],
                    )
                continue
            winner, *lower_priority = sorted(
                eligible,
                key=lambda item: (
                    self._rules[item.strategy].priority,
                    -item.strength,
                    item.sha256,
                ),
            )
            winners.append(winner)
            contenders_by_winner[winner.sha256] = tuple(lower_priority)

        ordered_winners = sorted(
            winners,
            key=lambda item: (
                self._rules[item.strategy].priority,
                -item.strength,
                item.symbol,
                item.sha256,
            ),
        )
        selected = ordered_winners[: self._policy.maximum_selections_per_clock]
        for priority, candidate in enumerate(selected):
            decisions[candidate.sha256] = self._result(candidate, priority=priority)
            for contender in contenders_by_winner[candidate.sha256]:
                decisions[contender.sha256] = self._result(
                    contender,
                    reason="LOWER_STRATEGY_PRIORITY",
                    selected_candidate_sha256=candidate.sha256,
                )
            self._last_selected[candidate.symbol] = (decision_at, candidate.sha256)
        for candidate in ordered_winners[self._policy.maximum_selections_per_clock :]:
            for contender in (candidate, *contenders_by_winner[candidate.sha256]):
                decisions[contender.sha256] = self._result(
                    contender,
                    reason="CLOCK_CAPACITY",
                )

        return tuple(
            decisions[item.sha256]
            for item in sorted(
                candidates,
                key=lambda candidate: (
                    candidate.symbol,
                    self._rules[candidate.strategy].priority,
                    candidate.sha256,
                ),
            )
        )


def arbitrate_candidates(
    candidates: Sequence[BaselineCandidate],
    policy: CandidateArbitrationVersion,
) -> tuple[CandidateArbitration, ...]:
    """Arbitrate a complete candidate matrix in chronological order."""
    if not candidates:
        raise ValueError("candidate arbitration requires candidates")
    arbitrator = CandidateArbitrator(policy)
    grouped: dict[tuple[date, datetime], list[BaselineCandidate]] = defaultdict(list)
    for candidate in candidates:
        grouped[(candidate.trade_date, candidate.decision_at)].append(candidate)
    results: list[CandidateArbitration] = []
    for clock in sorted(grouped):
        results.extend(arbitrator.decide(grouped[clock]))
    return tuple(results)
