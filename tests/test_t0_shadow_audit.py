import gzip
import io
import json
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from urllib.parse import urlparse

import pytest
from botocore.exceptions import ClientError
from t0_trading.arbitration import (
    ShadowArbitrationAuditError,
    ShadowArbitrationJournal,
    audit_shadow_journal,
    ensure_shadow_journal,
    publish_shadow_journal,
)
from t0_trading.capture.reader import StreamDayReader, StreamSessionReader
from t0_trading.capture.spool import CaptureSpool
from t0_trading.capture.store import S3CaptureStore
from t0_trading.configuration import TradingConfiguration, load_configuration
from t0_trading.identity import canonical_json, sha256
from t0_trading.market import StreamEnvelope

CONFIGURATION = Path("t0-trading/config/trading.yaml")
TRADE_DATE = date(2026, 9, 4)
SESSION_ID = "6b710ea5-f0eb-457e-bb58-73961428670a"
CAPTURE_MANIFEST_URI = (
    "s3://landing/root/stream/ssi_fastconnect_stream/raw/"
    f"trade_date={TRADE_DATE.isoformat()}/session={SESSION_ID}/manifest.json"
)


class _S3:
    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, dict[str, str]]] = {}

    def head_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
        try:
            body, metadata = self.objects[f"{Bucket}/{Key}"]
        except KeyError:
            raise ClientError({"Error": {"Code": "404"}}, "HeadObject") from None
        return {"ContentLength": len(body), "Metadata": metadata}

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
        body, metadata = self.objects[f"{Bucket}/{Key}"]
        return {"Body": io.BytesIO(body), "Metadata": metadata}

    def put_object(self, *, Bucket: str, Key: str, Body: bytes, **values: Any) -> None:
        object_id = f"{Bucket}/{Key}"
        if object_id in self.objects and values.get("IfNoneMatch") == "*":
            raise ClientError({"Error": {"Code": "PreconditionFailed"}}, "PutObject")
        self.objects[object_id] = (Body, values["Metadata"])

    def replace(self, key: str, body: bytes) -> None:
        self.objects[f"landing/{key}"] = (body, {"sha256": sha256(body)})


def _received(hour: int, minute: int, second: int = 0) -> datetime:
    return datetime(2026, 9, 4, hour - 7, minute, second, tzinfo=UTC)


def _configuration() -> TradingConfiguration:
    loaded = load_configuration(CONFIGURATION)
    version = loaded.resolve(TRADE_DATE)
    sessions = version.market.sessions.model_copy(
        update={"continuous_am": (time(9, 15), time(9, 21))}
    )
    short = version.model_copy(
        update={
            "market": version.market.model_copy(update={"sessions": sessions}),
            "features": version.features.model_copy(
                update={"decision_sessions": ("continuous_am",)}
            ),
        }
    )
    arbitration = loaded.candidate_arbitrations[0].model_copy(update={"effective_from": TRADE_DATE})
    return loaded.model_copy(
        update={"versions": (short,), "candidate_arbitrations": (arbitration,)}
    )


@dataclass(frozen=True)
class _SessionReader:
    uri: str
    trade_date: date
    manifest_sha256: str
    manifest: Any

    @property
    def covered_until(self) -> datetime:
        return self.manifest.disconnected_at

    def envelopes(self) -> Iterator[StreamEnvelope]:
        return iter(())


def _artifacts(tmp_path: Path) -> tuple[str, TradingConfiguration, _SessionReader, _S3]:
    configuration = _configuration()
    version = configuration.resolve(TRADE_DATE)
    arbitration_policy = configuration.resolve_candidate_arbitration(TRADE_DATE)
    assert arbitration_policy is not None
    connected_at = _received(9, 0)
    disconnected_at = _received(9, 21)
    journal = ShadowArbitrationJournal(
        tmp_path / "shadow.arbitrations.jsonl",
        TRADE_DATE,
        version,
        configuration.resolve_context(TRADE_DATE),
        arbitration_policy,
    )
    journal.connected(SESSION_ID, connected_at)
    journal.close(disconnected_at, (CAPTURE_MANIFEST_URI,))
    s3 = _S3()
    manifest_uri, _ = publish_shadow_journal(journal, S3CaptureStore(s3, "s3://landing/root"))
    reader = _SessionReader(
        uri=CAPTURE_MANIFEST_URI,
        trade_date=TRADE_DATE,
        manifest_sha256="a" * 64,
        manifest=SimpleNamespace(
            connected_at=connected_at,
            disconnected_at=disconnected_at,
            disconnect_kind="shutdown",
            published_at=disconnected_at,
            stream_session_id=SESSION_ID,
            symbols=version.market.symbols,
            api_version="v3",
            sdk_version="3.2.1",
            message_count=0,
        ),
    )
    return manifest_uri, configuration, reader, s3


def _use_capture(monkeypatch: pytest.MonkeyPatch, reader: _SessionReader) -> None:
    def from_uri(_client: object, uri: str) -> _SessionReader:
        assert uri == CAPTURE_MANIFEST_URI
        return reader

    monkeypatch.setattr("t0_trading.arbitration.audit.StreamSessionReader.from_uri", from_uri)


def _key(uri: str) -> str:
    return urlparse(uri).path.lstrip("/")


def test_shadow_journal_is_published_and_audited_from_s3(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest_uri, configuration, reader, s3 = _artifacts(tmp_path)
    _use_capture(monkeypatch, reader)

    report = audit_shadow_journal(manifest_uri, s3, configuration)

    assert report.status == "passed"
    assert report.trade_date == TRADE_DATE
    assert report.stream_session_ids == (SESSION_ID,)
    assert report.capture_evidence_sha256 == sha256(canonical_json(["a" * 64]))
    assert report.capture_message_count == 0
    assert report.gap_count == 0
    assert report.arbitration_count == report.candidate_count
    assert manifest_uri.endswith(f"trade_date={TRADE_DATE.isoformat()}/manifest.json")


def test_shadow_journal_outbox_publishes_commit_marker_last(tmp_path: Path) -> None:
    configuration = _configuration()
    version = configuration.resolve(TRADE_DATE)
    arbitration_policy = configuration.resolve_candidate_arbitration(TRADE_DATE)
    assert arbitration_policy is not None
    journal = ShadowArbitrationJournal(
        tmp_path / "work" / "shadow.arbitrations.jsonl",
        TRADE_DATE,
        version,
        configuration.resolve_context(TRADE_DATE),
        arbitration_policy,
    )
    journal.connected(SESSION_ID, _received(9, 0))
    journal.close(_received(9, 21), (CAPTURE_MANIFEST_URI,))
    s3 = _S3()
    spool = CaptureSpool(tmp_path / "spool", max_bytes=1_000_000)

    publish_shadow_journal(journal, S3CaptureStore(s3, "s3://landing/root"), spool=spool)

    assert list(s3.objects)[-1].endswith("/manifest.json")
    assert spool.pending_bytes == 0


def test_shadow_journal_recovery_is_replayable_and_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest_uri, configuration, reader, s3 = _artifacts(tmp_path / "live")
    _use_capture(monkeypatch, reader)
    live_objects = {
        object_id: value
        for object_id, value in s3.objects.items()
        if "/promotion/shadow_journals/" in object_id
    }
    for object_id in tuple(s3.objects):
        if "/promotion/shadow_journals/" in object_id:
            del s3.objects[object_id]
    capture = StreamDayReader((cast(StreamSessionReader, reader),))
    store = S3CaptureStore(s3, "s3://landing/root")

    rebuilt = ensure_shadow_journal(
        capture,
        configuration,
        store,
        s3,
        tmp_path / "recovery",
    )
    published = dict(s3.objects)
    existing = ensure_shadow_journal(
        capture,
        configuration,
        store,
        s3,
        tmp_path / "unused",
    )

    assert rebuilt.action == "REBUILT"
    assert existing.action == "EXISTING"
    assert rebuilt.manifest_uri == existing.manifest_uri == manifest_uri
    assert rebuilt.manifest_sha256 == existing.manifest_sha256
    assert rebuilt.capture_evidence_sha256 == existing.capture_evidence_sha256
    assert s3.objects == published
    assert {object_id: s3.objects[object_id] for object_id in live_objects} == live_objects


def test_shadow_journal_audit_rejects_s3_checksum_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest_uri, configuration, reader, s3 = _artifacts(tmp_path)
    _use_capture(monkeypatch, reader)
    manifest = json.loads(s3.objects[f"landing/{_key(manifest_uri)}"][0])
    arbitration_key = f"{_key(manifest_uri).rsplit('/', 1)[0]}/{manifest['arbitration_file']}"
    compressed = s3.objects[f"landing/{arbitration_key}"][0]
    s3.replace(arbitration_key, gzip.compress(gzip.decompress(compressed) + b"{}\n", mtime=0))

    with pytest.raises(ShadowArbitrationAuditError, match="checksum"):
        audit_shadow_journal(manifest_uri, s3, configuration)


def test_shadow_journal_audit_rejects_rehashed_content_that_differs_from_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest_uri, configuration, reader, s3 = _artifacts(tmp_path)
    _use_capture(monkeypatch, reader)
    manifest_key = _key(manifest_uri)
    manifest = json.loads(s3.objects[f"landing/{manifest_key}"][0])
    arbitration_key = f"{manifest_key.rsplit('/', 1)[0]}/{manifest['arbitration_file']}"
    body = gzip.decompress(s3.objects[f"landing/{arbitration_key}"][0]) + b"{}\n"
    manifest["arbitration_sha256"] = sha256(body)
    s3.replace(arbitration_key, gzip.compress(body, mtime=0))
    s3.replace(manifest_key, canonical_json(manifest))

    with pytest.raises(ShadowArbitrationAuditError, match="trailing records"):
        audit_shadow_journal(manifest_uri, s3, configuration)


def test_shadow_journal_audit_rejects_capture_lineage_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest_uri, configuration, reader, s3 = _artifacts(tmp_path)
    reader.manifest.stream_session_id = "6c60f055-e8cc-432a-b1d7-5cbabbd2b7c4"
    _use_capture(monkeypatch, reader)

    with pytest.raises(ShadowArbitrationAuditError, match="capture lineage"):
        audit_shadow_journal(manifest_uri, s3, configuration)


def test_shadow_journal_audit_rejects_noncanonical_or_stale_lineage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest_uri, configuration, reader, s3 = _artifacts(tmp_path)
    _use_capture(monkeypatch, reader)
    manifest_key = _key(manifest_uri)
    s3.replace(manifest_key, s3.objects[f"landing/{manifest_key}"][0] + b"\n")

    with pytest.raises(ShadowArbitrationAuditError, match="not canonical"):
        audit_shadow_journal(manifest_uri, s3, configuration)

    manifest_uri, _, reader, s3 = _artifacts(tmp_path / "stale")
    _use_capture(monkeypatch, reader)
    with pytest.raises(ShadowArbitrationAuditError, match="policy lineage"):
        audit_shadow_journal(manifest_uri, s3, load_configuration(CONFIGURATION))
