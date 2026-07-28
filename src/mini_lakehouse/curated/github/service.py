"""GitHub curation use case boundary."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from mini_lakehouse.config.settings import Settings
from mini_lakehouse.contracts import PlatformContracts, load_contracts
from mini_lakehouse.curated.github.repository import GithubCurationRepository
from mini_lakehouse.platform.trino import SqlExecutor, TrinoExecutor
from mini_lakehouse.sources.github_archive.models import ArchiveHour


class GithubCurationResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_hour: datetime
    source_rows: int
    event_rows: int
    actor_rows: int
    repository_rows: int


class GithubCurationService:
    def __init__(
        self,
        settings: Settings,
        *,
        executor: SqlExecutor | None = None,
        contracts: PlatformContracts | None = None,
        repository: GithubCurationRepository | None = None,
    ) -> None:
        self._settings = settings
        registry = contracts or load_contracts(settings.contracts_dir)
        source = registry.source("github_archive")
        product = registry.curated_product("github")
        if product.upstream_sources != (source.name,):
            raise ValueError("GitHub curation requires github_archive as its only upstream source")
        self._executor = executor
        self._repository = repository or GithubCurationRepository(settings, registry)

    def curate(self, source_hour: ArchiveHour) -> GithubCurationResult:
        if self._executor is not None:
            source_rows, counts = self._repository.curate_hour(
                self._executor,
                source_hour,
            )
        else:
            with TrinoExecutor(self._settings.trino) as executor:
                source_rows, counts = self._repository.curate_hour(executor, source_hour)
        return GithubCurationResult(
            source_hour=source_hour.value,
            source_rows=source_rows,
            event_rows=counts["events"],
            actor_rows=counts["actors_current"],
            repository_rows=counts["repositories_current"],
        )
