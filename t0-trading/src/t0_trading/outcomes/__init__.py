"""Deterministic offline outcomes for point-in-time feature snapshots."""

from t0_trading.outcomes.engine import label_outcomes
from t0_trading.outcomes.model import Action, OutcomeLabel

__all__ = ["Action", "OutcomeLabel", "label_outcomes"]
