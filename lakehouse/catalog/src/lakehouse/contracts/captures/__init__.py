"""Runtime capture contracts shared by producers and consumers."""

from lakehouse.contracts.captures.arxiv import (
    ArxivOaiManifest,
    ArxivOaiPage,
    arxiv_snapshot,
)
from lakehouse.contracts.captures.github_archive import (
    GitHubArchiveManifest,
    GitHubArchiveObject,
)

__all__ = [
    "ArxivOaiManifest",
    "ArxivOaiPage",
    "GitHubArchiveManifest",
    "GitHubArchiveObject",
    "arxiv_snapshot",
]
