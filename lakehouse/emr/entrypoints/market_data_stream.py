"""CLI adapter for terminal SSI Stream capture replay on EMR."""

from typing import Annotated

import typer
from emr_jobs.market_data.stream_job import run


def main(
    source_date: Annotated[
        str,
        typer.Option(help="Exchange-local source date in YYYY-MM-DD format."),
    ],
    landing_uri: Annotated[str, typer.Option(help="Landing S3 root URI.")],
    contracts_uri: Annotated[str, typer.Option(help="Versioned contract bundle URI.")],
) -> None:
    run(
        source_date=source_date,
        landing_uri=landing_uri,
        contracts_uri=contracts_uri,
    )


if __name__ == "__main__":
    typer.run(main)
