"""Build the validated contract artifact consumed by remote data jobs."""

from pathlib import Path
from typing import Annotated

import typer

from lakehouse.config.settings import DEFAULT_CONTRACTS_DIR
from lakehouse.contracts.loader import load_contracts


def main(
    output: Annotated[Path, typer.Option(dir_okay=False)],
    contracts: Annotated[Path, typer.Option(file_okay=False)] = DEFAULT_CONTRACTS_DIR,
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        load_contracts(contracts).model_dump_json(indent=2),
        encoding="utf-8",
    )


if __name__ == "__main__":
    typer.run(main)
