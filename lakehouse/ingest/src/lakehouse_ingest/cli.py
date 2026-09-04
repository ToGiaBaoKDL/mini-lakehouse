"""CLI for bounded external-source captures."""

from datetime import date
from typing import Annotated

import boto3
import typer

from lakehouse_ingest.arxiv import capture_day as capture_arxiv_day
from lakehouse_ingest.github_archive import capture_day
from lakehouse_ingest.storage import S3CaptureStore

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
)


@app.command("arxiv-oai")
def capture_arxiv_oai(
    source_date: Annotated[str, typer.Option(help="Complete OAI datestamp day.")],
    landing_uri: Annotated[str, typer.Option(help="Landing S3 root URI.")],
    max_pages: Annotated[int, typer.Option(min=1)] = 100,
) -> None:
    """Capture one complete ArXiv OAI datestamp day."""
    store = S3CaptureStore(boto3.client("s3"), landing_uri)
    typer.echo(capture_arxiv_day(store, date.fromisoformat(source_date), max_pages=max_pages))


@app.command("github-archive")
def capture_github_archive(
    source_date: Annotated[str, typer.Option(help="Complete UTC source day.")],
    landing_uri: Annotated[str, typer.Option(help="Landing S3 root URI.")],
    workers: Annotated[int, typer.Option(min=1, max=24)] = 8,
) -> None:
    """Capture one complete GitHub Archive UTC day."""
    store = S3CaptureStore(boto3.client("s3"), landing_uri)
    typer.echo(capture_day(store, date.fromisoformat(source_date), workers=workers))
