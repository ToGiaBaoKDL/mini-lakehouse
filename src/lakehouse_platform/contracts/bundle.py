"""Build the validated contract artifact consumed by remote data jobs."""

from pathlib import Path
from typing import Annotated

import typer

from lakehouse_platform.contracts.loader import load_contracts


def main(
    output: Annotated[Path, typer.Option(dir_okay=False)],
    contracts: Annotated[Path, typer.Option(file_okay=False)] = Path("contracts"),
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        load_contracts(contracts).model_dump_json(indent=2),
        encoding="utf-8",
    )


if __name__ == "__main__":
    typer.run(main)
