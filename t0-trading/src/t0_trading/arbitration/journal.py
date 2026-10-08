"""Durable local journal for point-in-time candidates and their arbitrations."""

from __future__ import annotations

import hashlib
import logging
import os
from collections import deque
from collections.abc import Callable, Sequence
from contextlib import suppress
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from t0_trading.arbitration.engine import CandidateArbitrator
from t0_trading.arbitration.selection import RealtimeSelection
from t0_trading.capture.membership import BreadthMembershipSnapshot
from t0_trading.capture.reader import StreamGap
from t0_trading.configuration import (
    BreadthVersion,
    CandidateArbitrationVersion,
    ContextVersion,
    RegimeVersion,
    TradingVersion,
)
from t0_trading.context import LiveDecisionContextEngine
from t0_trading.features import FeatureEngine, FeatureSnapshot, decision_times
from t0_trading.identity import canonical_json, sha256
from t0_trading.market import StreamEnvelope
from t0_trading.strategy.baselines import (
    BASELINE_NAMES,
    BASELINE_VERSION,
    RELATIVE_PEER_LAG,
    score_buy_first_baselines,
)

_ARBITRATION_SUFFIX = ".arbitrations.jsonl"


def _utc(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


class ShadowArbitrationManifest(BaseModel):
    """Commit marker for one complete arbitration-only shadow journal."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[4] = 4
    trade_date: date
    first_connected_at: datetime
    completed_at: datetime
    stream_session_ids: tuple[str, ...]
    capture_manifest_uris: tuple[str, ...]
    configuration_version: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    configuration_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    feature_version: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    context_version: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    context_configuration_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    baseline_version: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    arbitration_version: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    arbitration_configuration_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_candidate_count: int = Field(ge=1)
    candidate_count: int = Field(ge=1)
    candidate_file: str
    candidate_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_arbitration_count: int = Field(ge=1)
    arbitration_count: int = Field(ge=1)
    arbitration_file: str
    arbitration_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("first_connected_at", "completed_at")
    @classmethod
    def normalize_timestamp(cls, value: datetime) -> datetime:
        return _utc(value, "shadow journal timestamp")

    @model_validator(mode="after")
    def validate_manifest(self) -> ShadowArbitrationManifest:
        if self.first_connected_at > self.completed_at:
            raise ValueError("shadow journal timestamps are not ordered")
        if (
            not self.stream_session_ids
            or len(set(self.stream_session_ids)) != len(self.stream_session_ids)
            or len(self.capture_manifest_uris) != len(self.stream_session_ids)
        ):
            raise ValueError("shadow journal capture sessions are inconsistent")
        for session_id, uri in zip(
            self.stream_session_ids, self.capture_manifest_uris, strict=True
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
            self.candidate_count != self.expected_candidate_count
            or Path(self.candidate_file).name != self.candidate_file
            or not self.candidate_file.endswith((".candidates.jsonl", ".candidates.jsonl.gz"))
        ):
            raise ValueError("shadow candidate summary is inconsistent")
        if (
            self.arbitration_count != self.expected_arbitration_count
            or Path(self.arbitration_file).name != self.arbitration_file
            or not self.arbitration_file.endswith(
                (_ARBITRATION_SUFFIX, f"{_ARBITRATION_SUFFIX}.gz")
            )
        ):
            raise ValueError("shadow arbitration summary is inconsistent")
        return self

    def canonical_bytes(self) -> bytes:
        return canonical_json(self.model_dump(mode="json"))

    @property
    def sha256(self) -> str:
        return sha256(self.canonical_bytes())


class ShadowArbitrationJournal:
    """Observe captured envelopes without allowing research failures to affect capture."""

    def __init__(
        self,
        output: Path,
        trade_date: date,
        configuration: TradingVersion,
        context_policy: ContextVersion,
        arbitration_policy: CandidateArbitrationVersion,
        *,
        breadth_policy: BreadthVersion | None = None,
        breadth_membership: BreadthMembershipSnapshot | None = None,
        regime_policy: RegimeVersion | None = None,
        on_error: Callable[[Exception], None] | None = None,
        on_selection: Callable[[RealtimeSelection], None] | None = None,
        on_disconnect: Callable[[datetime], None] | None = None,
    ) -> None:
        if not output.name.endswith(_ARBITRATION_SUFFIX):
            raise ValueError(f"shadow journal output must end with {_ARBITRATION_SUFFIX}")
        if not context_policy.contains(trade_date):
            raise ValueError("shadow context policy does not cover the trade date")
        if set(configuration.market.symbols) != {"VIC", "VHM"}:
            raise ValueError("shadow buy-first baselines require exactly VIC and VHM")
        if (
            not arbitration_policy.contains(trade_date)
            or arbitration_policy.candidate_version != BASELINE_VERSION
        ):
            raise ValueError("shadow arbitration policy does not cover the candidate stream")
        self._trade_date = trade_date
        self._configuration = configuration
        self._context_policy = context_policy
        self._arbitration_policy = arbitration_policy
        self._timezone = ZoneInfo(configuration.market.timezone)
        self._feature_engine = FeatureEngine(configuration)
        self._context_engine = LiveDecisionContextEngine(
            configuration,
            context_policy,
            breadth_policy=breadth_policy,
            breadth_membership=breadth_membership,
            regime_policy=regime_policy,
        )
        self._arbitrator = CandidateArbitrator(arbitration_policy)
        self._decision_times = tuple(decision_times(configuration, trade_date))
        if not self._decision_times:
            raise ValueError("shadow journal configuration has no decision clocks")
        self._next_decision = 0
        self._pending: deque[StreamEnvelope] = deque()
        self._feature_history: deque[FeatureSnapshot] = deque()
        self._last_ingested_at: datetime | None = None
        self._open_gap: datetime | None = None
        self._gaps: list[StreamGap] = []
        self._session_ids: list[str] = []
        self._first_connected_at: datetime | None = None
        self._watermark_delay = timedelta(seconds=configuration.features.cadence_seconds)
        self._on_error = on_error
        self._on_selection = on_selection
        self._on_disconnect = on_disconnect
        self._failed = False
        self._closed = False
        self._candidate_digest = hashlib.sha256()
        self._arbitration_digest = hashlib.sha256()
        self.candidate_count = 0
        self.arbitration_count = 0
        self.manifest: ShadowArbitrationManifest | None = None

        base = output.name.removesuffix(_ARBITRATION_SUFFIX)
        self.output = output
        self.partial_output = Path(f"{output}.partial")
        self.candidate_output = output.with_name(f"{base}.candidates.jsonl")
        self.partial_candidate_output = Path(f"{self.candidate_output}.partial")
        self.manifest_output = output.with_name(f"{base}.manifest.json")
        self.partial_manifest_output = Path(f"{self.manifest_output}.partial")
        paths = (
            self.output,
            self.partial_output,
            self.candidate_output,
            self.partial_candidate_output,
            self.manifest_output,
            self.partial_manifest_output,
        )
        if any(path.exists() for path in paths):
            raise FileExistsError("shadow journal output already exists")
        output.parent.mkdir(parents=True, exist_ok=True)
        self._arbitration_stream = self.partial_output.open("xb")
        self._candidate_stream = self.partial_candidate_output.open("xb")

    @property
    def failed(self) -> bool:
        return self._failed

    @property
    def arbitration_sha256(self) -> str | None:
        return None if not self._closed or self._failed else self._arbitration_digest.hexdigest()

    def _fail(self, error: Exception) -> None:
        if self._failed:
            return
        self._failed = True
        self._arbitration_stream.close()
        self._candidate_stream.close()
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
            envelope = self._pending.popleft()
            self._feature_engine.apply(envelope)
            self._context_engine.apply(envelope)

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

    def _advance(self, cutoff: datetime, *, realtime: bool = False) -> None:
        while (
            self._next_decision < len(self._decision_times)
            and self._decision_times[self._next_decision] <= cutoff
        ):
            decision_at = self._decision_times[self._next_decision]
            snapshots = self._snapshots(decision_at)
            context = self._context_engine.build(snapshots)
            while self._feature_history and (
                self._feature_history[0].decision_at < decision_at - RELATIVE_PEER_LAG
            ):
                self._feature_history.popleft()
            candidates = score_buy_first_baselines(
                snapshots, (context,), history=tuple(self._feature_history)
            )
            for candidate in candidates:
                line = candidate.canonical_bytes() + b"\n"
                self._candidate_stream.write(line)
                self._candidate_digest.update(line)
                self.candidate_count += 1
            arbitrations = self._arbitrator.decide(candidates)
            for arbitration in arbitrations:
                line = arbitration.canonical_bytes() + b"\n"
                self._arbitration_stream.write(line)
                self._arbitration_digest.update(line)
                self.arbitration_count += 1
            self._candidate_stream.flush()
            self._arbitration_stream.flush()
            self._next_decision += 1
            # Only hand off decisions after both journal streams accepted their records.
            # This is a live shadow hint, not proof of the final S3 publication commit.
            if (
                realtime
                and self._on_selection is not None
                and context.selection_block_reason(realtime=True) is None
            ):
                try:
                    by_hash = {item.sha256: item for item in candidates}
                    for arbitration in arbitrations:
                        if arbitration.status != "SELECTED":
                            continue
                        candidate = by_hash[arbitration.candidate_sha256]
                        required = {
                            candidate.feature_snapshot_sha256,
                            candidate.peer_feature_snapshot_sha256,
                        }
                        self._on_selection(
                            RealtimeSelection(
                                candidate=candidate,
                                arbitration=arbitration,
                                context=context,
                                features=tuple(
                                    item for item in snapshots if item.sha256 in required
                                ),
                                lagged_peer=next(
                                    (
                                        item
                                        for item in self._feature_history
                                        if item.symbol != candidate.symbol
                                        and item.decision_at == decision_at - RELATIVE_PEER_LAG
                                    ),
                                    None,
                                )
                                if candidate.strategy == "vic_vhm_relative"
                                else None,
                            )
                        )
                except Exception as error:
                    # A broken consumer must not invalidate the research journal or raw capture.
                    self._on_selection = None
                    logging.getLogger(__name__).error(
                        "Realtime handoff disabled (%s)", type(error).__name__
                    )
            self._feature_history.extend(snapshots)
        if self._next_decision == len(self._decision_times):
            self._pending.clear()
        else:
            self._apply_until(cutoff)

    def advance(self, observed_at: datetime) -> None:
        self._safe(
            lambda: self._advance(
                _utc(observed_at, "observed_at") - self._watermark_delay, realtime=True
            )
        )

    def disconnected(self, disconnected_at: datetime, *, unavailable: bool) -> None:
        if self._on_disconnect is not None:
            with suppress(Exception):
                self._on_disconnect(disconnected_at)

        def operation() -> None:
            observed_at = _utc(disconnected_at, "disconnected_at")
            if unavailable and self._open_gap is None:
                self._open_gap = observed_at
            self._advance(observed_at)

        self._safe(operation)

    def close(self, observed_at: datetime, capture_manifest_uris: Sequence[str]) -> None:
        def operation() -> None:
            completed_at = _utc(observed_at, "observed_at")
            self._advance(completed_at)
            if self._next_decision != len(self._decision_times):
                raise ValueError("shadow journal did not reach every configured decision clock")
            if self._first_connected_at is None:
                raise ValueError("shadow journal has no connected capture session")
            for stream in (self._candidate_stream, self._arbitration_stream):
                stream.flush()
                os.fsync(stream.fileno())
                stream.close()
            expected_count = (
                len(self._decision_times)
                * len(self._configuration.market.symbols)
                * len(BASELINE_NAMES)
            )
            self.manifest = ShadowArbitrationManifest(
                trade_date=self._trade_date,
                first_connected_at=self._first_connected_at,
                completed_at=completed_at,
                stream_session_ids=tuple(self._session_ids),
                capture_manifest_uris=tuple(capture_manifest_uris),
                configuration_version=self._configuration.version,
                configuration_sha256=self._configuration.sha256,
                feature_version=self._configuration.features.version,
                context_version=self._context_policy.version,
                context_configuration_sha256=self._context_engine.configuration_sha256,
                baseline_version=BASELINE_VERSION,
                arbitration_version=self._arbitration_policy.version,
                arbitration_configuration_sha256=self._arbitration_policy.sha256,
                expected_candidate_count=expected_count,
                candidate_count=self.candidate_count,
                candidate_file=self.candidate_output.name,
                candidate_sha256=self._candidate_digest.hexdigest(),
                expected_arbitration_count=expected_count,
                arbitration_count=self.arbitration_count,
                arbitration_file=self.output.name,
                arbitration_sha256=self._arbitration_digest.hexdigest(),
            )
            with self.partial_manifest_output.open("xb") as manifest_output:
                manifest_output.write(self.manifest.canonical_bytes())
                manifest_output.flush()
                os.fsync(manifest_output.fileno())
            os.replace(self.partial_candidate_output, self.candidate_output)
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
        """Retain explicitly partial evidence after an unclean process outcome."""
        for stream in (self._candidate_stream, self._arbitration_stream):
            if not self._closed and not stream.closed:
                stream.close()
        self._failed = True
