"""Bounded shadow handoff; no SDK, database, notification or broker I/O on capture's thread."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from queue import Empty, Full, Queue
from threading import Event, Lock, Thread

from pydantic import BaseModel, ConfigDict

from t0_trading.arbitration.selection import RealtimeSelection
from t0_trading.persistence import TransientDatabaseError


class RealtimeHealth(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool
    failure: str | None
    failed_symbols: tuple[str, ...]
    delivered: int
    stale: int
    duplicates: int
    disconnected: int
    queued: int
    transient_errors: int
    retry_exhausted: int


class RealtimeWorker:
    """Bounded session-local handoff of immutable selections.

    Permanent sink failures disable that symbol; transient DB writes retry within the decision TTL.
    Disconnect invalidation takes priority over writes. Overflow disables the worker.
    A caller must not attach an external side effect without its own durable idempotency key.
    """

    def __init__(
        self,
        sink: Callable[[RealtimeSelection], None],
        *,
        maximum_delay: timedelta,
        capacity: int = 128,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        invalidate: Callable[[datetime], None] | None = None,
        maximum_attempts: int = 3,
        retry_delay: float = 0.1,
    ) -> None:
        if (
            maximum_delay <= timedelta(0)
            or capacity < 1
            or maximum_attempts < 1
            or retry_delay <= 0
        ):
            raise ValueError("realtime delay, capacity and retry budget must be positive")
        self._sink = sink
        self._maximum_delay = maximum_delay
        self._clock = clock
        self._invalidate = invalidate
        self._maximum_attempts = maximum_attempts
        self._retry_delay = retry_delay
        self._queue: Queue[RealtimeSelection] = Queue(maxsize=capacity)
        self._stop = Event()
        self._lock = Lock()
        self._failure: str | None = None
        self._failed_symbols: set[str] = set()
        self._last_decision: dict[str, tuple[datetime, str]] = {}
        self._not_before: datetime | None = None
        self._invalidated_through: datetime | None = None
        self._delivered = self._stale = self._duplicates = self._disconnected = 0
        self._transient_errors = self._retry_exhausted = 0
        self._thread = Thread(target=self._run, name="t0-realtime-shadow", daemon=True)
        self._thread.start()

    def offer(self, selection: RealtimeSelection) -> None:
        """Nonblocking producer: never run the sink or wait for capacity here."""
        with self._lock:
            if self._stop.is_set() or selection.candidate.symbol in self._failed_symbols:
                return
            try:
                self._queue.put_nowait(selection)
            except Full:
                self._failure = "QUEUE_OVERFLOW"
                self._stop.set()

    def disconnected(self, at: datetime) -> None:
        """Invalidate queued pre-disconnect decisions; warmup stays owned by the feature engine."""
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("disconnect clock must be aware")
        with self._lock:
            self._not_before = max(at, self._not_before) if self._not_before else at

    def _run(self) -> None:
        while True:
            synchronized = self._sync_invalidation()
            # Best-effort final invalidation belongs to this thread, never the capture caller.
            if self._stop.is_set():
                return
            if not synchronized:
                self._stop.wait(self._retry_delay)
                continue
            try:
                selection = self._queue.get(timeout=0.1)
            except Empty:
                continue
            try:
                symbol = selection.candidate.symbol
                at = selection.candidate.decision_at
                with self._lock:
                    if self._stop.is_set() or symbol in self._failed_symbols:
                        continue
                    previous = self._last_decision.get(symbol)
                    if (
                        previous is not None
                        and at == previous[0]
                        and selection.arbitration.sha256 != previous[1]
                    ):
                        self._failed_symbols.add(symbol)
                        self._failure = "CONFLICTING_SELECTION"
                        continue
                    if previous is not None and at <= previous[0]:
                        self._duplicates += 1
                        continue
                    self._last_decision[symbol] = (at, selection.arbitration.sha256)
                if self._deliver(selection):
                    with self._lock:
                        self._delivered += 1
                        if self._failure is not None and self._failure.startswith("TRANSIENT_DB"):
                            self._failure = None
            except Exception as error:
                with self._lock:
                    self._failed_symbols.add(selection.candidate.symbol)
                    self._failure = f"SINK_OR_CLOCK_ERROR:{type(error).__name__}"
            finally:
                self._queue.task_done()

    def _transient_failure(self) -> None:
        with self._lock:
            self._transient_errors += 1
            self._failure = "TRANSIENT_DB_UNAVAILABLE"

    def _sync_invalidation(self) -> bool:
        with self._lock:
            at = self._not_before
            if self._invalidate is None or at is None or at == self._invalidated_through:
                return True
        try:
            self._invalidate(at)
        except TransientDatabaseError:
            self._transient_failure()
            return False
        except Exception as error:
            with self._lock:
                self._failure = f"INVALIDATION_ERROR:{type(error).__name__}"
                self._stop.set()
            return False
        with self._lock:
            self._invalidated_through = at
            if self._failure == "TRANSIENT_DB_UNAVAILABLE":
                self._failure = None
            return at == self._not_before

    def _can_deliver(self, selection: RealtimeSelection) -> bool:
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("realtime clock must be aware")
        with self._lock:
            if self._stop.is_set():
                return False
            at = selection.candidate.decision_at
            if self._not_before is not None and at <= self._not_before:
                self._disconnected += 1
                return False
            if not timedelta(0) <= now - at <= self._maximum_delay:
                self._stale += 1
                return False
            return True

    def _deliver(self, selection: RealtimeSelection) -> bool:
        attempts = 0
        while self._can_deliver(selection):
            if not self._sync_invalidation():
                self._stop.wait(self._retry_delay)
                continue
            if not self._can_deliver(selection):
                return False
            try:
                self._sink(selection)
                return True
            except TransientDatabaseError:
                self._transient_failure()
                attempts += 1
                if attempts >= self._maximum_attempts:
                    with self._lock:
                        self._retry_exhausted += 1
                    return False
                self._stop.wait(self._retry_delay * 2 ** (attempts - 1))
        return False

    def health(self) -> RealtimeHealth:
        with self._lock:
            return RealtimeHealth(
                enabled=not self._stop.is_set(),
                failure=self._failure,
                failed_symbols=tuple(sorted(self._failed_symbols)),
                delivered=self._delivered,
                stale=self._stale,
                duplicates=self._duplicates,
                disconnected=self._disconnected,
                queued=self._queue.qsize(),
                transient_errors=self._transient_errors,
                retry_exhausted=self._retry_exhausted,
            )

    def close(self, timeout: float = 1.0) -> RealtimeHealth:
        """Discard pending selections, never drain old decisions as new signals on shutdown.

        A sink already in flight must implement its own I/O timeout; Python cannot cancel it.
        """
        self._stop.set()
        self._thread.join(timeout=max(0.0, timeout))
        with self._lock:
            if self._thread.is_alive():
                self._failure = "SHUTDOWN_TIMEOUT"
            while True:
                try:
                    self._queue.get_nowait()
                    self._queue.task_done()
                except Empty:
                    break
        return self.health()
