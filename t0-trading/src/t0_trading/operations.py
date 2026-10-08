"""Read-only operational evidence. Unknown live state is never reported as healthy."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from t0_trading.arbitration.journal import ShadowArbitrationManifest
from t0_trading.configuration import TradingConfiguration
from t0_trading.context import DecisionContext
from t0_trading.context.regime import context_identity
from t0_trading.evidence_paths import PROMOTION_EVIDENCE_PREFIX, shadow_journal_manifest_key
from t0_trading.execution.engine import reduce_paper_order
from t0_trading.execution.readiness import PaperReadiness, load_paper_readiness
from t0_trading.execution.runtime import PaperSession
from t0_trading.promotion.model import ArbitratedSessionReport
from t0_trading.promotion.publication import PromotionEvidenceStore


class OperationalStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    trade_date: date
    readiness: PaperReadiness
    journal: Literal["COMMITTED", "MISSING", "INVALID"]
    publication: Literal["COMMITTED", "MISSING", "INVALID"]
    candidate_count: int | None
    arbitration_count: int | None
    capture_gap_count: int | None
    paper_order_counts: dict[str, int] | None
    open_reservation_count: int | None
    ledger_reconciled: bool | None
    # Sealed S3 artifacts are not heartbeats. Live ages require a separate live read model.
    live_freshness: Literal["NOT_OBSERVED", "CURRENT", "STALE_OR_INCOMPLETE"] = "NOT_OBSERVED"
    market_health: dict[str, tuple[str, ...]]
    security_status: Literal["NOT_CERTIFIED"] = "NOT_CERTIFIED"
    reasons: tuple[str, ...]
    capital_authorized: Literal[False] = False


def operational_status(
    store: PromotionEvidenceStore,
    configuration: TradingConfiguration,
    *,
    trade_date: date,
    previous_session: date,
    paper_session: PaperSession | None = None,
    paper_enabled: bool = False,
    context: DecisionContext | None = None,
    observed_at: datetime | None = None,
) -> OperationalStatus:
    readiness = load_paper_readiness(
        store,
        configuration,
        trade_date=trade_date,
        previous_session=previous_session,
        enabled=paper_enabled,
        costs=paper_session.costs if paper_session is not None else None,
    )
    reasons = [readiness.reason, "LIVE_FRESHNESS_NOT_OBSERVED", "SECURITY_STATUS_NOT_CERTIFIED"]
    journal_state: Literal["COMMITTED", "MISSING", "INVALID"] = "MISSING"
    publication_state: Literal["COMMITTED", "MISSING", "INVALID"] = "MISSING"
    candidate_count = arbitration_count = gaps = None
    try:
        value = store.read_json(shadow_journal_manifest_key(trade_date))
        if value is not None:
            journal = ShadowArbitrationManifest.model_validate(value)
            if journal.trade_date != trade_date:
                raise ValueError("journal date mismatch")
            journal_state = "COMMITTED"
            candidate_count, arbitration_count = journal.candidate_count, journal.arbitration_count
    except Exception:
        journal_state = "INVALID"
    if journal_state != "COMMITTED":
        reasons.append(f"JOURNAL_{journal_state}")
    try:
        value = store.read_json(
            f"{PROMOTION_EVIDENCE_PREFIX}/sessions/trade_date={trade_date}/arbitrated_session.json"
        )
        if value is not None:
            report = ArbitratedSessionReport.model_validate(value)
            if report.trade_date != trade_date:
                raise ValueError("publication date mismatch")
            publication_state = "COMMITTED"
            gaps = report.capture_gap_count
    except Exception:
        publication_state = "INVALID"
    if publication_state != "COMMITTED":
        reasons.append(f"PUBLICATION_{publication_state}")
    counts = None
    reservations = None
    reconciled = None
    if paper_session is None:
        reasons.append("PAPER_REPOSITORY_NOT_OBSERVED")
    else:
        try:
            verified = PaperSession.model_validate(paper_session.model_dump())
            if verified.ledger.trade_date != trade_date:
                raise ValueError("paper session date mismatch")
            counts = dict(
                Counter(
                    reduce_paper_order(item, verified.events(item)).status
                    for item in verified.intents
                )
            )
            reservations = len(verified.ledger.reservations)
            reconciled = True
        except ValueError:
            reconciled = False
            reasons.append("PAPER_LEDGER_RECONCILIATION_FAILED")
    freshness: Literal["NOT_OBSERVED", "CURRENT", "STALE_OR_INCOMPLETE"] = "NOT_OBSERVED"
    market_health: dict[str, tuple[str, ...]] = {}
    if context is not None:
        if observed_at is None or observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise ValueError("live status requires an aware observation clock")
        age = (observed_at.astimezone(UTC) - context.decision_at).total_seconds()
        policy = configuration.resolve_context(trade_date)
        regime_policy = configuration.resolve_regime(trade_date)
        if (
            context.trade_date != trade_date
            or age < 0
            or context.context_configuration_sha256
            != context_identity(policy, regime_policy, configuration.resolve_breadth(trade_date))
        ):
            raise ValueError("live status context lineage mismatch")
        for item in context.indices:
            market_health[item.index] = tuple(
                dict.fromkeys(
                    (
                        *item.reasons,
                        *(
                            ("STALE_INDEX",)
                            if item.age_seconds is None
                            or float(item.age_seconds) + age > policy.index_stale_after_seconds
                            else ()
                        ),
                    )
                )
            )
        for item in context.market_statuses:
            market_health[item.market] = item.reasons
        for basket in context.breadth:
            market_health[f"breadth:{basket.index}"] = basket.reasons
        if context.market_basis == "CONSTITUENT_BREADTH":
            analytics_health = market_health.get(
                f"breadth:{context.market_reference_index}", ("MISSING_REQUIRED_BREADTH",)
            )
            context_age_limit = configuration.resolve(trade_date).features.cadence_seconds
        else:
            analytics_health = tuple(
                reason for item in context.indices for reason in market_health[item.index]
            )
            context_age_limit = policy.index_stale_after_seconds
        freshness = (
            "CURRENT"
            if not context.reasons
            and not analytics_health
            and context.is_tradable
            and context.data_mode == "LIVE"
            and age <= context_age_limit
            else "STALE_OR_INCOMPLETE"
        )
        reasons.remove("LIVE_FRESHNESS_NOT_OBSERVED")
        if freshness != "CURRENT":
            reasons.append("LIVE_CONTEXT_STALE_OR_INCOMPLETE")
    return OperationalStatus(
        trade_date=trade_date,
        readiness=readiness,
        journal=journal_state,
        publication=publication_state,
        candidate_count=candidate_count,
        arbitration_count=arbitration_count,
        capture_gap_count=gaps,
        paper_order_counts=counts,
        open_reservation_count=reservations,
        ledger_reconciled=reconciled,
        live_freshness=freshness,
        market_health=market_health,
        reasons=tuple(reasons),
    )
