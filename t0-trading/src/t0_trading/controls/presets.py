"""Broker-specific assumptions kept outside generic accounting and execution."""

from datetime import UTC, date, datetime
from decimal import Decimal

from t0_trading.controls.model import CostPolicy

PUBLIC_VNDIRECT_DTA_CHECKED_AT = datetime(2026, 9, 22, tzinfo=UTC)


def public_vndirect_dta_costs(
    trade_date: date, *, checked_at: datetime, extra_slippage_bps: Decimal = Decimal(0)
) -> CostPolicy:
    """Public DTA online tariff, not account-statement or historical verification."""
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
