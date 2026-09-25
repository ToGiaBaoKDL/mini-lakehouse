"""Broker-specific research assumptions kept outside generic accounting contracts."""

from datetime import date, datetime
from decimal import Decimal

from t0_trading.simulation.model import CostPolicy


def public_vndirect_dta_costs(
    trade_date: date, *, checked_at: datetime, extra_slippage_bps: Decimal = Decimal(0)
) -> CostPolicy:
    """Public DTA online tariff, not an account-statement or historical verification.

    The public page does not establish a historical effective date. Supplying an
    earlier trade_date is therefore a backtest assumption, never verified history.
    """
    return CostPolicy(
        version="vndirect-dta-public-2026-09-22",
        effective_from=trade_date,
        buy_fee_bps=Decimal(10),
        sell_fee_bps=Decimal(10),
        sell_tax_bps=Decimal(10),
        extra_slippage_bps=extra_slippage_bps,
        basis="PUBLIC_SCHEDULE_ASSUMPTION",
        account_plan="VNDIRECT_DTA",
        fee_source=(
            "https://www.vndirect.com.vn/bieu-phi-tai-khoan-da/; "
            "https://support.vndirect.com.vn/hc/vi/articles/14623187438873"
        ),
        fee_checked_at=checked_at,
    )
