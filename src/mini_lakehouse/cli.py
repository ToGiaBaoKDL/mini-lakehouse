import argparse
import json
from collections.abc import Sequence

from mini_lakehouse.config import get_settings
from mini_lakehouse.github_archive.models import ArchiveHour
from mini_lakehouse.github_archive.service import GithubArchiveIngestionService
from mini_lakehouse.logging import configure_logging


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lakehouse")
    subparsers = parser.add_subparsers(dest="command", required=True)
    ingest = subparsers.add_parser("ingest", help="Ingest one GitHub Archive UTC hour")
    ingest.add_argument(
        "--hour",
        help="ISO-8601 UTC hour, for example 2025-01-01T00:00:00Z; defaults to the last hour",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    settings = get_settings()
    configure_logging(settings.log_level)
    if args.command == "ingest":
        result = GithubArchiveIngestionService(settings).ingest(ArchiveHour.parse(args.hour))
        print(json.dumps(result.model_dump(mode="json"), indent=2))


if __name__ == "__main__":
    main()
