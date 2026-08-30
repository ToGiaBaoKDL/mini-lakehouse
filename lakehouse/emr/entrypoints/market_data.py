"""CLI adapter for the bounded SSI market-data EMR job."""

from typing import Annotated

import typer
from emr_jobs.market_data.job import run


def main(
    source_date: Annotated[
        str,
        typer.Option(help="Exchange-local trade date in YYYY-MM-DD format."),
    ],
    capture_manifest_uri: Annotated[
        str,
        typer.Option(help="Immutable SSI REST capture manifest URI."),
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
