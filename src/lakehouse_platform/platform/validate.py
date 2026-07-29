"""Read-only validation for environment settings and declarative contracts."""

import json

import typer

from lakehouse_platform.config import get_settings
from lakehouse_platform.contracts import load_contracts
from lakehouse_platform.logging import configure_logging


def main() -> None:
    """Validate local configuration and YAML contracts without AWS I/O."""
    settings = get_settings()
    configure_logging(
        settings.log_level,
        json_logs=settings.environment == "production",
    )
    contracts = load_contracts(settings.contracts_dir)
    typer.echo(
        json.dumps(
            {
                "managed_namespaces": len(contracts.managed_namespaces()),
                "sources": len(contracts.sources),
                "curated_products": len(contracts.curated),
                "domains": len(contracts.domains),
                "managed_tables": sum(
                    len(owner.tables) for owner in (*contracts.sources, *contracts.curated)
                ),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    typer.run(main)
