"""Stateful decision gates shared by ordered replay and a future shadow clock."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, timedelta

from t0_trading.configuration import (
    DecisionRule,
    DecisionVersion,
    OutcomeVersion,
    StrategyVersion,
    TradingVersion,
)
from t0_trading.decisions.model import DecisionAction, StrategyDecision
from t0_trading.features import FeatureSnapshot
from t0_trading.strategy import StrategyScore, score_features


class DecisionEngine:
    """Apply deterministic thresholds, market gates, and per-strategy cooldowns."""

    def __init__(
        self,
        configuration: TradingVersion,
        strategy_policy: StrategyVersion,
        outcome_policy: OutcomeVersion,
        decision_policy: DecisionVersion,
    ) -> None:
        if (
            decision_policy.strategy_version != strategy_policy.version
            or decision_policy.outcome_version != outcome_policy.version
        ):
            raise ValueError("decision policy lineage does not match score and outcome policies")
        if any(
            rule.horizon_seconds not in outcome_policy.horizons_seconds
            for rule in decision_policy.rules
        ):
            raise ValueError("decision horizon is absent from the outcome policy")
        if decision_policy.cooldown_seconds % configuration.features.cadence_seconds:
            raise ValueError("decision cooldown must align to the feature cadence")
        self.configuration = configuration
        self.strategy_policy = strategy_policy
        self.outcome_policy = outcome_policy
        self.decision_policy = decision_policy
        self._last_decision_at: datetime | None = None
        self._last_action_at: dict[tuple[str, str], datetime] = {}

    def _validate_snapshots(self, snapshots: Sequence[FeatureSnapshot]) -> tuple[date, datetime]:
        if not snapshots:
            raise ValueError("decision engine requires feature snapshots")
        keys = {(snapshot.symbol, snapshot.decision_at) for snapshot in snapshots}
        decision_times = {snapshot.decision_at for snapshot in snapshots}
        trade_dates = {snapshot.trade_date for snapshot in snapshots}
        if (
            len(keys) != len(snapshots)
            or len(decision_times) != 1
            or len(trade_dates) != 1
            or {snapshot.symbol for snapshot in snapshots} != set(self.configuration.market.symbols)
        ):
            raise ValueError("decision clock requires one snapshot per configured symbol")
        decision_at = next(iter(decision_times))
        trade_date = next(iter(trade_dates))
        if self._last_decision_at is not None and decision_at <= self._last_decision_at:
            raise ValueError("decision times must be strictly increasing")
        if not all(
            policy.contains(trade_date)
            for policy in (
                self.configuration,
                self.strategy_policy,
                self.outcome_policy,
                self.decision_policy,
            )
        ):
            raise ValueError("decision policies are not effective for the feature trade date")
        return trade_date, decision_at

    @staticmethod
    def _threshold(rule: DecisionRule, score: StrategyScore | None):
        if score is None or score.signed_score == 0:
            return None
        return rule.buy_minimum_strength if score.signed_score > 0 else rule.sell_minimum_strength

    def _decide(
        self,
        snapshot: FeatureSnapshot,
        rule: DecisionRule,
        score: StrategyScore | None,
    ) -> StrategyDecision:
        reasons = list(snapshot.reasons)
        threshold = self._threshold(rule, score)
        if score is None:
            reasons.append("SCORE_UNAVAILABLE")
        else:
            if (
                snapshot.spread_bps is None
                or snapshot.trade_age_seconds is None
                or snapshot.quote_age_seconds is None
            ):
                raise ValueError("scored feature snapshot is missing decision risk inputs")
            if snapshot.spread_bps > self.decision_policy.maximum_spread_bps:
                reasons.append("SPREAD_LIMIT")
            if snapshot.trade_age_seconds > self.decision_policy.maximum_trade_age_seconds:
                reasons.append("TRADE_AGE_LIMIT")
            if snapshot.quote_age_seconds > self.decision_policy.maximum_quote_age_seconds:
                reasons.append("QUOTE_AGE_LIMIT")
            if score.signed_score == 0:
                reasons.append("ZERO_SCORE")
            elif threshold is None or abs(score.signed_score) < threshold:
                reasons.append("BELOW_THRESHOLD")

        candidate: DecisionAction = "ABSTAIN"
        if score is not None and score.direction is not None and not reasons:
            candidate = score.direction
            previous = self._last_action_at.get((rule.strategy, snapshot.symbol))
            if previous is not None and snapshot.decision_at - previous < timedelta(
                seconds=self.decision_policy.cooldown_seconds
            ):
                reasons.append("COOLDOWN")
                candidate = "ABSTAIN"
            else:
                self._last_action_at[rule.strategy, snapshot.symbol] = snapshot.decision_at

        return StrategyDecision(
            decision_version=self.decision_policy.version,
            decision_configuration_sha256=self.decision_policy.sha256,
            strategy=rule.strategy,
            strategy_version=self.strategy_policy.version,
            strategy_configuration_sha256=self.strategy_policy.sha256,
            outcome_version=self.outcome_policy.version,
            outcome_configuration_sha256=self.outcome_policy.sha256,
            feature_version=snapshot.feature_version,
            feature_configuration_sha256=snapshot.configuration_sha256,
            feature_snapshot_sha256=snapshot.sha256,
            strategy_score_sha256=score.sha256 if score is not None else None,
            symbol=snapshot.symbol,
            trade_date=snapshot.trade_date,
            decision_at=snapshot.decision_at,
            market_session=snapshot.market_session,
            horizon_seconds=rule.horizon_seconds,
            signed_score=score.signed_score if score is not None else None,
            minimum_strength=threshold,
            action=candidate,
            reasons=tuple(dict.fromkeys(reasons)),
        )

    def decisions(self, snapshots: Sequence[FeatureSnapshot]) -> tuple[StrategyDecision, ...]:
        """Evaluate one complete feature-clock instant exactly once."""
        _, decision_at = self._validate_snapshots(snapshots)
        scores = score_features(snapshots, self.configuration, self.strategy_policy)
        by_key = {(score.feature_snapshot_sha256, score.strategy): score for score in scores}
        ordered_snapshots = sorted(
            snapshots,
            key=lambda snapshot: self.configuration.market.symbols.index(snapshot.symbol),
        )
        decisions = tuple(
            self._decide(
                snapshot,
                rule,
                by_key.get((snapshot.sha256, rule.strategy)),
            )
            for snapshot in ordered_snapshots
            for rule in self.decision_policy.rules
        )
        self._last_decision_at = decision_at
        return decisions


def replay_decisions(
    snapshots: Sequence[FeatureSnapshot],
    configuration: TradingVersion,
    strategy_policy: StrategyVersion,
    outcome_policy: OutcomeVersion,
    decision_policy: DecisionVersion,
) -> tuple[StrategyDecision, ...]:
    """Replay feature snapshots through the same ordered engine used by a shadow clock."""
    if not snapshots:
        raise ValueError("decision replay requires feature snapshots")
    keys = {(snapshot.symbol, snapshot.decision_at) for snapshot in snapshots}
    if len(keys) != len(snapshots):
        raise ValueError("decision replay feature snapshots must be unique")
    engine = DecisionEngine(
        configuration,
        strategy_policy,
        outcome_policy,
        decision_policy,
    )
    by_time: dict[datetime, list[FeatureSnapshot]] = {}
    for snapshot in snapshots:
        by_time.setdefault(snapshot.decision_at, []).append(snapshot)
    return tuple(
        decision
        for decision_at in sorted(by_time)
        for decision in engine.decisions(by_time[decision_at])
    )
