"""Bounded Trino mutations for current ArXiv metadata."""

from datetime import date

from mini_lakehouse.config.settings import Settings
from mini_lakehouse.contracts import PlatformContracts, load_contracts
from mini_lakehouse.platform.trino import SqlExecutor


class ArxivCurationRepository:
    def __init__(
        self,
        settings: Settings,
        contracts: PlatformContracts | None = None,
    ) -> None:
        self._settings = settings
        registry = contracts or load_contracts(settings.contracts_dir)
        self._source = registry.source("arxiv")
        self._product = registry.curated_product("arxiv")

    def _source_relation(self) -> str:
        return self._source.table_identifier("oai_records_raw").trino(self._settings.trino.catalog)

    def _checkpoint_relation(self) -> str:
        return self._source.table_identifier("oai_checkpoints").trino(self._settings.trino.catalog)

    def _relation(self, key: str) -> str:
        return self._product.table_identifier(key).trino(self._settings.trino.catalog)

    def _latest_source_sql(self) -> str:
        return f"""
            SELECT
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
                pdf_url,
                is_deleted,
                record_sha256,
                ingested_at
            FROM (
                SELECT
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
                    pdf_url,
                    is_deleted,
                    record_sha256,
                    ingested_at,
                    row_number() OVER (
                        PARTITION BY arxiv_id
                        ORDER BY datestamp_date DESC, ingested_at DESC, record_sha256 DESC
                    ) AS mutation_rank
                FROM {self._source_relation()}
                WHERE datestamp_date = ?
            )
            WHERE mutation_rank = 1
        """

    def _merge_papers_sql(self) -> str:
        return f"""
            MERGE INTO {self._relation("papers")} AS target
            USING (
                SELECT
                    arxiv_id,
                    oai_identifier,
                    nullif(trim(title), '') AS title,
                    nullif(trim(abstract), '') AS abstract,
                    nullif(trim(license_uri), '') AS license_uri,
                    nullif(trim(doi), '') AS doi,
                    nullif(trim(journal_ref), '') AS journal_ref,
                    nullif(trim(comments), '') AS comments,
                    created_date,
                    updated_date,
                    datestamp_date AS oai_datestamp,
                    pdf_url,
                    is_deleted,
                    record_sha256 AS source_record_sha256
                FROM ({self._latest_source_sql()})
            ) AS source
            ON target.arxiv_id = source.arxiv_id
            WHEN MATCHED AND (
                source.oai_datestamp > target.oai_datestamp
                OR (
                    source.oai_datestamp = target.oai_datestamp
                    AND source.source_record_sha256 <> target.source_record_sha256
                )
            ) THEN UPDATE SET
                oai_identifier = source.oai_identifier,
                title = source.title,
                abstract = source.abstract,
                license_uri = source.license_uri,
                doi = source.doi,
                journal_ref = source.journal_ref,
                comments = source.comments,
                created_date = source.created_date,
                updated_date = source.updated_date,
                oai_datestamp = source.oai_datestamp,
                pdf_url = source.pdf_url,
                is_deleted = source.is_deleted,
                source_record_sha256 = source.source_record_sha256,
                curated_at = current_timestamp
            WHEN NOT MATCHED THEN INSERT (
                arxiv_id,
                oai_identifier,
                title,
                abstract,
                license_uri,
                doi,
                journal_ref,
                comments,
                created_date,
                updated_date,
                oai_datestamp,
                pdf_url,
                is_deleted,
                source_record_sha256,
                curated_at
            ) VALUES (
                source.arxiv_id,
                source.oai_identifier,
                source.title,
                source.abstract,
                source.license_uri,
                source.doi,
                source.journal_ref,
                source.comments,
                source.created_date,
                source.updated_date,
                source.oai_datestamp,
                source.pdf_url,
                source.is_deleted,
                source.source_record_sha256,
                current_timestamp
            )
        """

    def _pending_mutations_sql(self) -> str:
        return f"""
            SELECT source.*
            FROM ({self._latest_source_sql()}) AS source
            LEFT JOIN {self._relation("papers")} AS paper
              ON paper.arxiv_id = source.arxiv_id
            WHERE paper.arxiv_id IS NULL
               OR source.datestamp_date > paper.oai_datestamp
               OR (
                    source.datestamp_date = paper.oai_datestamp
                    AND source.record_sha256 <> paper.source_record_sha256
               )
        """

    def _delete_children_sql(self, key: str) -> str:
        return f"""
            DELETE FROM {self._relation(key)}
            WHERE arxiv_id IN (
                SELECT arxiv_id
                FROM ({self._pending_mutations_sql()})
            )
        """

    def _insert_authors_sql(self) -> str:
        return f"""
            INSERT INTO {self._relation("paper_authors")} (
                arxiv_id,
                author_position,
                keyname,
                forenames,
                suffix,
                affiliation,
                oai_datestamp,
                curated_at
            )
            SELECT
                source.arxiv_id,
                author.author_position,
                author.keyname,
                nullif(trim(author.forenames), ''),
                nullif(trim(author.suffix), ''),
                nullif(trim(author.affiliation), ''),
                source.datestamp_date,
                current_timestamp
            FROM ({self._pending_mutations_sql()}) AS source
            CROSS JOIN UNNEST(
                CAST(
                    json_parse(source.authors_json)
                    AS ARRAY(
                        ROW(
                            keyname VARCHAR,
                            forenames VARCHAR,
                            suffix VARCHAR,
                            affiliation VARCHAR
                        )
                    )
                )
            ) WITH ORDINALITY AS author(
                keyname,
                forenames,
                suffix,
                affiliation,
                author_position
            )
            WHERE NOT source.is_deleted
              AND nullif(trim(author.keyname), '') IS NOT NULL
        """

    def _insert_categories_sql(self) -> str:
        return f"""
            INSERT INTO {self._relation("paper_categories")} (
                arxiv_id,
                category,
                is_primary,
                oai_datestamp,
                curated_at
            )
            SELECT
                source.arxiv_id,
                category,
                category = source.primary_category,
                source.datestamp_date,
                current_timestamp
            FROM ({self._pending_mutations_sql()}) AS source
            CROSS JOIN UNNEST(split(coalesce(source.categories, ''), ' '))
                AS categories(category)
            WHERE NOT source.is_deleted
              AND nullif(trim(category), '') IS NOT NULL
        """

    def curate_day(self, executor: SqlExecutor, day: date) -> dict[str, int]:
        source_result = executor.execute(
            f"""
            SELECT count(*)
            FROM {self._source_relation()}
            WHERE datestamp_date = ?
            """,
            (day,),
        )
        source_rows = int(source_result.rows[0][0])
        checkpoint = executor.execute(
            f"""
            SELECT record_count
            FROM {self._checkpoint_relation()}
            WHERE datestamp_date = ?
            """,
            (day,),
        )
        if len(checkpoint.rows) != 1:
            raise RuntimeError(f"ArXiv landing day {day} has no successful checkpoint")
        checkpoint_rows = int(checkpoint.rows[0][0])
        if checkpoint_rows != source_rows:
            raise RuntimeError(
                f"ArXiv landing day {day} checkpoint expects {checkpoint_rows} records, "
                f"found {source_rows}"
            )
        pending = executor.execute(
            f"SELECT count(*) FROM ({self._pending_mutations_sql()})",
            (day,),
        )
        if len(pending.rows) != 1:
            raise RuntimeError("Trino returned no ArXiv pending mutation count")
        pending_rows = int(pending.rows[0][0])

        if pending_rows:
            # The paper hash is the durable publication marker. Children are
            # replaced first so a partial failure remains pending and can be
            # repaired safely; the paper MERGE commits that mutation last.
            executor.execute(self._delete_children_sql("paper_authors"), (day,))
            executor.execute(self._delete_children_sql("paper_categories"), (day,))
            executor.execute(self._insert_authors_sql(), (day,))
            executor.execute(self._insert_categories_sql(), (day,))
            executor.execute(self._merge_papers_sql(), (day,))

        counts = executor.execute(
            f"""
            SELECT
                count(*) AS papers,
                (
                    SELECT count(*)
                    FROM {self._relation("paper_authors")}
                    WHERE oai_datestamp = ?
                ) AS authors,
                (
                    SELECT count(*)
                    FROM {self._relation("paper_categories")}
                    WHERE oai_datestamp = ?
                ) AS categories
            FROM {self._relation("papers")}
            WHERE oai_datestamp = ?
            """,
            (day, day, day),
        )
        if len(counts.rows) != 1:
            raise RuntimeError("Trino returned no ArXiv curation metrics")
        row = counts.rows[0]
        return {
            "source": source_rows,
            "papers": int(row[0]),
            "authors": int(row[1]),
            "categories": int(row[2]),
            "mutations": pending_rows,
        }
