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

__all__ = [
    "ArbitratedHoldoutEvaluation",
    "ArbitratedSessionEvaluation",
    "ArbitratedSessionReport",
    "ArbitratedWalkForwardFold",
    "ArbitratedWalkForwardReport",
    "PromotionGateReport",
    "PromotionReason",
    "PromotionStatus",
    "PromotionTargetResult",
    "evaluate_arbitrated_session",
    "evaluate_arbitrated_walk_forward",
    "evaluate_promotion_gate",
]
