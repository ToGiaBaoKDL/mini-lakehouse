"""Explicit Glue/Iceberg table contract operations."""

import json
from collections.abc import Sequence

import typer
from loguru import logger

from lakehouse_platform.aws import get_runtime_parameter
from lakehouse_platform.config import get_settings
from lakehouse_platform.contracts import load_contracts
from lakehouse_platform.logging import configure_logging
from lakehouse_platform.platform.catalog.tables import (
    apply_table_contracts,
    validate_table_contracts,
)
from lakehouse_platform.platform.iceberg import load_iceberg_catalog

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Apply or validate Glue/Iceberg table contracts.",
)


def require_valid(errors: tuple[str, ...]) -> None:
    if errors:
        raise RuntimeError("Table contract validation failed:\n- " + "\n- ".join(errors))


def _run(*, apply: bool) -> None:
    settings = get_settings()
    configure_logging(
        settings.log_level,
        json_logs=settings.environment == "production",
    )
    contracts = load_contracts(settings.contracts_dir)

    def parameter(key: str) -> str:
        return get_runtime_parameter(settings.environment, key)

    landing_uri = parameter("storage/landing_uri")
    curated_uri = parameter("storage/curated_uri")
    catalog = load_iceberg_catalog(
        parameter("catalog/name"),
        region_name=settings.aws_region,
        profile_name=settings.aws_profile,
    )
    if apply:
        logger.info("Applying Glue/Iceberg contracts")
        apply_table_contracts(
            catalog,
            contracts,
            landing_uri=landing_uri,
            curated_uri=curated_uri,
        )
    errors = validate_table_contracts(
        catalog,
        contracts,
        landing_uri=landing_uri,
        curated_uri=curated_uri,
    )
    require_valid(errors)
    typer.echo(json.dumps({"valid": True, "errors": []}, indent=2))


@app.command("apply")
def apply_contracts() -> None:
    """Create missing objects and apply safe table-property updates."""
    _run(apply=True)


@app.command("validate")
def validate_contracts() -> None:
    """Detect drift without mutating Glue or Iceberg state."""
    _run(apply=False)


def main(arguments: Sequence[str] | None = None) -> None:
    app(args=list(arguments) if arguments is not None else None, prog_name="lakehouse-catalog")


if __name__ == "__main__":
    main()
