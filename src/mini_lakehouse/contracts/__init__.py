"""Cross-domain catalog contracts."""

from mini_lakehouse.contracts.tables import (
    CONTRIBUTOR_ACTIVITY_DAILY,
    GITHUB_EVENTS_RAW,
    REPOSITORY_ACTIVITY_DAILY,
    TableIdentifier,
)

__all__ = [
    "CONTRIBUTOR_ACTIVITY_DAILY",
    "GITHUB_EVENTS_RAW",
    "REPOSITORY_ACTIVITY_DAILY",
    "TableIdentifier",
]
