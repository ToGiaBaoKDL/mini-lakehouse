"""CLI adapter for terminal SSI Stream capture replay on EMR."""

from typing import Annotated

import typer
from emr_jobs.market_data.stream_job import run


def main(
    capture_manifest_uri: Annotated[
        str,
        typer.Option(help="Immutable terminal SSI Stream manifest URI."),
    ],
    contracts_uri: Annotated[str, typer.Option(help="Versioned contract bundle URI.")],
) -> None:
    run(
        capture_manifest_uri=capture_manifest_uri,
        contracts_uri=contracts_uri,
    )


if __name__ == "__main__":
    typer.run(main)
