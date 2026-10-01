"""Conservative top-of-book paper matching, never a claim about actual exchange fills."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal

from t0_trading.configuration import ContextVersion, PaperExecutionVersion
from t0_trading.execution.engine import reduce_paper_order
from t0_trading.execution.model import (
    PaperEventType,
    PaperFillEvidence,
    PaperMarketSnapshot,
    PaperOrderEvent,
    PaperOrderIntent,
)
from t0_trading.identity import canonical_json, sha256

TERMINAL_STATUSES = frozenset({"FILLED", "REJECTED", "CANCELLED", "EXPIRED"})


def lifecycle_event(
    intent: PaperOrderIntent,
    events: Sequence[PaperOrderEvent],
    *,
    event_type: PaperEventType,
    at: datetime,
    reason: str | None = None,
    fill: PaperFillEvidence | None = None,
) -> PaperOrderEvent:
    """One deterministic event constructor; the existing reducer remains authoritative."""
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("paper event time must be timezone-aware")
    at = at.astimezone(UTC)
    state = reduce_paper_order(intent, events)
    filled = state.cumulative_filled_quantity
    average = state.average_fill_price
    if fill is not None:
        average = ((average or Decimal(0)) * filled + fill.fill_price * fill.filled_quantity) / (
            filled + fill.filled_quantity
        )
        filled += fill.filled_quantity
    evidence = sha256(
        canonical_json(
            {
                "intent": intent.sha256,
                "previous": state.last_event_sha256,
                "event": event_type,
                "at": at.isoformat(),
                "reason": reason,
                "fill": fill.model_dump(mode="json") if fill else None,
            }
        )
    )
    event = PaperOrderEvent(
        intent_sha256=intent.sha256,
        sequence=state.event_count + 1,
        event_type=event_type,
        occurred_at=at,
        cumulative_filled_quantity=filled,
        average_fill_price=average,
        reason=reason,
        adapter_event_id=f"top-of-book-v1:{evidence}",
        adapter_evidence_sha256=evidence,
        fill_evidence=fill,
    )
    reduce_paper_order(intent, (*events, event))
    return event


def match_quote(
    intent: PaperOrderIntent,
    events: Sequence[PaperOrderEvent],
    market: PaperMarketSnapshot,
    policy: PaperExecutionVersion,
    context_policy: ContextVersion,
    *,
    available_quantity: int,
) -> PaperOrderEvent | None:
    """Match one order from a shared quote budget; callers allocate budget across orders."""
    state = reduce_paper_order(intent, events)
    if state.status in TERMINAL_STATUSES:
        return None
    if intent.execution_configuration_sha256 != policy.sha256 or market.symbol != intent.symbol:
        raise ValueError("paper matching policy or symbol mismatch")
    if market.observed_at < intent.created_at or (
        events and market.observed_at < events[-1].occurred_at
    ):
        raise ValueError("paper matching cannot rewind order time")
    if market.observed_at >= intent.expires_at:
        return lifecycle_event(
            intent, events, event_type="EXPIRED", at=market.observed_at, reason="TIME_IN_FORCE"
        )
    if state.status == "CREATED":
        raise ValueError("paper order must be accepted before matching")
    if market.market_status not in context_policy.tradable_market_statuses:
        return lifecycle_event(
            intent,
            events,
            event_type="CANCELLED",
            at=market.observed_at,
            reason="MARKET_STATUS_INELIGIBLE",
        )
    if (
        market.quote_received_at <= intent.created_at
        or (market.observed_at - market.quote_received_at).total_seconds()
        > policy.maximum_quote_age_seconds
    ):
        return None
    buying = intent.action == "BUY"
    price = market.best_ask_price if buying else market.best_bid_price
    displayed = market.best_ask_quantity if buying else market.best_bid_quantity
    if available_quantity < 0 or available_quantity > displayed:
        raise ValueError("paper quote budget exceeds displayed liquidity")
    if (buying and price > intent.limit_price) or (not buying and price < intent.limit_price):
        return None
    # Only whole configured lots; displayed liquidity is an upper bound, not a fill guarantee.
    quantity = min(intent.quantity - state.cumulative_filled_quantity, available_quantity)
    quantity = quantity // policy.lot_size * policy.lot_size
    if not quantity:
        return None
    identity = (market.stream_session_id, market.receive_sequence)
    if any(
        item.fill_evidence is not None
        and (item.fill_evidence.stream_session_id, item.fill_evidence.receive_sequence) == identity
        for item in events
    ):
        return None
    fill = PaperFillEvidence(
        symbol=intent.symbol,
        action=intent.action,
        observed_at=market.quote_received_at,
        stream_session_id=market.stream_session_id,
        receive_sequence=market.receive_sequence,
        filled_quantity=quantity,
        available_quantity=available_quantity,
        fill_price=price,
        source_sha256=market.source_sha256,
    )
    return lifecycle_event(
        intent,
        events,
        at=market.observed_at,
        fill=fill,
        event_type="FILLED"
        if quantity + state.cumulative_filled_quantity == intent.quantity
        else "PARTIALLY_FILLED",
    )
