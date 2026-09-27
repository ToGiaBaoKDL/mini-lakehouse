"""Canonical fee-adjusted return calculations shared across research stages."""

from decimal import Decimal

from t0_trading.controls.model import CostPolicy
from t0_trading.numeric import basis_points
from t0_trading.outcomes import OutcomeLabel

_BPS = Decimal(10_000)


def conditional_net_return_bps(label: OutcomeLabel, costs: CostPolicy) -> Decimal:
    """Return fee-adjusted BUY-to-SELL basis points for one priced outcome."""
    if label.action != "BUY" or label.entry_vwap is None or label.horizon_vwap is None:
        raise ValueError("conditional net return requires a priced BUY label")
    entry = label.entry_vwap * (1 + (costs.buy_fee_bps + costs.extra_slippage_bps) / _BPS)
    exit_value = label.horizon_vwap * (
        1 - (costs.sell_fee_bps + costs.sell_tax_bps + costs.extra_slippage_bps) / _BPS
    )
    return basis_points(exit_value - entry, entry)
