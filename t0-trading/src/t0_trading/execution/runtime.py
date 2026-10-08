"""Atomic paper-session transitions reusing the existing planner and ledger reducers.

This consumer is separate from capture. It never opens a broker connection or credits unsettled
sale proceeds. A repository must commit each returned state using its predecessor hash.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Literal, Protocol
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, model_validator

from t0_trading.configuration import TradingConfiguration
from t0_trading.controls import AccountSnapshot, CostPolicy
from t0_trading.execution.engine import plan_paper_exit, plan_paper_order, reduce_paper_order
from t0_trading.execution.ledger import (
    initialize_paper_ledger,
    reserve_paper_entry,
    reserve_paper_exit,
    settle_paper_entry,
    settle_paper_exit,
)
from t0_trading.execution.matching import TERMINAL_STATUSES, lifecycle_event, match_quote
from t0_trading.execution.model import (
    PaperExecutionRequest,
    PaperExitRequest,
    PaperMarketSnapshot,
    PaperOrderEvent,
    PaperOrderIntent,
    PaperResourceLedger,
)
from t0_trading.execution.readiness import PaperReadiness
from t0_trading.identity import canonical_json, sha256
from t0_trading.promotion.evidence import resolve_research_lineage

Operation = PaperOrderIntent | PaperOrderEvent


class PaperOrderBlocked(ValueError):
    """Preserve the planner's ordered reasons at the consumer boundary."""

    def __init__(self, reasons: tuple[str, ...]) -> None:
        self.reasons = reasons
        super().__init__("paper order blocked")


class PaperSession(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    account: AccountSnapshot
    costs: CostPolicy
    ledger: PaperResourceLedger
    revision: int = Field(ge=0, default=0)
    operations: tuple[Operation, ...] = ()
    markets: tuple[PaperMarketSnapshot, ...] = ()

    @model_validator(mode="after")
    def reconcile(self) -> PaperSession:
        if not self.costs.contains(self.ledger.trade_date):
            raise ValueError("paper costs must cover the session date")
        ledger = initialize_paper_ledger(self.account, self.ledger.trade_date)
        intents: dict[str, PaperOrderIntent] = {}
        events: dict[str, list[PaperOrderEvent]] = {}
        entries: set[str] = set()
        liquidity: dict[tuple[str, int, str, str], tuple[int, int, str]] = {}
        last_operation_at = self.account.as_of
        for operation in self.operations:
            at = (
                operation.created_at
                if isinstance(operation, PaperOrderIntent)
                else operation.occurred_at
            )
            if at < last_operation_at:
                raise ValueError("paper operations cannot rewind the session clock")
            last_operation_at = at
            if isinstance(operation, PaperOrderIntent):
                if operation.sha256 in intents:
                    raise ValueError("duplicate paper intent")
                if operation.leg == "ENTRY":
                    if operation.arbitration_sha256 in entries:
                        raise ValueError("arbitration already planned")
                    entries.add(operation.arbitration_sha256)
                    ledger = reserve_paper_entry(ledger, operation)
                else:
                    ledger = reserve_paper_exit(ledger, operation)
                intents[operation.sha256] = operation
                events[operation.sha256] = []
            else:
                intent = intents.get(operation.intent_sha256)
                if intent is None:
                    raise ValueError("paper event has no intent")
                history = events[intent.sha256]
                history.append(operation)
                state = reduce_paper_order(intent, history)
                fill = operation.fill_evidence
                if fill is not None:
                    key = (fill.stream_session_id, fill.receive_sequence, fill.symbol, fill.action)
                    used, capacity, digest = liquidity.get(
                        key, (0, fill.available_quantity, fill.source_sha256)
                    )
                    if digest != fill.source_sha256 or used + fill.filled_quantity > capacity:
                        raise ValueError("paper operations reuse or conflict on quote liquidity")
                    liquidity[key] = (used + fill.filled_quantity, capacity, digest)
                if state.status in TERMINAL_STATUSES:
                    settle = settle_paper_entry if intent.leg == "ENTRY" else settle_paper_exit
                    ledger = settle(ledger, intent, state, self.costs)
        if ledger != self.ledger:
            raise ValueError("paper ledger does not reconcile to immutable operations")
        symbols = tuple(item.symbol for item in self.markets)
        if symbols != tuple(sorted(set(symbols))):
            raise ValueError("paper quote cursors must be unique and ordered")
        return self

    @property
    def sha256(self) -> str:
        return sha256(canonical_json(self.model_dump(mode="json")))

    @property
    def intents(self) -> tuple[PaperOrderIntent, ...]:
        return tuple(item for item in self.operations if isinstance(item, PaperOrderIntent))

    def events(self, intent: PaperOrderIntent) -> tuple[PaperOrderEvent, ...]:
        return tuple(
            item
            for item in self.operations
            if isinstance(item, PaperOrderEvent) and item.intent_sha256 == intent.sha256
        )


def _append(session: PaperSession, operation: Operation) -> PaperSession:
    ledger = session.ledger
    if isinstance(operation, PaperOrderIntent):
        reserve = reserve_paper_entry if operation.leg == "ENTRY" else reserve_paper_exit
        ledger = reserve(ledger, operation)
    else:
        intent = next(item for item in session.intents if item.sha256 == operation.intent_sha256)
        state = reduce_paper_order(intent, (*session.events(intent), operation))
        if state.status in TERMINAL_STATUSES:
            settle = settle_paper_entry if intent.leg == "ENTRY" else settle_paper_exit
            ledger = settle(ledger, intent, state, session.costs)
    return session.model_copy(
        update={"ledger": ledger, "operations": (*session.operations, operation)}
    )


def _finish(previous: PaperSession, current: PaperSession) -> PaperSession:
    if current == previous:
        return previous
    return PaperSession.model_validate({**current.model_dump(), "revision": previous.revision + 1})


def submit_entry(
    session: PaperSession,
    request: PaperExecutionRequest,
    readiness: PaperReadiness,
    configuration: TradingConfiguration,
) -> PaperSession:
    if readiness.mode != "PAPER":
        return session
    lineage = readiness.research_lineage
    if lineage is None:
        raise ValueError("paper admission requires research lineage")
    if (
        readiness.promotion != request.promotion
        or readiness.trade_date != request.candidate.trade_date
        or readiness.previous_session >= readiness.trade_date
        or request.account != session.account
        or request.costs != session.costs
        or readiness.trade_date != session.ledger.trade_date
        or lineage
        != resolve_research_lineage(configuration, session.ledger.trade_date, session.costs)
        or request.candidate.baseline_version != lineage.baseline_version
        or request.candidate.context_configuration_sha256 != lineage.context_configuration_sha256
    ):
        raise ValueError("paper admission lineage mismatch")
    if any(
        item.leg == "ENTRY" and item.arbitration_sha256 == request.arbitration.sha256
        for item in session.intents
    ):
        return session
    if request.resources != session.ledger:
        raise ValueError("paper planner used stale resources")
    day = session.ledger.trade_date
    policy = configuration.resolve_paper_execution(day)
    gate = configuration.resolve_promotion_gate(day)
    arbitration = configuration.resolve_candidate_arbitration(day)
    if policy is None or gate is None or arbitration is None:
        raise PaperOrderBlocked(("NO_EFFECTIVE_POLICY",))
    plan = plan_paper_order(request, policy, arbitration, configuration.resolve_context(day), gate)
    if plan.intent is None:
        raise PaperOrderBlocked(plan.reasons)
    result = _append(session, plan.intent)
    result = _append(
        result, lifecycle_event(plan.intent, (), event_type="ACCEPTED", at=plan.intent.created_at)
    )
    return _finish(session, result)


def submit_exit(
    session: PaperSession,
    entry: PaperOrderIntent,
    market: PaperMarketSnapshot,
    configuration: TradingConfiguration,
) -> PaperSession:
    """Managing an existing exposure does not require a new entry promotion PASS."""
    if entry not in session.intents:
        raise ValueError("paper exit parent is unknown")
    if not any(
        item.entry_intent_sha256 == entry.sha256 and item.status == "OPEN"
        for item in session.ledger.reservations
    ):
        return session
    policy = configuration.resolve_paper_execution(entry.trade_date)
    if policy is None:
        raise PaperOrderBlocked(("NO_EFFECTIVE_POLICY",))
    plan = plan_paper_exit(
        PaperExitRequest(
            entry_intent=entry,
            entry_state=reduce_paper_order(entry, session.events(entry)),
            market=market,
            resources=session.ledger,
        ),
        policy,
        configuration.resolve_context(entry.trade_date),
    )
    if plan.intent is None:
        raise PaperOrderBlocked(plan.reasons)
    result = _append(session, plan.intent)
    result = _append(
        result, lifecycle_event(plan.intent, (), event_type="ACCEPTED", at=plan.intent.created_at)
    )
    return _finish(session, result)


def advance_clock(session: PaperSession, at: datetime) -> PaperSession:
    """Expire orders even during disconnects and quiet markets; never wait for a quote."""
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("paper clock must be timezone-aware")
    result = session
    for intent in session.intents:
        events = result.events(intent)
        if (
            reduce_paper_order(intent, events).status not in TERMINAL_STATUSES
            and at >= intent.expires_at
        ):
            result = _append(
                result,
                lifecycle_event(
                    intent, events, event_type="EXPIRED", at=at, reason="TIME_IN_FORCE"
                ),
            )
    return _finish(session, result)


def cancel_order(
    session: PaperSession, intent: PaperOrderIntent, at: datetime, *, reason: str
) -> PaperSession:
    if intent not in session.intents:
        raise ValueError("paper cancel intent is unknown")
    events = session.events(intent)
    if reduce_paper_order(intent, events).status in TERMINAL_STATUSES:
        return session
    return _finish(
        session,
        _append(
            session, lifecycle_event(intent, events, event_type="CANCELLED", at=at, reason=reason)
        ),
    )


def process_quote(
    session: PaperSession, market: PaperMarketSnapshot, configuration: TradingConfiguration
) -> PaperSession:
    timezone = ZoneInfo(configuration.resolve(session.ledger.trade_date).market.timezone)
    if market.observed_at.astimezone(timezone).date() != session.ledger.trade_date:
        raise ValueError("paper quote must belong to the resource session date")
    previous = next((item for item in session.markets if item.symbol == market.symbol), None)
    if previous is not None:
        if previous == market:
            return session
        if (
            (previous.stream_session_id, previous.receive_sequence)
            == (market.stream_session_id, market.receive_sequence)
            or market.quote_received_at < previous.quote_received_at
            or market.observed_at < previous.observed_at
            or (
                market.stream_session_id == previous.stream_session_id
                and market.receive_sequence <= previous.receive_sequence
            )
        ):
            raise ValueError("paper quote cursor conflict or regression")
    policy = configuration.resolve_paper_execution(session.ledger.trade_date)
    if policy is None:
        return session
    context = configuration.resolve_context(session.ledger.trade_date)
    result = session
    budgets = {"BUY": market.best_ask_quantity, "SELL": market.best_bid_quantity}
    # An unchanged book on a later callback does not prove replenished liquidity.
    if previous is not None:
        if previous.best_ask_price == market.best_ask_price:
            budgets["BUY"] = max(0, market.best_ask_quantity - previous.best_ask_quantity)
        if previous.best_bid_price == market.best_bid_price:
            budgets["SELL"] = max(0, market.best_bid_quantity - previous.best_bid_quantity)
    for intent in sorted(
        session.intents, key=lambda item: (item.priority, item.created_at, item.sha256)
    ):
        if intent.symbol != market.symbol or intent.created_at > market.observed_at:
            continue
        event = match_quote(
            intent,
            result.events(intent),
            market,
            policy,
            context,
            available_quantity=budgets[intent.action],
        )
        if event is not None:
            result = _append(result, event)
            if event.fill_evidence is not None:
                budgets[intent.action] -= event.fill_evidence.filled_quantity
    markets = tuple(
        sorted(
            (*[item for item in session.markets if item.symbol != market.symbol], market),
            key=lambda item: item.symbol,
        )
    )
    return _finish(session, result.model_copy(update={"markets": markets}))


class PaperRepository(Protocol):
    def load(self) -> PaperSession: ...
    def commit(self, previous: PaperSession, current: PaperSession) -> None: ...


class PaperRuntimeResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    status: Literal["COMMITTED", "NO_CHANGE", "BLOCKED", "OBSERVE_ONLY", "ERROR"]
    reason: str
    block_reasons: tuple[str, ...] = ()
    state_sha256: str | None = None


class PaperRuntime:
    """Failure-isolated consumer boundary. No exception text or account data enters telemetry."""

    def __init__(
        self,
        repository: PaperRepository,
        configuration: TradingConfiguration,
        *,
        enabled: bool = False,
    ) -> None:
        self._repository = repository
        self._configuration = configuration
        self._enabled = enabled

    def _run(self, transition: Callable[[PaperSession], PaperSession]) -> PaperRuntimeResult:
        try:
            previous = self._repository.load()
            state = transition(previous)
            changed = state != previous
            if changed:
                # Acknowledge only after atomic persistence; never blindly retry a stale plan.
                self._repository.commit(previous, state)
            return PaperRuntimeResult(
                status="COMMITTED" if changed else "NO_CHANGE",
                reason="PAPER_ONLY" if changed else "IDEMPOTENT_OR_NOT_APPLICABLE",
                state_sha256=state.sha256,
            )
        except PaperOrderBlocked as error:
            return PaperRuntimeResult(
                status="BLOCKED", reason="PLANNER_BLOCKED", block_reasons=error.reasons
            )
        except Exception as error:
            return PaperRuntimeResult(status="ERROR", reason=type(error).__name__)

    def entry(
        self, request: PaperExecutionRequest, readiness: PaperReadiness
    ) -> PaperRuntimeResult:
        if not self._enabled or readiness.mode != "PAPER":
            return PaperRuntimeResult(
                status="OBSERVE_ONLY",
                reason=readiness.reason if self._enabled else "PAPER_DISABLED",
            )
        return self._run(lambda state: submit_entry(state, request, readiness, self._configuration))

    def quote(self, market: PaperMarketSnapshot) -> PaperRuntimeResult:
        if not self._enabled:
            return PaperRuntimeResult(status="OBSERVE_ONLY", reason="PAPER_DISABLED")
        return self._run(lambda state: process_quote(state, market, self._configuration))

    def clock(self, at: datetime) -> PaperRuntimeResult:
        return self._run(lambda state: advance_clock(state, at))

    def exit(self, entry: PaperOrderIntent, market: PaperMarketSnapshot) -> PaperRuntimeResult:
        if not self._enabled:
            return PaperRuntimeResult(status="OBSERVE_ONLY", reason="PAPER_DISABLED")
        return self._run(lambda state: submit_exit(state, entry, market, self._configuration))

    def cancel(self, intent: PaperOrderIntent, at: datetime, *, reason: str) -> PaperRuntimeResult:
        return self._run(lambda state: cancel_order(state, intent, at, reason=reason))
