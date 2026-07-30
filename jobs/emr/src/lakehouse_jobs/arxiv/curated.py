"""Publish the latest canonical ArXiv metadata mutation."""

from pyspark.sql import SparkSession

from lakehouse_jobs.common.contracts import spark_identifier
from lakehouse_platform.contracts.curated import CuratedProductContract


def publish(
    spark: SparkSession,
    *,
    catalog_name: str,
    source_table: str,
    product: CuratedProductContract,
    source_date: str,
) -> None:
    papers = spark_identifier(catalog_name, product.table_identifier("papers"))
    authors = spark_identifier(catalog_name, product.table_identifier("paper_authors"))
    categories = spark_identifier(
        catalog_name,
        product.table_identifier("paper_categories"),
    )
    spark.sql(
        f"""
        CREATE OR REPLACE TEMP VIEW arxiv_day_latest AS
        SELECT *
        FROM (
            SELECT
                *,
                row_number() OVER (
                    PARTITION BY arxiv_id
                    ORDER BY datestamp_date DESC, ingested_at DESC, record_sha256 DESC
                ) AS mutation_rank
            FROM {source_table}
            WHERE datestamp_date = DATE '{source_date}'
        )
        WHERE mutation_rank = 1
        """
    )
    spark.sql(
        f"""
        CREATE OR REPLACE TEMP VIEW arxiv_pending AS
        SELECT source.*
        FROM arxiv_day_latest source
        LEFT JOIN {papers} target ON target.arxiv_id = source.arxiv_id
        WHERE target.arxiv_id IS NULL
           OR source.datestamp_date > target.oai_datestamp
           OR (
                source.datestamp_date = target.oai_datestamp
                AND source.record_sha256 <> target.source_record_sha256
           )
        """
    )
    spark.catalog.cacheTable("arxiv_pending")
    if not spark.table("arxiv_pending").take(1):
        spark.catalog.uncacheTable("arxiv_pending")
        return
    spark.sql(
        f"""
        MERGE INTO {authors} target
        USING (SELECT DISTINCT arxiv_id FROM arxiv_pending) source
        ON target.arxiv_id = source.arxiv_id
        WHEN MATCHED THEN DELETE
        """
    )
    spark.sql(
        f"""
        MERGE INTO {categories} target
        USING (SELECT DISTINCT arxiv_id FROM arxiv_pending) source
        ON target.arxiv_id = source.arxiv_id
        WHEN MATCHED THEN DELETE
        """
    )
    spark.sql(
        """
        SELECT
            source.arxiv_id,
            position + 1 AS author_position,
            author.keyname,
            nullif(trim(author.forenames), '') AS forenames,
            nullif(trim(author.suffix), '') AS suffix,
            nullif(trim(author.affiliation), '') AS affiliation,
            source.datestamp_date AS oai_datestamp,
            current_timestamp() AS curated_at
        FROM arxiv_pending source
        LATERAL VIEW posexplode(
            from_json(
                source.authors_json,
                'array<struct<keyname:string,forenames:string,suffix:string,affiliation:string>>'
            )
        ) exploded AS position, author
        WHERE NOT source.is_deleted
          AND nullif(trim(author.keyname), '') IS NOT NULL
        """
    ).writeTo(authors).append()
    spark.sql(
        """
        SELECT DISTINCT
            source.arxiv_id,
            trim(category) AS category,
            trim(category) = source.primary_category AS is_primary,
            source.datestamp_date AS oai_datestamp,
            current_timestamp() AS curated_at
        FROM arxiv_pending source
        LATERAL VIEW explode(split(coalesce(source.categories, ''), ' ')) exploded AS category
        WHERE NOT source.is_deleted
          AND nullif(trim(category), '') IS NOT NULL
        """
    ).writeTo(categories).append()
    spark.sql(
        f"""
        MERGE INTO {papers} target
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
                record_sha256 AS source_record_sha256,
                current_timestamp() AS curated_at
            FROM arxiv_pending
        ) source
        ON target.arxiv_id = source.arxiv_id
        WHEN MATCHED THEN UPDATE SET
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
            curated_at = source.curated_at
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
            source.curated_at
        )
        """
    )
    spark.catalog.uncacheTable("arxiv_pending")
