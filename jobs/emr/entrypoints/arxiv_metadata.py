"""CLI adapter for the ArXiv metadata EMR job."""

from typing import Annotated

import typer
from lakehouse_jobs.arxiv.job import run


def main(
    source_date: Annotated[str, typer.Option(help="OAI datestamp day in YYYY-MM-DD format.")],
    landing_uri: Annotated[str, typer.Option(help="Landing S3 root URI.")],
    contracts_uri: Annotated[str, typer.Option(help="Versioned contract bundle URI.")],
    catalog_name: Annotated[str, typer.Option(help="Spark Iceberg catalog alias.")] = "glue",
    max_pages: Annotated[
        int,
        typer.Option(min=1, help="Maximum OAI pages accepted for one source day."),
    ] = 100,
) -> None:
    run(
        source_date=source_date,
        landing_uri=landing_uri,
        contracts_uri=contracts_uri,
        catalog_name=catalog_name,
        max_pages=max_pages,
    )


if __name__ == "__main__":
    typer.run(main)
