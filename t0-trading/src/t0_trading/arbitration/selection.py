"""Causal, immutable handoff shared by shadow journals and realtime consumers."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

from t0_trading.arbitration.model import CandidateArbitration, require_selected_candidate
from t0_trading.context.model import DecisionContext
from t0_trading.features.model import FeatureSnapshot
from t0_trading.identity import canonical_json
from t0_trading.strategy.baselines import RELATIVE_PEER_LAG, BaselineCandidate


class RealtimeSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    candidate: BaselineCandidate
    arbitration: CandidateArbitration
    context: DecisionContext
    features: tuple[FeatureSnapshot, ...]
    lagged_peer: FeatureSnapshot | None = None

    def canonical_bytes(self) -> bytes:
        # Computed feature fields participate in their own feature hash, but must not become
        # forbidden input fields when this enclosing payload is rehydrated after a restart.
        return canonical_json(self.model_dump(mode="json", round_trip=True))

    @model_validator(mode="after")
    def validate_lineage(self) -> "RealtimeSelection":
        require_selected_candidate(self.candidate, self.arbitration)
        candidate = self.candidate
        context = self.context
        if (
            candidate.context_snapshot_sha256 != context.sha256
            or candidate.context_configuration_sha256 != context.context_configuration_sha256
            or candidate.context_version != context.context_version
            or candidate.context_data_mode != context.data_mode
            or candidate.market_regime != context.regime
            or candidate.decision_at != context.decision_at
            or candidate.trade_date != context.trade_date
            or context.data_mode != "LIVE"
        ):
            raise ValueError("realtime selection requires matching live context")
        if context.selection_block_reason(realtime=True) is not None:
            raise ValueError(
                "realtime selection requires healthy regime and verified market status"
            )
        by_symbol = {item.symbol: item for item in self.features}
        feature = by_symbol.get(candidate.symbol)
        if (
            len(by_symbol) != len(self.features)
            or feature is None
            or feature.sha256 != candidate.feature_snapshot_sha256
            or any(
                item.decision_at != candidate.decision_at
                or item.trade_date != candidate.trade_date
                or item.configuration_sha256 != context.feature_configuration_sha256
                or item.reasons
                for item in self.features
            )
            or (
                candidate.peer_feature_snapshot_sha256 is not None
                and candidate.peer_feature_snapshot_sha256
                not in {item.sha256 for item in self.features if item.symbol != candidate.symbol}
            )
        ):
            raise ValueError("realtime selection feature lineage is incomplete or ineligible")
        lag = self.lagged_peer
        if candidate.strategy == "vic_vhm_relative":
            if (
                lag is None
                or lag.symbol == candidate.symbol
                or lag.symbol not in by_symbol
                or lag.decision_at != candidate.decision_at - RELATIVE_PEER_LAG
                or lag.trade_date != candidate.trade_date
                or lag.configuration_sha256 != context.feature_configuration_sha256
                or lag.reasons
            ):
                raise ValueError("relative selection requires eligible causal peer history")
        elif lag is not None:
            raise ValueError("only relative selection may carry lagged peer history")
        return self
