"""CLI adapter for the GitHub Archive EMR job."""

from typing import Annotated

import typer
from emr_jobs.github_archive.job import run


def main(
    source_date: Annotated[str, typer.Option(help="UTC source day in YYYY-MM-DD format.")],
    capture_manifest_uri: Annotated[
        str,
        typer.Option(help="Terminal GitHub Archive capture manifest URI."),
    ],
    contracts_uri: Annotated[str, typer.Option(help="Versioned contract bundle URI.")],
) -> None:
    run(
        source_date=source_date,
        capture_manifest_uri=capture_manifest_uri,
        contracts_uri=contracts_uri,
    )


if __name__ == "__main__":
    typer.run(main)
