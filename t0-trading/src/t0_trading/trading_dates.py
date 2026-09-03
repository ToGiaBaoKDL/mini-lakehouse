"""Resolve completed Vietnamese trading dates from official SSI history."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from typing import Protocol


class MarketHistory(Protocol):
    def get_securities_summary_by_index_historical(
        self,
        index: str,
        from_date: str,
        to_date: str,
    ) -> Sequence[object]: ...


class TradingDateError(ValueError):
    """A requested or discoverable completed trading date is unavailable."""


def _record_date(record: object) -> date:
    value = record.get("trading_date") if isinstance(record, Mapping) else None
    if value is None:
        value = getattr(record, "trading_date", None)
    if not isinstance(value, str):
        raise TradingDateError("SSI trading history returned an invalid trading date.")
    try:
        return date.fromisoformat(value.replace("/", "-"))
    except ValueError as error:
        raise TradingDateError("SSI trading history returned an invalid trading date.") from error


def resolve_completed_trade_date(
    market: MarketHistory,
    *,
    before_date: date,
    requested_date: date | None = None,
    index: str = "VN30",
    lookback_days: int = 31,
) -> date:
    """Resolve one SSI-observed trading date strictly before ``before_date``."""
    if requested_date is not None and requested_date >= before_date:
        raise TradingDateError("trade_date must be earlier than the current market date.")
    if lookback_days < 1:
        raise ValueError("lookback_days must be positive")

    end_date = requested_date or before_date - timedelta(days=1)
    start_date = requested_date or before_date - timedelta(days=lookback_days)
    records = market.get_securities_summary_by_index_historical(
        index,
        start_date.strftime("%Y/%m/%d"),
        end_date.strftime("%Y/%m/%d"),
    )
    observed_dates = {
        observed
        for record in records
        if start_date <= (observed := _record_date(record)) <= end_date
    }
    if requested_date is not None:
        if requested_date not in observed_dates:
            raise TradingDateError("trade_date is not an SSI-observed completed trading day.")
        return requested_date
    if not observed_dates:
        raise TradingDateError("SSI returned no completed trading day in the lookback window.")
    return max(observed_dates)
