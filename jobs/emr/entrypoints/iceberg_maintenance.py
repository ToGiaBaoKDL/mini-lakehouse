"""CLI adapter for contract-owned Iceberg maintenance."""

from typing import Annotated

import typer
from emr_jobs.maintenance import run


def main(
    contracts_uri: Annotated[str, typer.Option(help="Versioned contract bundle URI.")],
) -> None:
    run(contracts_uri=contracts_uri)


if __name__ == "__main__":
    typer.run(main)
