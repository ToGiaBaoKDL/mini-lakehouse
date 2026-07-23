"""ArXiv current-state curation use case."""

from contextlib import nullcontext
from datetime import date

from mini_lakehouse.config.settings import Settings
from mini_lakehouse.contracts import PlatformContracts, load_contracts
from mini_lakehouse.curated_products.arxiv.models import ArxivCurationResult
from mini_lakehouse.curated_products.arxiv.repository import ArxivCurationRepository
from mini_lakehouse.curated_products.table_manager import CuratedTableManager
from mini_lakehouse.platform.trino import SqlExecutor, TrinoExecutor


class ArxivCurationService:
    def __init__(
        self,
        settings: Settings,
        *,
        executor: SqlExecutor | None = None,
        contracts: PlatformContracts | None = None,
        table_manager: CuratedTableManager | None = None,
        repository: ArxivCurationRepository | None = None,
    ) -> None:
        self._settings = settings
        registry = contracts or load_contracts(settings.contracts_dir)
        product = registry.curated_product("arxiv")
        if product.upstream_sources != ("arxiv",):
            raise ValueError("ArXiv curation requires arxiv as its only upstream source")
        self._executor = executor
        self._table_manager = table_manager or CuratedTableManager(settings, product.name, registry)
        self._repository = repository or ArxivCurationRepository(settings, registry)

    def curate_day(self, day: date) -> ArxivCurationResult:
        owned_executor = TrinoExecutor(self._settings.trino) if self._executor is None else None
        context = owned_executor if owned_executor is not None else nullcontext(self._executor)
        with context as executor:
            if executor is None:
                raise RuntimeError("A SQL executor is required")
            self._table_manager.ensure_tables(executor)
            counts = self._repository.curate_day(executor, day)
        return ArxivCurationResult(
            datestamp_date=day,
            source_rows=counts["source"],
            paper_rows=counts["papers"],
            author_rows=counts["authors"],
            category_rows=counts["categories"],
            was_written=counts["mutations"] > 0,
        )
