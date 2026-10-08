"""Pure source-aware regime classification, shared by live capture and replay."""

from collections.abc import Sequence
from decimal import Decimal

from t0_trading.configuration import BreadthVersion, ContextVersion, RegimeVersion
from t0_trading.context.breadth import BreadthContext
from t0_trading.context.model import IndexContext, MarketRegime, MarketStatusContext
from t0_trading.identity import canonical_json, sha256
from t0_trading.numeric import RATIO_QUANTUM, ratio

_ZERO = Decimal(0)
_ONE = Decimal(1)


def context_identity(
    policy: ContextVersion,
    regime_policy: RegimeVersion | None,
    breadth_policy: BreadthVersion | None,
) -> str:
    """Preserve historical identities; new analytical assumptions define a new cohort."""
    if regime_policy is None:
        return policy.sha256
    if regime_policy.basis == "CONSTITUENT_BREADTH" and (
        breadth_policy is None or regime_policy.reference_index not in breadth_policy.indices
    ):
        raise ValueError("constituent regime requires its declared breadth policy")
    return sha256(
        canonical_json(
            {
                "context_policy_sha256": policy.sha256,
                "regime_policy_sha256": regime_policy.sha256,
                "breadth_policy_sha256": breadth_policy.sha256
                if breadth_policy is not None and regime_policy.basis == "CONSTITUENT_BREADTH"
                else None,
            }
        )
    )


def market_state(
    indices: Sequence[IndexContext],
    statuses: Sequence[MarketStatusContext],
    policy: ContextVersion,
    *,
    regime_policy: RegimeVersion | None = None,
    breadth: Sequence[BreadthContext] = (),
    breadth_policy: BreadthVersion | None = None,
) -> tuple[Decimal | None, MarketRegime, tuple[str, ...]]:
    """An explicit basis never falls back to another feed or calendar authorization."""
    if regime_policy is not None and regime_policy.basis == "CONSTITUENT_BREADTH":
        if breadth_policy is None:
            raise ValueError("constituent regime requires a breadth policy")
        reference = next(
            (item for item in breadth if item.index == regime_policy.reference_index), None
        )
        if reference is None:
            return None, "UNKNOWN", (f"{regime_policy.reference_index}_MISSING_BREADTH",)
        if reference.policy_sha256 != breadth_policy.sha256:
            raise ValueError("constituent regime breadth policy lineage mismatch")
        if reference.reasons:
            return (
                None,
                "UNKNOWN",
                tuple(f"{reference.index}_{reason}" for reason in reference.reasons),
            )
        threshold = regime_policy.trend_threshold_bps
        dispersion_threshold = regime_policy.high_dispersion_threshold_bps
        if (
            threshold is None
            or dispersion_threshold is None
            or reference.mean_return_bps is None
            or reference.dispersion_bps is None
            or reference.advance_ratio is None
        ):
            raise ValueError("eligible constituent regime requires complete metrics and policy")
        confirmation = ratio(
            min(_ONE, max(_ZERO, reference.mean_return_bps / threshold)) * reference.advance_ratio,
            _ONE,
        )
        if reference.dispersion_bps >= dispersion_threshold:
            return confirmation, "HIGH_DISPERSION", ()
        if reference.mean_return_bps >= threshold and reference.upward_confirmation:
            return confirmation, "TREND_UP", ()
        if reference.mean_return_bps <= -threshold and (
            reference.advance_ratio <= _ONE - breadth_policy.confirmation_advance_ratio
        ):
            return confirmation, "TREND_DOWN", ()
        return confirmation, "RANGE", ()

    reasons = tuple(
        dict.fromkeys(
            (
                *(f"{item.index}_{reason}" for item in indices for reason in item.reasons),
                *(
                    f"{item.market}_{reason}"
                    for item in statuses
                    for reason in item.reasons
                    if regime_policy is None
                ),
            )
        )
    )
    if not indices:
        reasons = (*reasons, "MISSING_REQUIRED_INDICES")
    if reasons:
        return None, "UNKNOWN", reasons
    missing_windows = tuple(
        f"{item.index}_MISSING_REQUIRED_WINDOW"
        for item in indices
        if not any(
            window.window_seconds == policy.market_windows_seconds[-1] for window in item.windows
        )
    )
    if missing_windows:
        return None, "UNKNOWN", missing_windows
    long = [
        next(
            window
            for window in item.windows
            if window.window_seconds == policy.market_windows_seconds[-1]
        )
        for item in indices
    ]
    confirmation = min(
        _ONE,
        max(
            _ZERO,
            min(
                ratio(window.return_bps, policy.trend_threshold_bps, quantum=RATIO_QUANTUM)
                for window in long
            ),
        ),
    )
    market_return = sum((window.return_bps for window in long), _ZERO) / len(long)
    market_volatility = sum((window.realized_volatility_bps for window in long), _ZERO) / len(long)
    if market_volatility >= policy.high_volatility_threshold_bps:
        return confirmation, "HIGH_VOLATILITY", ()
    if market_return >= policy.trend_threshold_bps:
        return confirmation, "TREND_UP", ()
    if market_return <= -policy.trend_threshold_bps:
        return confirmation, "TREND_DOWN", ()
    return confirmation, "RANGE", ()
