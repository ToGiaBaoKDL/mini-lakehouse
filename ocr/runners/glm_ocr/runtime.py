"""CLI entry point for the provider-neutral GLM-OCR runner."""

from pathlib import Path
from typing import Annotated

import typer
from document_ocr.protocol import OcrJob
from runner.job import run


def main(
    job: Annotated[Path, typer.Option(exists=True, dir_okay=False, readable=True)],
    output_directory: Annotated[Path, typer.Option(file_okay=False)],
    model_path: Annotated[Path, typer.Option(exists=True, file_okay=False, readable=True)],
    layout_model_path: Annotated[
        Path,
        typer.Option(exists=True, file_okay=False, readable=True),
    ],
) -> None:
    run(
        OcrJob.model_validate_json(job.read_bytes()),
        output_directory,
        model_path=model_path,
        layout_model_path=layout_model_path,
    )


if __name__ == "__main__":
    typer.run(main)
