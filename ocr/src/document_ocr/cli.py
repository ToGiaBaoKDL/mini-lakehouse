"""Command-line boundary for the isolated OCR task image."""

import json
from enum import StrEnum
from typing import Annotated

import typer
from lakehouse.config.settings import get_settings
from lakehouse.logging import configure_logging
from loguru import logger

from document_ocr.application import RetryableOcrError, TerminalOcrError
from document_ocr.application.runtime import run_arxiv_ocr


class Provider(StrEnum):
    KAGGLE = "kaggle"
    MODAL = "modal"


def run(
    arxiv_id: Annotated[str, typer.Option(help="One exact curated ArXiv document ID.")],
    provider: Annotated[
        Provider,
        typer.Option(case_sensitive=False, help="Remote GPU execution provider."),
    ] = Provider.KAGGLE,
) -> None:
    """OCR one ArXiv PDF and publish its validated output."""
    settings = get_settings()
    configure_logging(settings.log_level)
    try:
        result = run_arxiv_ocr(arxiv_id, provider.value)
    except TerminalOcrError as error:
        logger.error("{}", error)
        raise typer.Exit(code=2) from error
    except RetryableOcrError as error:
        logger.error("{}", error)
        raise typer.Exit(code=1) from error
    typer.echo(json.dumps(result, separators=(",", ":"), sort_keys=True))


app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
)
app.command("run")(run)
