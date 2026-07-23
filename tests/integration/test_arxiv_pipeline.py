import json
import os
from datetime import UTC, date, datetime

import pytest

from mini_lakehouse.config import get_settings
from mini_lakehouse.contracts import (
    PlatformContracts,
    load_contracts,
    partition_expression,
    trino_type,
)
from mini_lakehouse.curated_products.arxiv.service import ArxivCurationService
from mini_lakehouse.platform.trino import SqlExecutor, TrinoExecutor

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_LAKEHOUSE_INTEGRATION") != "1",
        reason="set RUN_LAKEHOUSE_INTEGRATION=1 with a clean Compose stack running",
    ),
]

_TEST_DAY = date(2099, 12, 31)
_ARXIV_ID = "mini-lakehouse-e2e-arxiv"


def _ensure_landing_tables(
    executor: SqlExecutor,
    contracts: PlatformContracts,
) -> None:
    settings = get_settings()
    source = contracts.source("arxiv")
    for table in source.tables:
        columns = ",\n    ".join(
            f'"{column.name}" {trino_type(column)}{" NOT NULL" if column.required else ""}'
            for column in table.columns
        )
        partitioning = ", ".join(
            f"'{partition_expression(partition)}'" for partition in table.partitioning
        )
        executor.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {
                source.table_identifier(table.key).trino(settings.trino.catalog)
            } (
                {columns}
            )
            WITH (
                format = 'PARQUET',
                format_version = 2,
                partitioning = ARRAY[{partitioning}]
            )
            """
        )


def _delete_test_rows(
    executor: SqlExecutor,
    contracts: PlatformContracts,
) -> None:
    settings = get_settings()
    source = contracts.source("arxiv")
    product = contracts.curated_product("arxiv")
    for table_key in ("paper_authors", "paper_categories", "papers"):
        executor.execute(
            f"DELETE FROM {product.table_identifier(table_key).trino(settings.trino.catalog)} "
            "WHERE arxiv_id = ?",
            (_ARXIV_ID,),
        )
    for table_key in ("oai_records_raw", "oai_checkpoints"):
        executor.execute(
            f"DELETE FROM {source.table_identifier(table_key).trino(settings.trino.catalog)} "
            "WHERE datestamp_date = ?",
            (_TEST_DAY,),
        )


def _insert_landing_record(
    executor: SqlExecutor,
    contracts: PlatformContracts,
) -> None:
    settings = get_settings()
    source = contracts.source("arxiv")
    raw_hash = "a" * 64
    raw_object_key = "api/arxiv/raw/oai/datestamp=2099-12-31/integration-responses.tar.zst"
    authors_json = json.dumps(
        [
            {
                "keyname": "Nguyen",
                "forenames": "An",
                "suffix": "Jr.",
                "affiliation": "Lakehouse Lab",
            },
            {
                "keyname": "Tran",
                "forenames": None,
                "suffix": None,
                "affiliation": None,
            },
        ],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    executor.execute(
        f"""
        INSERT INTO {source.table_identifier("oai_records_raw").trino(settings.trino.catalog)} (
            oai_identifier,
            arxiv_id,
            title,
            abstract,
            authors_json,
            categories,
            primary_category,
            license_uri,
            doi,
            journal_ref,
            comments,
            created_date,
            updated_date,
            datestamp_date,
            set_specs,
            pdf_url,
            is_deleted,
            raw_object_key,
            raw_object_sha256,
            record_index,
            record_sha256,
            ingested_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            f"oai:arXiv.org:{_ARXIV_ID}",
            _ARXIV_ID,
            "ArXiv curation integration test",
            "Synthetic metadata for the local integration stack.",
            authors_json,
            "cs.AI cs.LG",
            "cs.AI",
            None,
            None,
            None,
            None,
            _TEST_DAY,
            _TEST_DAY,
            _TEST_DAY,
            "cs:cs.AI",
            f"https://arxiv.org/pdf/{_ARXIV_ID}",
            False,
            raw_object_key,
            raw_hash,
            0,
            "b" * 64,
            datetime(2099, 12, 31, tzinfo=UTC),
        ),
    )
    executor.execute(
        f"""
        INSERT INTO {source.table_identifier("oai_checkpoints").trino(settings.trino.catalog)} (
            datestamp_date,
            raw_object_key,
            raw_object_sha256,
            page_count,
            record_count,
            schema_version,
            published_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            _TEST_DAY,
            raw_object_key,
            raw_hash,
            1,
            1,
            source.table("oai_records_raw").schema_contract,
            datetime(2099, 12, 31, tzinfo=UTC),
        ),
    )


def _curated_snapshot_ids(
    executor: SqlExecutor,
    contracts: PlatformContracts,
) -> tuple[int, ...]:
    settings = get_settings()
    product = contracts.curated_product("arxiv")
    snapshot_ids: list[int] = []
    for table_key in ("papers", "paper_authors", "paper_categories"):
        identifier = product.table_identifier(table_key)
        namespace = ".".join(identifier.namespace)
        relation = f'"{settings.trino.catalog}"."{namespace}"."{identifier.name}$snapshots"'
        result = executor.execute(f"SELECT max(snapshot_id) FROM {relation}")
        assert len(result.rows) == 1
        snapshot_ids.append(int(result.rows[0][0]))
    return tuple(snapshot_ids)


def test_arxiv_curation_expands_authors_and_is_idempotent() -> None:
    settings = get_settings()
    contracts = load_contracts(settings.contracts_dir)
    product = contracts.curated_product("arxiv")

    try:
        with TrinoExecutor(settings.trino) as executor:
            _ensure_landing_tables(executor, contracts)
            _delete_test_rows(executor, contracts)
            _insert_landing_record(executor, contracts)
            curation = ArxivCurationService(
                settings,
                executor=executor,
                contracts=contracts,
            )
            first_result = curation.curate_day(_TEST_DAY)
            first_snapshot_ids = _curated_snapshot_ids(executor, contracts)
            second_result = curation.curate_day(_TEST_DAY)
            second_snapshot_ids = _curated_snapshot_ids(executor, contracts)

            papers = executor.execute(
                f"""
                SELECT count(*)
                FROM {product.table_identifier("papers").trino(settings.trino.catalog)}
                WHERE arxiv_id = ?
                """,
                (_ARXIV_ID,),
            )
            authors = executor.execute(
                f"""
                SELECT author_position, keyname, forenames, suffix, affiliation
                FROM {product.table_identifier("paper_authors").trino(settings.trino.catalog)}
                WHERE arxiv_id = ?
                ORDER BY author_position
                """,
                (_ARXIV_ID,),
            )
            categories = executor.execute(
                f"""
                SELECT category, is_primary
                FROM {product.table_identifier("paper_categories").trino(settings.trino.catalog)}
                WHERE arxiv_id = ?
                ORDER BY category
                """,
                (_ARXIV_ID,),
            )

        assert papers.rows == ((1,),)
        assert first_result.was_written is True
        assert second_result.was_written is False
        assert second_snapshot_ids == first_snapshot_ids
        assert authors.rows == (
            (1, "Nguyen", "An", "Jr.", "Lakehouse Lab"),
            (2, "Tran", None, None, None),
        )
        assert categories.rows == (("cs.AI", True), ("cs.LG", False))
    finally:
        with TrinoExecutor(settings.trino) as executor:
            _delete_test_rows(executor, contracts)
