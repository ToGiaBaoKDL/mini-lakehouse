"""Buy-first research baselines from certified, point-in-time feature snapshots.

The three hypotheses in the VIC/VHM brief can be audited without changing the
capture policy, sending an alert, or pretending that missing context exists.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from t0_trading.context import ContextDataMode, DecisionContext, MarketRegime
from t0_trading.features import FeatureSnapshot, WindowFeatures
from t0_trading.identity import canonical_json, sha256
from t0_trading.market.session import MarketSession
from t0_trading.numeric import RATIO_QUANTUM, ratio

BaselineName = Literal["mean_reversion", "momentum_pullback", "vic_vhm_relative"]
BASELINE_NAMES: tuple[BaselineName, ...] = (
    "mean_reversion",
    "momentum_pullback",
    "vic_vhm_relative",
)
BASELINE_GROUP_NAMES: dict[BaselineName, tuple[str, str, str]] = {
    "mean_reversion": ("price_stretch", "zone", "reversal_confirmation"),
    "momentum_pullback": (
        "trend_strength",
        "pullback_quality",
        "reacceleration_confirmation",
    ),
    "vic_vhm_relative": (
        "relative_divergence",
        "lagged_peer_confirmation",
        "market_confirmation",
    ),
}
BASELINE_VERSION = "buy-first-baselines-v3"

_ZERO = Decimal(0)
_ONE = Decimal(1)


class GroupEvidence(BaseModel):
    """A group is unavailable, failed, or matched; no imputation is permitted."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    strength: Decimal | None = Field(ge=0, le=1)

    @property
    def status(self) -> Literal["UNAVAILABLE", "FAIL", "MATCH"]:
        if self.strength is None:
            return "UNAVAILABLE"
        return "MATCH" if self.strength > 0 else "FAIL"


class BaselineCandidate(BaseModel):
    """Research-only BUY candidate; it is neither a signal nor an order intent."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[3] = 3
    baseline_version: Literal["buy-first-baselines-v3"] = BASELINE_VERSION
    strategy: BaselineName
    symbol: str
    trade_date: date
    decision_at: datetime
    feature_snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    peer_feature_snapshot_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    context_snapshot_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    context_version: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9-]*$")
    context_configuration_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    context_data_mode: ContextDataMode | None = None
    market_regime: MarketRegime = "UNKNOWN"
    groups: tuple[GroupEvidence, GroupEvidence, GroupEvidence]
    strength: Decimal = Field(ge=0, le=1)
    block_reasons: tuple[str, ...]

    @model_validator(mode="after")
    def validate_candidate(self) -> BaselineCandidate:
        expected = BASELINE_GROUP_NAMES[self.strategy]
        if tuple(group.name for group in self.groups) != expected:
            raise ValueError("baseline group contract is inconsistent")
        if self.symbol != self.symbol.strip().upper():
            raise ValueError("baseline symbol must be uppercase")
        if self.decision_at.tzinfo is None or self.decision_at.utcoffset() is None:
            raise ValueError("baseline decision time must be timezone-aware")
        if self.strategy != "vic_vhm_relative" and self.peer_feature_snapshot_sha256 is not None:
            raise ValueError("only relative baselines may carry peer lineage")
        if (
            self.strategy == "vic_vhm_relative"
            and self.peer_feature_snapshot_sha256 is None
            and not self.block_reasons
        ):
            raise ValueError("active relative baseline requires peer lineage")
        if self.context_snapshot_sha256 is None and self.market_regime != "UNKNOWN":
            raise ValueError("classified market regime requires context lineage")
        context_lineage = (
            self.context_snapshot_sha256,
            self.context_version,
            self.context_configuration_sha256,
            self.context_data_mode,
        )
        if any(value is None for value in context_lineage) and any(
            value is not None for value in context_lineage
        ):
            raise ValueError("context lineage must be wholly present or absent")
        if tuple(dict.fromkeys(self.block_reasons)) != self.block_reasons:
            raise ValueError("baseline block reasons must be unique")
        if (
            not self.block_reasons
            and self.strategy in {"mean_reversion", "vic_vhm_relative"}
            and any(group.status != "MATCH" for group in self.groups)
        ):
            raise ValueError("context-dependent candidate must match every group")
        if not self.block_reasons and (
            self.groups[0].status != "MATCH"
            or not any(group.status == "MATCH" for group in self.groups[1:])
            or self.strength == 0
        ):
            raise ValueError("candidate must match its required and confirming groups")
        if self.block_reasons and self.strength != 0:
            raise ValueError("blocked baseline cannot carry actionable strength")
        return self

    @property
    def is_candidate(self) -> bool:
        return not self.block_reasons

    def canonical_bytes(self) -> bytes:
        return canonical_json(self.model_dump(mode="json"))

    @property
    def sha256(self) -> str:
        return sha256(self.canonical_bytes())


def _window(snapshot: FeatureSnapshot, seconds: int) -> WindowFeatures:
    matches = tuple(item for item in snapshot.windows if item.window_seconds == seconds)
    if len(matches) != 1:
        raise ValueError(f"baseline requires one {seconds}-second feature window")
    return matches[0]


def _positive(numerator: Decimal | None, denominator: Decimal | None) -> Decimal | None:
    if numerator is None or denominator is None or denominator <= 0:
        return None
    return min(_ONE, max(_ZERO, ratio(numerator, denominator, quantum=RATIO_QUANTUM)))


def _flow(window: WindowFeatures) -> Decimal | None:
    return (
        max(_ZERO, window.trade_volume_imbalance)
        if window.trade_volume_imbalance is not None
        else None
    )


def _minimum(*values: Decimal | None) -> Decimal | None:
    if any(value is None for value in values):
        return None
    return min(value for value in values if value is not None)


def _build(
    strategy: BaselineName,
    snapshot: FeatureSnapshot,
    strengths: tuple[Decimal | None, Decimal | None, Decimal | None],
    *,
    peer: FeatureSnapshot | None = None,
    context: DecisionContext | None = None,
    block: str | None = None,
) -> BaselineCandidate:
    names = BASELINE_GROUP_NAMES[strategy]
    groups = tuple(
        GroupEvidence(name=name, strength=strength)
        for name, strength in zip(names, strengths, strict=True)
    )
    if block is not None:
        reasons = (block,)
    elif strategy in {"mean_reversion", "vic_vhm_relative"}:
        reasons = (
            ("required_group_unavailable",)
            if any(group.status == "UNAVAILABLE" for group in groups)
            else ("required_group_failed",)
            if any(group.status == "FAIL" for group in groups)
            else ()
        )
    else:
        reasons = (
            ("required_group_unavailable",)
            if groups[0].status == "UNAVAILABLE"
            else ("required_group_failed",)
            if groups[0].status == "FAIL"
            else ("confirming_group_unavailable",)
            if all(group.status == "UNAVAILABLE" for group in groups[1:])
            else ("confirming_group_failed",)
            if not any(group.status == "MATCH" for group in groups[1:])
            else ()
        )
    matched = [
        group.strength for group in groups if group.strength is not None and group.strength > 0
    ]
    strength = _ZERO if reasons else ratio(sum(matched, _ZERO), len(matched))
    return BaselineCandidate(
        strategy=strategy,
        symbol=snapshot.symbol,
        trade_date=snapshot.trade_date,
        decision_at=snapshot.decision_at,
        feature_snapshot_sha256=snapshot.sha256,
        peer_feature_snapshot_sha256=peer.sha256 if peer is not None else None,
        context_snapshot_sha256=context.sha256 if context is not None else None,
        context_version=context.context_version if context is not None else None,
        context_configuration_sha256=(
            context.context_configuration_sha256 if context is not None else None
        ),
        context_data_mode=context.data_mode if context is not None else None,
        market_regime=context.regime if context is not None else "UNKNOWN",
        groups=groups,  # type: ignore[arg-type]
        strength=strength,
        block_reasons=reasons,
    )


def _mean_reversion(
    snapshot: FeatureSnapshot, context: DecisionContext | None
) -> BaselineCandidate:
    short, long = _window(snapshot, 30), _window(snapshot, 300)
    stretch = _positive(
        -long.last_price_to_vwap_bps if long.last_price_to_vwap_bps is not None else None,
        long.realized_volatility_bps,
    )
    reversal = _minimum(
        _positive(short.price_return_bps, short.realized_volatility_bps),
        _flow(short),
    )
    zone = context.zone(snapshot.symbol).near_support_strength if context is not None else None
    return _build("mean_reversion", snapshot, (stretch, zone, reversal), context=context)


def _momentum_pullback(
    snapshot: FeatureSnapshot, context: DecisionContext | None
) -> BaselineCandidate:
    short, medium, long = (_window(snapshot, seconds) for seconds in (30, 60, 300))
    trend = _minimum(
        _positive(medium.price_return_bps, medium.realized_volatility_bps),
        _positive(long.price_return_bps, long.realized_volatility_bps),
    )
    pullback = _minimum(
        _positive(
            -short.price_return_bps if short.price_return_bps is not None else None,
            short.realized_volatility_bps,
        ),
        _flow(short),
    )
    if (
        short.price_return_bps is not None
        and medium.price_return_bps is not None
        and abs(short.price_return_bps) > max(_ZERO, medium.price_return_bps)
    ):
        pullback = _ZERO
    reacceleration = _minimum(
        _positive(short.price_return_bps, short.realized_volatility_bps),
        _flow(short),
    )
    return _build(
        "momentum_pullback",
        snapshot,
        (trend, pullback, reacceleration),
        context=context,
    )


def _relative(
    snapshot: FeatureSnapshot,
    peer: FeatureSnapshot | None,
    lagged_peer: FeatureSnapshot | None,
    context: DecisionContext | None,
) -> BaselineCandidate:
    if peer is None or not peer.is_eligible:
        return _build(
            "vic_vhm_relative",
            snapshot,
            (None, None, None),
            context=context,
            block="missing_peer",
        )
    target_long, peer_long = _window(snapshot, 300), _window(peer, 300)
    divergence = _positive(
        peer_long.price_return_bps - target_long.price_return_bps
        if peer_long.price_return_bps is not None and target_long.price_return_bps is not None
        else None,
        peer_long.realized_volatility_bps + target_long.realized_volatility_bps
        if peer_long.realized_volatility_bps is not None
        and target_long.realized_volatility_bps is not None
        else None,
    )
    lagged = None
    if lagged_peer is not None and lagged_peer.is_eligible:
        previous = _window(lagged_peer, 30)
        current = _window(snapshot, 30)
        lagged = _minimum(
            _positive(previous.price_return_bps, previous.realized_volatility_bps),
            _positive(current.price_return_bps, current.realized_volatility_bps),
            _flow(current),
        )
    # A prior, disjoint peer move is only a baseline proxy, not a fitted leader model.
    market = context.market_confirmation_strength if context is not None else None
    return _build(
        "vic_vhm_relative",
        snapshot,
        (divergence, lagged, market),
        peer=peer,
        context=context,
    )


def score_buy_first_baselines(
    snapshots: Sequence[FeatureSnapshot],
    contexts: Sequence[DecisionContext] = (),
) -> tuple[BaselineCandidate, ...]:
    """Emit all candidates and abstentions without reading future outcomes.

    Exactly one disjoint 30-second prior peer window is used for the relative
    baseline's lagged-move proxy. A missing peer clock abstains instead of
    interpolation. Only VIC/VHM are supported by the V1 research contract.
    """
    if not snapshots:
        raise ValueError("baseline scoring requires feature snapshots")
    if (
        len({item.trade_date for item in snapshots}) != 1
        or len({(item.feature_version, item.configuration_sha256) for item in snapshots}) != 1
    ):
        raise ValueError("baseline features must share trade date and lineage")
    if any(item.symbol not in {"VIC", "VHM"} for item in snapshots):
        raise ValueError("V1 baselines support only VIC and VHM")
    by_key = {(item.symbol, item.decision_at): item for item in snapshots}
    if len(by_key) != len(snapshots):
        raise ValueError("baseline feature clocks must be unique")
    context_by_clock = {item.decision_at: item for item in contexts}
    if len(context_by_clock) != len(contexts):
        raise ValueError("baseline context clocks must be unique")
    if any(
        context.trade_date != snapshots[0].trade_date
        or context.feature_configuration_sha256 != snapshots[0].configuration_sha256
        or tuple(zone.symbol for zone in context.zones)
        != tuple(sorted({item.symbol for item in snapshots}))
        for context in contexts
    ):
        raise ValueError("baseline context lineage does not match features")
    output: list[BaselineCandidate] = []
    for snapshot in sorted(snapshots, key=lambda item: (item.decision_at, item.symbol)):
        peer_symbol = "VHM" if snapshot.symbol == "VIC" else "VIC"
        peer = by_key.get((peer_symbol, snapshot.decision_at))
        context = context_by_clock.get(snapshot.decision_at)
        block = (
            "feature_ineligible"
            if not snapshot.is_eligible
            else "not_continuous_session"
            if snapshot.market_session
            not in (MarketSession.CONTINUOUS_AM, MarketSession.CONTINUOUS_PM)
            else "market_status_ineligible"
            if context is not None
            and any(not status.is_tradable for status in context.market_statuses)
            else None
        )
        if block is not None:
            output.extend(
                _build(
                    name,
                    snapshot,
                    (None, None, None),
                    peer=peer if name == "vic_vhm_relative" else None,
                    context=context,
                    block=block,
                )
                for name in BASELINE_NAMES
            )
            continue
        output.extend(
            (
                _mean_reversion(snapshot, context),
                _momentum_pullback(snapshot, context),
                _relative(
                    snapshot,
                    peer,
                    by_key.get((peer_symbol, snapshot.decision_at - timedelta(seconds=30))),
                    context,
                ),
            )
        )
    return tuple(output)
