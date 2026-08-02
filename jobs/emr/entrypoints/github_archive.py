"""CLI adapter for the GitHub Archive EMR job."""

from typing import Annotated

import typer
from emr_jobs.github_archive.job import run


def main(
    source_date: Annotated[str, typer.Option(help="UTC source day in YYYY-MM-DD format.")],
    landing_uri: Annotated[str, typer.Option(help="Landing S3 root URI.")],
    contracts_uri: Annotated[str, typer.Option(help="Versioned contract bundle URI.")],
    capture_workers: Annotated[
        int,
        typer.Option(min=1, max=24, help="Concurrent archive downloads."),
    ] = 8,
) -> None:
    run(
        source_date=source_date,
        landing_uri=landing_uri,
        contracts_uri=contracts_uri,
        capture_workers=capture_workers,
    )


if __name__ == "__main__":
    typer.run(main)
