"""Read the exact preceding session's immutable gate; never fall back to an older PASS."""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

from t0_trading.arbitration import ShadowArbitrationAuditReport
from t0_trading.configuration import TradingConfiguration
from t0_trading.evidence_paths import PROMOTION_EVIDENCE_PREFIX
from t0_trading.identity import canonical_json, sha256
from t0_trading.promotion import PromotionGateReport, evaluate_promotion_gate
from t0_trading.promotion.publication import PromotionEvidenceStore, load_session_evidence


class PaperReadiness(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    trade_date: date
    previous_session: date
    mode: Literal["OBSERVE_ONLY", "PAPER"]
    reason: str
    promotion: PromotionGateReport | None = None
    capital_authorized: Literal[False] = False

    @model_validator(mode="after")
    def validate_mode(self) -> "PaperReadiness":
        if self.mode == "PAPER" and (
            self.reason != "PROMOTION_PASS"
            or self.promotion is None
            or self.promotion.status != "PASS"
            or self.previous_session >= self.trade_date
        ):
            raise ValueError("paper readiness requires valid prior promotion PASS")
        return self


def load_paper_readiness(
    store: PromotionEvidenceStore,
    configuration: TradingConfiguration,
    *,
    trade_date: date,
    previous_session: date,
    enabled: bool = False,
) -> PaperReadiness:
    """Previous session must come from an observed trading calendar, not weekday arithmetic.

    The store verifies object checksums. Recompute the published gate and bind every session to
    its published shadow audit. Errors are reported by category only, never credential text.
    """
    report = None
    reason = "PAPER_DISABLED"
    try:
        if previous_session >= trade_date:
            raise ValueError("previous session must precede trading date")
        gate = configuration.resolve_promotion_gate(trade_date)
        arbitration = configuration.resolve_candidate_arbitration(trade_date)
        if (
            gate is None
            or arbitration is None
            or configuration.resolve_paper_execution(trade_date) is None
        ):
            reason = "NO_EFFECTIVE_POLICY"
        else:
            key = (
                f"{PROMOTION_EVIDENCE_PREFIX}/gates/"
                f"as_of_date={previous_session}/promotion_gate.json"
            )
            value = store.read_json(key)
            if value is None:
                reason = "MISSING_PREVIOUS_SESSION_GATE"
            else:
                report = PromotionGateReport.model_validate(value)
                sessions = load_session_evidence(store, gate, as_of_date=previous_session)
                if not sessions or max(sessions) != previous_session:
                    raise ValueError("latest session evidence is incomplete")
                for session in sessions.values():
                    audit_value = store.read_json(
                        f"{PROMOTION_EVIDENCE_PREFIX}/sessions/trade_date={session.trade_date}/shadow_audit.json"
                    )
                    audit = ShadowArbitrationAuditReport.model_validate(audit_value)
                    if (
                        sha256(canonical_json(audit.model_dump(mode="json")))
                        != session.shadow_audit_sha256
                        or audit.trade_date != session.trade_date
                        or audit.capture_evidence_sha256 != session.capture_evidence_sha256
                        or audit.arbitration_configuration_sha256
                        != session.arbitration_configuration_sha256
                        or audit.gap_count != session.capture_gap_count
                    ):
                        raise ValueError("session shadow audit lineage mismatch")
                expected = evaluate_promotion_gate(
                    sessions,
                    configuration.resolve_baseline_evaluation(trade_date, "PROMOTION"),
                    gate,
                    arbitration,
                )
                if report != expected:
                    raise ValueError("published gate differs from complete session evidence")
                reason = "PAPER_DISABLED" if not enabled else f"PROMOTION_{report.status}"
    except Exception as error:
        # This is a fail-closed process boundary: do not allow evidence I/O to kill capture.
        reason = f"INVALID_OR_UNAVAILABLE_EVIDENCE:{type(error).__name__}"
        report = None
    return PaperReadiness(
        trade_date=trade_date,
        previous_session=previous_session,
        mode="PAPER" if enabled and reason == "PROMOTION_PASS" else "OBSERVE_ONLY",
        reason=reason,
        promotion=report,
    )
