"""Command-line boundary for the isolated OCR task image."""

import json
from typing import Annotated

import typer
from lakehouse.config.settings import get_settings
from lakehouse.logging import configure_logging
from loguru import logger

from document_ocr.arxiv.runtime import run_arxiv_ocr
from document_ocr.arxiv.workflow import OcrError


def run(
    arxiv_id: Annotated[str, typer.Option(help="One exact curated ArXiv document ID.")],
    pipeline: Annotated[
        str | None,
        typer.Option(help="YAML-configured pipeline; defaults to the configured pipeline."),
    ] = None,
) -> None:
    """Extract one ArXiv PDF and publish its validated output."""
    settings = get_settings()
    configure_logging(settings.log_level)
    try:
        result = run_arxiv_ocr(arxiv_id, pipeline)
    except (OcrError, ValueError) as error:
        logger.error("{}", error)
        raise typer.Exit(code=1) from error
    typer.echo(json.dumps(result, separators=(",", ":"), sort_keys=True))


app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
)
app.command("run")(run)
