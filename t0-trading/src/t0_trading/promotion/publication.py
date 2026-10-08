"""Immutable publication of daily arbitration evidence and promotion state."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import date
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from t0_trading.arbitration import ShadowArbitrationAuditReport
from t0_trading.configuration import PromotionGateVersion
from t0_trading.evidence_paths import PROMOTION_EVIDENCE_PREFIX
from t0_trading.promotion.model import ArbitratedSessionReport, PromotionGateReport

_SESSION_KEY = re.compile(
    rf"^{PROMOTION_EVIDENCE_PREFIX}/sessions/trade_date=(\d{{4}}-\d{{2}}-\d{{2}})/arbitrated_session.json$"
)


class PromotionEvidenceStore(Protocol):
    def uri(self, key: str) -> str: ...

    def read_json(self, key: str) -> dict[str, Any] | None: ...

    def list_keys(self, prefix: str) -> tuple[str, ...]: ...

    def put_json(self, key: str, value: Mapping[str, Any]) -> tuple[str, str]: ...


class PromotionEvidencePublication(BaseModel):
    """Deterministic locations and state emitted by one daily publication."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    trade_date: date
    shadow_audit_uri: str
    shadow_audit_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    session_report_uri: str
    session_report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    gate_report_uri: str
    gate_report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    gate_status: Literal["PENDING", "PASS", "FAIL"]
    observed_session_count: int = Field(ge=0)
    required_session_count: int = Field(ge=1)
    capital_authorized: Literal[False] = False


def _session_root(trade_date: date) -> str:
    return f"{PROMOTION_EVIDENCE_PREFIX}/sessions/trade_date={trade_date.isoformat()}"


def publish_session_evidence(
    store: PromotionEvidenceStore,
    shadow_audit: ShadowArbitrationAuditReport,
    session: ArbitratedSessionReport,
) -> tuple[str, str, str, str]:
    """Commit one date only after its shadow proof has been persisted."""
    if shadow_audit.trade_date != session.trade_date:
        raise ValueError("promotion evidence dates do not match")
    root = _session_root(session.trade_date)
    shadow_key, shadow_sha256 = store.put_json(
        f"{root}/shadow_audit.json", shadow_audit.model_dump(mode="json")
    )
    if shadow_sha256 != session.shadow_audit_sha256:
        raise RuntimeError("published shadow audit checksum is inconsistent")
    session_key, session_sha256 = store.put_json(
        f"{root}/arbitrated_session.json", session.model_dump(mode="json")
    )
    if session_sha256 != session.sha256:
        raise RuntimeError("published session report checksum is inconsistent")
    return shadow_key, shadow_sha256, session_key, session_sha256


def load_session_evidence(
    store: PromotionEvidenceStore,
    gate: PromotionGateVersion,
    *,
    as_of_date: date,
    reference: ArbitratedSessionReport | None = None,
) -> dict[date, ArbitratedSessionReport]:
    """Load a fixed-assumption cohort, without pooling changed context/cost policies."""
    if reference is not None and reference.trade_date != as_of_date:
        raise ValueError("promotion cohort reference must be the as-of session")
    sessions: dict[date, ArbitratedSessionReport] = {}
    prefix = f"{PROMOTION_EVIDENCE_PREFIX}/sessions"
    for key in store.list_keys(prefix):
        match = _SESSION_KEY.fullmatch(key)
        if match is None:
            continue
        key_date = date.fromisoformat(match.group(1))
        if key_date > as_of_date or not gate.contains(key_date):
            continue
        value = store.read_json(key)
        if value is None:
            raise RuntimeError(f"promotion session disappeared during listing: {key}")
        report = ArbitratedSessionReport.model_validate(value)
        if report.trade_date != key_date:
            raise RuntimeError(f"promotion session key lineage is inconsistent: {key}")
        if reference is not None and report.research_lineage != reference.research_lineage:
            continue
        if key_date in sessions:
            raise RuntimeError(f"duplicate promotion session exists for {key_date.isoformat()}")
        sessions[key_date] = report
    return dict(sorted(sessions.items()))


def publish_gate_evidence(
    store: PromotionEvidenceStore,
    *,
    as_of_date: date,
    report: PromotionGateReport,
) -> tuple[str, str]:
    """Publish the reproducible gate view for a specific as-of date."""
    key = (
        f"{PROMOTION_EVIDENCE_PREFIX}/gates/as_of_date={as_of_date.isoformat()}/promotion_gate.json"
    )
    stored_key, digest = store.put_json(key, report.model_dump(mode="json"))
    if digest != report.sha256:
        raise RuntimeError("published promotion gate checksum is inconsistent")
    return stored_key, digest
