"""Canonical object paths for prospective T0 evidence."""

from datetime import date

PROMOTION_EVIDENCE_PREFIX = "stream/ssi_fastconnect_stream/promotion"


def shadow_journal_root(trade_date: date) -> str:
    return f"{PROMOTION_EVIDENCE_PREFIX}/shadow_journals/trade_date={trade_date.isoformat()}"


def shadow_journal_manifest_key(trade_date: date) -> str:
    return f"{shadow_journal_root(trade_date)}/manifest.json"
