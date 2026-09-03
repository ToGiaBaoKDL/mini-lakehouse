from dataclasses import dataclass
from datetime import date

import pytest
from t0_trading.trading_dates import TradingDateError, resolve_completed_trade_date


@dataclass
class _Summary:
    trading_date: str


class _Market:
    def __init__(self, dates: list[str]) -> None:
        self.dates = dates
        self.calls: list[tuple[str, str, str]] = []

    def get_securities_summary_by_index_historical(
        self, index: str, from_date: str, to_date: str
    ) -> list[_Summary]:
        self.calls.append((index, from_date, to_date))
        return [_Summary(value) for value in self.dates]


def test_latest_completed_date_skips_weekends_and_holidays() -> None:
    market = _Market(["2026/08/27", "2026/08/28"])

    result = resolve_completed_trade_date(
        market,
        before_date=date(2026, 9, 3),
    )

    assert result == date(2026, 8, 28)
    assert market.calls == [("VN30", "2026/08/03", "2026/09/02")]


def test_requested_date_must_be_completed_and_observed() -> None:
    market = _Market(["2026/08/28"])

    assert resolve_completed_trade_date(
        market,
        before_date=date(2026, 9, 3),
        requested_date=date(2026, 8, 28),
    ) == date(2026, 8, 28)
    with pytest.raises(TradingDateError, match="not an SSI-observed"):
        resolve_completed_trade_date(
            market,
            before_date=date(2026, 9, 3),
            requested_date=date(2026, 9, 2),
        )


@pytest.mark.parametrize("requested", [date(2026, 9, 3), date(2026, 9, 4)])
def test_current_and_future_dates_fail_before_calling_ssi(requested: date) -> None:
    market = _Market([])

    with pytest.raises(TradingDateError, match="earlier than"):
        resolve_completed_trade_date(
            market,
            before_date=date(2026, 9, 3),
            requested_date=requested,
        )

    assert market.calls == []
