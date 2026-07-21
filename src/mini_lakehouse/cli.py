import argparse
import json
from collections.abc import Sequence

from mini_lakehouse.config import get_settings
from mini_lakehouse.contracts import load_contracts
from mini_lakehouse.logging import configure_logging
from mini_lakehouse.platform.runtime import validate_runtime_contract
from mini_lakehouse.sources.github_archive.models import ArchiveHour
from mini_lakehouse.sources.github_archive.service import GithubArchiveIngestionService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lakehouse")
    subparsers = parser.add_subparsers(dest="command", required=True)
    ingest = subparsers.add_parser("ingest", help="Run a source-owned ingestion command")
    source_parsers = ingest.add_subparsers(dest="source", required=True)
    github_archive = source_parsers.add_parser(
        "github-archive",
        help="Ingest one GitHub Archive UTC hour",
    )
    github_archive.add_argument(
        "--hour",
        help="ISO-8601 UTC hour, for example 2025-01-01T00:00:00Z; defaults to the last hour",
    )
    subparsers.add_parser(
        "validate",
        help="Validate declarative contracts and runtime settings without side effects",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    settings = get_settings()
    configure_logging(settings.log_level)
    if args.command == "ingest" and args.source == "github-archive":
        result = GithubArchiveIngestionService(settings).ingest(ArchiveHour.parse(args.hour))
        print(json.dumps(result.model_dump(mode="json"), indent=2))
    elif args.command == "validate":
        contracts = load_contracts(settings.contracts_dir)
        validate_runtime_contract(settings, contracts)
        print(
            json.dumps(
                {
                    "catalog": contracts.catalog.catalog.name,
                    "catalog_role_grants": len(contracts.catalog.catalog_role_grants),
                    "namespaces": len(contracts.catalog.namespaces),
                    "sources": len(contracts.sources),
                    "domains": len(contracts.domains),
                    "policies": len(contracts.policies),
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
