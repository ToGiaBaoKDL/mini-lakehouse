"""Exchange-local session classification from effective-dated configuration."""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from enum import StrEnum
from zoneinfo import ZoneInfo

from t0_trading.configuration import SessionScheduleConfiguration


class MarketSession(StrEnum):
    PRE_OPEN = "pre_open"
    OPENING_AUCTION = "opening_auction"
    CONTINUOUS_AM = "continuous_am"
    LUNCH_BREAK = "lunch_break"
    CONTINUOUS_PM = "continuous_pm"
    CLOSING_AUCTION = "closing_auction"
    CLOSED = "closed"


TRADE_SESSIONS = frozenset(
    {
        MarketSession.OPENING_AUCTION,
        MarketSession.CONTINUOUS_AM,
        MarketSession.CONTINUOUS_PM,
        MarketSession.CLOSING_AUCTION,
    }
)


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value


def _instant(value: date, local_time: time, timezone: ZoneInfo) -> datetime:
    return datetime.combine(value, local_time, timezone).astimezone(UTC)


def trading_window(
    trade_date: date,
    *,
    timezone: ZoneInfo,
    schedule: SessionScheduleConfiguration,
) -> tuple[datetime, datetime]:
    """Return the configured opening and closing instants in UTC."""
    return (
        _instant(trade_date, schedule.opening_auction[0], timezone),
        _instant(trade_date, schedule.closing_auction[1], timezone),
    )


def covers_trading_window(
    connected_at: datetime,
    disconnected_at: datetime,
    *,
    trade_date: date,
    timezone: ZoneInfo,
    schedule: SessionScheduleConfiguration,
) -> bool:
    """Return whether one ordered connection covers the complete configured session."""
    connected_at = _aware(connected_at).astimezone(UTC)
    disconnected_at = _aware(disconnected_at).astimezone(UTC)
    if disconnected_at < connected_at:
        raise ValueError("connection timestamps are not ordered")
    market_open, market_close = trading_window(
        trade_date,
        timezone=timezone,
        schedule=schedule,
    )
    return connected_at <= market_open and disconnected_at >= market_close


def session_window(
    trade_date: date,
    session: MarketSession,
    *,
    timezone: ZoneInfo,
    schedule: SessionScheduleConfiguration,
) -> tuple[datetime, datetime]:
    """Return one configured exchange session in UTC."""
    windows = {
        MarketSession.OPENING_AUCTION: schedule.opening_auction,
        MarketSession.CONTINUOUS_AM: schedule.continuous_am,
        MarketSession.CONTINUOUS_PM: schedule.continuous_pm,
        MarketSession.CLOSING_AUCTION: schedule.closing_auction,
    }
    try:
        start, end = windows[session]
    except KeyError as error:
        raise ValueError(f"market session has no trading window: {session}") from error
    return _instant(trade_date, start, timezone), _instant(trade_date, end, timezone)


def session_at(
    timestamp: datetime,
    *,
    trade_date: date,
    timezone: ZoneInfo,
    schedule: SessionScheduleConfiguration,
) -> MarketSession:
    """Classify one instant against the clock of a certified trading date."""
    local = _aware(timestamp).astimezone(timezone)
    if local.date() != trade_date:
        return MarketSession.CLOSED
    value = local.timetz().replace(tzinfo=None)
    opening_start, opening_end = schedule.opening_auction
    morning_end = schedule.continuous_am[1]
    afternoon_start, afternoon_end = schedule.continuous_pm
    closing_end = schedule.closing_auction[1]
    if value < opening_start:
        return MarketSession.PRE_OPEN
    if value < opening_end:
        return MarketSession.OPENING_AUCTION
    if value < morning_end:
        return MarketSession.CONTINUOUS_AM
    if value < afternoon_start:
        return MarketSession.LUNCH_BREAK
    if value < afternoon_end:
        return MarketSession.CONTINUOUS_PM
    # SSI stamps the closing-auction executions at exactly the configured end second.
    if value <= closing_end:
        return MarketSession.CLOSING_AUCTION
    return MarketSession.CLOSED
