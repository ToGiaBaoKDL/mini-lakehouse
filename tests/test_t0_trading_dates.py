from dataclasses import dataclass
from datetime import date

import pytest
from t0_trading.trading_dates import TradingDateError, require_observed_trade_date


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


def test_trade_date_must_be_observed_by_ssi() -> None:
    market = _Market(["2026/08/28"])

    assert require_observed_trade_date(
        market,
        trade_date=date(2026, 8, 28),
    ) == date(2026, 8, 28)
    assert market.calls == [("VN30", "2026/08/28", "2026/08/28")]

    with pytest.raises(TradingDateError, match="not an SSI-observed"):
        require_observed_trade_date(
            market,
            trade_date=date(2026, 9, 2),
        )
