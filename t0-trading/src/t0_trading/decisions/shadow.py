"""Durable local shadow journal over the deterministic decision clock."""

from __future__ import annotations

import hashlib
import os
from collections import Counter, deque
from collections.abc import Callable, Sequence
from contextlib import suppress
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from t0_trading.capture.reader import StreamGap
from t0_trading.configuration import (
    DecisionVersion,
    OutcomeVersion,
    StrategyVersion,
    TradingVersion,
)
from t0_trading.decisions.engine import DecisionEngine
from t0_trading.decisions.model import DecisionAction
from t0_trading.features import FeatureEngine, FeatureSnapshot, decision_times
from t0_trading.identity import canonical_json, sha256
from t0_trading.market import StreamEnvelope

_ACTIONS: tuple[DecisionAction, ...] = ("BUY", "SELL", "ABSTAIN")
_COMPLETED_RETENTION = timedelta(days=14)
_PARTIAL_RETENTION = timedelta(days=3)


def _utc(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


def prune_shadow_journals(directory: Path, *, observed_at: datetime | None = None) -> None:
    """Remove only owned shadow artifacts after their local diagnostic lifetime."""
    if not directory.exists():
        return
    now = _utc(observed_at or datetime.now(UTC), "observed_at")
    for path in directory.iterdir():
        if path.is_symlink() or not path.is_file():
            continue
        if path.name.endswith(".partial"):
            retention = _PARTIAL_RETENTION
        elif path.name.endswith((".jsonl", ".manifest.json")):
            retention = _COMPLETED_RETENTION
        else:
            continue
        modified_at = datetime.fromtimestamp(path.stat().st_mtime, UTC)
        if modified_at <= now - retention:
            path.unlink()


class ShadowJournalManifest(BaseModel):
    """Commit marker for one complete local shadow decision journal."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    trade_date: date
    first_connected_at: datetime
    completed_at: datetime
    stream_session_ids: tuple[str, ...]
    capture_manifest_uris: tuple[str, ...]
    configuration_version: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    configuration_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    feature_version: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    strategy_version: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    strategy_configuration_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    outcome_version: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    outcome_configuration_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    decision_version: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    decision_configuration_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_decision_count: int = Field(ge=1)
    decision_count: int = Field(ge=1)
    action_counts: dict[DecisionAction, int]
    journal_file: str
    journal_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("first_connected_at", "completed_at")
    @classmethod
    def normalize_timestamp(cls, value: datetime) -> datetime:
        return _utc(value, "shadow journal timestamp")

    @model_validator(mode="after")
    def validate_manifest(self) -> ShadowJournalManifest:
        expected_actions = set(_ACTIONS)
        if self.first_connected_at > self.completed_at:
            raise ValueError("shadow journal timestamps are not ordered")
        if (
            not self.stream_session_ids
            or len(set(self.stream_session_ids)) != len(self.stream_session_ids)
            or len(self.capture_manifest_uris) != len(self.stream_session_ids)
        ):
            raise ValueError("shadow journal capture sessions are inconsistent")
        for session_id, uri in zip(
            self.stream_session_ids,
            self.capture_manifest_uris,
            strict=True,
        ):
            parsed = urlparse(uri)
            if (
                parsed.scheme != "s3"
                or not parsed.netloc
                or f"/trade_date={self.trade_date.isoformat()}/" not in parsed.path
                or not parsed.path.endswith(f"/session={session_id}/manifest.json")
            ):
                raise ValueError("shadow journal capture manifest lineage is inconsistent")
        if (
            self.decision_count != self.expected_decision_count
            or set(self.action_counts) != expected_actions
            or any(count < 0 for count in self.action_counts.values())
            or sum(self.action_counts.values()) != self.decision_count
        ):
            raise ValueError("shadow journal decision summary is inconsistent")
        if Path(self.journal_file).name != self.journal_file or not self.journal_file.endswith(
            ".jsonl"
        ):
            raise ValueError("shadow journal file name is invalid")
        return self

    def canonical_bytes(self) -> bytes:
        return canonical_json(self.model_dump(mode="json"))

    @property
    def sha256(self) -> str:
        return sha256(self.canonical_bytes())


class ShadowDecisionJournal:
    """Consume captured envelopes without allowing shadow failures to affect capture."""

    def __init__(
        self,
        output: Path,
        trade_date: date,
        configuration: TradingVersion,
        strategy_policy: StrategyVersion,
        outcome_policy: OutcomeVersion,
        decision_policy: DecisionVersion,
        *,
        on_error: Callable[[Exception], None] | None = None,
    ) -> None:
        if output.suffix != ".jsonl":
            raise ValueError("shadow journal output must use a .jsonl suffix")
        self._trade_date = trade_date
        self._configuration = configuration
        self._strategy_policy = strategy_policy
        self._outcome_policy = outcome_policy
        self._decision_policy = decision_policy
        self._timezone = ZoneInfo(configuration.market.timezone)
        self._feature_engine = FeatureEngine(configuration)
        self._decision_engine = DecisionEngine(
            configuration,
            strategy_policy,
            outcome_policy,
            decision_policy,
        )
        self._decision_times = tuple(decision_times(configuration, trade_date))
        if not self._decision_times:
            raise ValueError("shadow journal configuration has no decision clocks")
        self._next_decision = 0
        self._pending: deque[StreamEnvelope] = deque()
        self._last_ingested_at: datetime | None = None
        self._open_gap: datetime | None = None
        self._gaps: list[StreamGap] = []
        self._session_ids: list[str] = []
        self._first_connected_at: datetime | None = None
        self._watermark_delay = timedelta(seconds=configuration.features.cadence_seconds)
        self._on_error = on_error
        self._failed = False
        self._closed = False
        self._digest = hashlib.sha256()
        self._action_counts: Counter[DecisionAction] = Counter()
        self.decision_count = 0
        self.manifest: ShadowJournalManifest | None = None
        self.output = output
        self.partial_output = output.with_suffix(".jsonl.partial")
        self.manifest_output = output.with_suffix(".manifest.json")
        self.partial_manifest_output = Path(f"{self.manifest_output}.partial")
        paths = (
            self.output,
            self.partial_output,
            self.manifest_output,
            self.partial_manifest_output,
        )
        if any(path.exists() for path in paths):
            raise FileExistsError("shadow journal output already exists")
        output.parent.mkdir(parents=True, exist_ok=True)
        self._stream = self.partial_output.open("xb")

    @property
    def failed(self) -> bool:
        return self._failed

    @property
    def journal_sha256(self) -> str | None:
        return None if not self._closed or self._failed else self._digest.hexdigest()

    @property
    def action_counts(self) -> dict[DecisionAction, int]:
        return {action: self._action_counts[action] for action in _ACTIONS}

    def _fail(self, error: Exception) -> None:
        if self._failed:
            return
        self._failed = True
        self._stream.close()
        if self._on_error is not None:
            with suppress(Exception):
                self._on_error(error)

    def _safe(self, operation: Callable[[], None]) -> None:
        if self._failed or self._closed:
            return
        try:
            operation()
        except Exception as error:
            self._fail(error)

    def connected(self, stream_session_id: str, connected_at: datetime) -> None:
        """Close an unavailable interval when a fresh SDK connection succeeds."""

        def operation() -> None:
            observed_at = _utc(connected_at, "connected_at")
            if not stream_session_id or stream_session_id in self._session_ids:
                raise ValueError("shadow journal stream session identifier is invalid")
            if observed_at.astimezone(self._timezone).date() != self._trade_date:
                raise ValueError("shadow journal connection falls outside its market date")
            if self._first_connected_at is None:
                if observed_at > self._decision_times[0]:
                    raise ValueError("shadow journal started after its first decision clock")
                self._first_connected_at = observed_at
            self._session_ids.append(stream_session_id)
            if self._open_gap is not None:
                if observed_at < self._open_gap:
                    raise ValueError("shadow journal connection timestamps are not ordered")
                if observed_at > self._open_gap:
                    self._gaps.append(StreamGap(started_at=self._open_gap, ended_at=observed_at))
                self._open_gap = None

        self._safe(operation)

    def ingest(self, envelopes: Sequence[StreamEnvelope]) -> None:
        """Queue receipt-ordered evidence; feature state advances only behind the watermark."""

        def operation() -> None:
            if self._next_decision == len(self._decision_times):
                return
            for envelope in envelopes:
                if (
                    self._last_ingested_at is not None
                    and envelope.received_at < self._last_ingested_at
                ):
                    raise ValueError("shadow journal envelopes must be receipt ordered")
                if envelope.received_at.astimezone(self._timezone).date() != self._trade_date:
                    raise ValueError("shadow journal envelope falls outside its market date")
                self._pending.append(envelope)
                self._last_ingested_at = envelope.received_at

        self._safe(operation)

    def _gap_affected(self, decision_at: datetime) -> bool:
        warmup = timedelta(seconds=self._configuration.features.warmup_seconds)
        if self._open_gap is not None and decision_at >= self._open_gap:
            return True
        return any(gap.started_at <= decision_at < gap.ended_at + warmup for gap in self._gaps)

    def _apply_until(self, cutoff: datetime) -> None:
        while self._pending and self._pending[0].received_at <= cutoff:
            self._feature_engine.apply(self._pending.popleft())

    def _snapshots(self, decision_at: datetime) -> tuple[FeatureSnapshot, ...]:
        self._apply_until(decision_at)
        snapshots = self._feature_engine.snapshots(decision_at)
        if not self._gap_affected(decision_at):
            return snapshots
        return tuple(
            snapshot
            if "CAPTURE_GAP" in snapshot.reasons
            else snapshot.model_copy(update={"reasons": (*snapshot.reasons, "CAPTURE_GAP")})
            for snapshot in snapshots
        )

    def _advance(self, cutoff: datetime) -> None:
        while (
            self._next_decision < len(self._decision_times)
            and self._decision_times[self._next_decision] <= cutoff
        ):
            decision_at = self._decision_times[self._next_decision]
            for decision in self._decision_engine.decisions(self._snapshots(decision_at)):
                line = decision.canonical_bytes() + b"\n"
                self._stream.write(line)
                self._digest.update(line)
                self._action_counts[decision.action] += 1
                self.decision_count += 1
            self._stream.flush()
            self._next_decision += 1
        if self._next_decision == len(self._decision_times):
            self._pending.clear()
        else:
            self._apply_until(cutoff)

    def advance(self, observed_at: datetime) -> None:
        """Advance behind one feature cadence so callback delivery cannot race the clock."""
        self._safe(lambda: self._advance(_utc(observed_at, "observed_at") - self._watermark_delay))

    def disconnected(self, disconnected_at: datetime, *, unavailable: bool) -> None:
        """Flush the safe boundary and open a gap before a reconnect when required."""

        def operation() -> None:
            observed_at = _utc(disconnected_at, "disconnected_at")
            if unavailable and self._open_gap is None:
                self._open_gap = observed_at
            self._advance(observed_at)

        self._safe(operation)

    def close(self, observed_at: datetime, capture_manifest_uris: Sequence[str]) -> None:
        """Publish the local journal only after a clean bounded runtime shutdown."""

        def operation() -> None:
            completed_at = _utc(observed_at, "observed_at")
            self._advance(completed_at)
            if self._next_decision != len(self._decision_times):
                raise ValueError("shadow journal did not reach every configured decision clock")
            if self._first_connected_at is None:
                raise ValueError("shadow journal has no connected capture session")
            self._stream.flush()
            os.fsync(self._stream.fileno())
            self._stream.close()
            self.manifest = ShadowJournalManifest(
                trade_date=self._trade_date,
                first_connected_at=self._first_connected_at,
                completed_at=completed_at,
                stream_session_ids=tuple(self._session_ids),
                capture_manifest_uris=tuple(capture_manifest_uris),
                configuration_version=self._configuration.version,
                configuration_sha256=self._configuration.sha256,
                feature_version=self._configuration.features.version,
                strategy_version=self._strategy_policy.version,
                strategy_configuration_sha256=self._strategy_policy.sha256,
                outcome_version=self._outcome_policy.version,
                outcome_configuration_sha256=self._outcome_policy.sha256,
                decision_version=self._decision_policy.version,
                decision_configuration_sha256=self._decision_policy.sha256,
                expected_decision_count=(
                    len(self._decision_times)
                    * len(self._configuration.market.symbols)
                    * len(self._decision_policy.rules)
                ),
                decision_count=self.decision_count,
                action_counts=self.action_counts,
                journal_file=self.output.name,
                journal_sha256=self._digest.hexdigest(),
            )
            with self.partial_manifest_output.open("xb") as manifest_output:
                manifest_output.write(self.manifest.canonical_bytes())
                manifest_output.flush()
                os.fsync(manifest_output.fileno())
            os.replace(self.partial_output, self.output)
            os.replace(self.partial_manifest_output, self.manifest_output)
            directory = os.open(self.output.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
            self._closed = True

        self._safe(operation)

    def abort(self) -> None:
        """Retain an explicitly partial journal after an unclean process outcome."""
        if not self._closed and not self._stream.closed:
            self._stream.close()
        self._failed = True
