import json
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from t0_trading.arbitration import (
    ShadowArbitrationAuditError,
    ShadowArbitrationJournal,
    audit_shadow_journal,
)
from t0_trading.configuration import TradingConfiguration, load_configuration
from t0_trading.identity import canonical_json, sha256
from t0_trading.market import StreamEnvelope

CONFIGURATION = Path("t0-trading/config/trading.yaml")
TRADE_DATE = date(2026, 9, 4)
SESSION_ID = "6b710ea5-f0eb-457e-bb58-73961428670a"
MANIFEST_URI = (
    "s3://landing/root/stream/ssi_fastconnect_stream/raw/"
    f"trade_date={TRADE_DATE.isoformat()}/session={SESSION_ID}/manifest.json"
)


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


def _artifacts(tmp_path: Path) -> tuple[Path, TradingConfiguration, _SessionReader]:
    configuration = _configuration()
    version = configuration.resolve(TRADE_DATE)
    connected_at = _received(9, 0)
    disconnected_at = _received(9, 21)
    output = tmp_path / "shadow.arbitrations.jsonl"
    journal = ShadowArbitrationJournal(
        output,
        TRADE_DATE,
        version,
        configuration.resolve_context(TRADE_DATE),
        configuration.resolve_candidate_arbitration(TRADE_DATE),
    )
    journal.connected(SESSION_ID, connected_at)
    journal.close(disconnected_at, (MANIFEST_URI,))
    reader = _SessionReader(
        uri=MANIFEST_URI,
        trade_date=TRADE_DATE,
        manifest_sha256="a" * 64,
        manifest=SimpleNamespace(
            connected_at=connected_at,
            disconnected_at=disconnected_at,
            published_at=disconnected_at,
            stream_session_id=SESSION_ID,
            symbols=version.market.symbols,
            api_version="v3",
            sdk_version="3.2.1",
            message_count=0,
        ),
    )
    return journal.manifest_output, configuration, reader


def _use_capture(monkeypatch: pytest.MonkeyPatch, reader: _SessionReader) -> None:
    def from_uri(_client: object, uri: str) -> _SessionReader:
        assert uri == MANIFEST_URI
        return reader

    monkeypatch.setattr(
        "t0_trading.arbitration.audit.StreamSessionReader.from_uri",
        from_uri,
    )


def test_shadow_journal_audit_proves_local_output_against_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest_path, configuration, reader = _artifacts(tmp_path)
    _use_capture(monkeypatch, reader)

    report = audit_shadow_journal(manifest_path, object(), configuration)

    assert report.status == "passed"
    assert report.trade_date == TRADE_DATE
    assert report.stream_session_ids == (SESSION_ID,)
    assert report.capture_evidence_sha256 == sha256(canonical_json(["a" * 64]))
    assert report.capture_message_count == 0
    assert report.gap_count == 0
    assert report.arbitration_count == report.candidate_count


def test_shadow_journal_audit_rejects_local_checksum_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest_path, configuration, reader = _artifacts(tmp_path)
    _use_capture(monkeypatch, reader)
    journal_path = manifest_path.with_name("shadow.arbitrations.jsonl")
    with journal_path.open("ab") as journal:
        journal.write(b"{}\n")

    with pytest.raises(ShadowArbitrationAuditError, match="checksum"):
        audit_shadow_journal(manifest_path, object(), configuration)


def test_shadow_journal_audit_rejects_rehashed_content_that_differs_from_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest_path, configuration, reader = _artifacts(tmp_path)
    _use_capture(monkeypatch, reader)
    journal_path = manifest_path.with_name("shadow.arbitrations.jsonl")
    with journal_path.open("ab") as journal:
        journal.write(b"{}\n")
    manifest = json.loads(manifest_path.read_bytes())
    manifest["arbitration_sha256"] = sha256(journal_path.read_bytes())
    manifest_path.write_bytes(canonical_json(manifest))

    with pytest.raises(ShadowArbitrationAuditError, match="trailing records"):
        audit_shadow_journal(manifest_path, object(), configuration)


def test_shadow_journal_audit_rejects_capture_lineage_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest_path, configuration, reader = _artifacts(tmp_path)
    reader.manifest.stream_session_id = "6c60f055-e8cc-432a-b1d7-5cbabbd2b7c4"
    _use_capture(monkeypatch, reader)

    with pytest.raises(ShadowArbitrationAuditError, match="capture lineage"):
        audit_shadow_journal(manifest_path, object(), configuration)


def test_shadow_journal_audit_rejects_noncanonical_or_stale_lineage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest_path, configuration, reader = _artifacts(tmp_path)
    _use_capture(monkeypatch, reader)
    manifest_path.write_bytes(manifest_path.read_bytes() + b"\n")

    with pytest.raises(ShadowArbitrationAuditError, match="not canonical"):
        audit_shadow_journal(manifest_path, object(), configuration)

    manifest_path, configuration, reader = _artifacts(tmp_path / "stale")
    _use_capture(monkeypatch, reader)
    with pytest.raises(ShadowArbitrationAuditError, match="policy lineage"):
        audit_shadow_journal(manifest_path, object(), load_configuration(CONFIGURATION))
