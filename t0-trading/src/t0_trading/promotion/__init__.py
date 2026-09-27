"""Shadow-to-paper promotion public API."""

from t0_trading.promotion.engine import (
    evaluate_arbitrated_walk_forward,
    evaluate_promotion_gate,
)
from t0_trading.promotion.evidence import evaluate_arbitrated_session
from t0_trading.promotion.model import (
    ArbitratedHoldoutEvaluation,
    ArbitratedSessionEvaluation,
    ArbitratedSessionReport,
    ArbitratedWalkForwardFold,
    ArbitratedWalkForwardReport,
    PromotionGateReport,
    PromotionReason,
    PromotionStatus,
    PromotionTargetResult,
)
from t0_trading.promotion.publication import (
    PROMOTION_EVIDENCE_PREFIX,
    PromotionEvidencePublication,
    load_session_evidence,
    publish_gate_evidence,
    publish_session_evidence,
)

__all__ = [
    "PROMOTION_EVIDENCE_PREFIX",
    "ArbitratedHoldoutEvaluation",
    "ArbitratedSessionEvaluation",
    "ArbitratedSessionReport",
    "ArbitratedWalkForwardFold",
    "ArbitratedWalkForwardReport",
    "PromotionEvidencePublication",
    "PromotionGateReport",
    "PromotionReason",
    "PromotionStatus",
    "PromotionTargetResult",
    "evaluate_arbitrated_session",
    "evaluate_arbitrated_walk_forward",
    "evaluate_promotion_gate",
    "load_session_evidence",
    "publish_gate_evidence",
    "publish_session_evidence",
]
