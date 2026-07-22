"""Single Prefect/dbt invocation boundary with strict node failure propagation."""

from typing import Any, Protocol

from prefect_dbt import PrefectDbtRunner, PrefectDbtSettings

from mini_lakehouse.config import get_settings


class DbtRunner(Protocol):
    def invoke(self, args: list[str]) -> Any: ...


GITHUB_SOURCE_SELECTOR = "github_sources"
ENGINEERING_MART_SELECTOR = "engineering_marts"
SERIAL_DBT_WRITE_THREADS = "1"


def _invoke(runner: DbtRunner, command: list[str]) -> None:
    result = runner.invoke(command)
    if getattr(result, "success", False) is not True:
        # PrefectDbtRunner normally raises with failed node details. Keep an explicit
        # boundary so a future runner/configuration cannot turn a failed dbt invocation
        # into a successful Prefect task.
        raise RuntimeError(f"dbt {' '.join(command)} completed without a successful result")


def run_dbt_pipeline(*, runner: DbtRunner | None = None) -> None:
    settings = get_settings()
    dbt_runner = runner or PrefectDbtRunner(
        settings=PrefectDbtSettings(
            project_dir=settings.dbt.project_dir,
            profiles_dir=settings.dbt.profiles_dir,
        ),
        raise_on_failure=True,
    )
    _invoke(dbt_runner, ["source", "freshness", "--selector", GITHUB_SOURCE_SELECTOR])
    _invoke(
        dbt_runner,
        ["test", "--selector", GITHUB_SOURCE_SELECTOR, "--indirect-selection", "cautious"],
    )
    _invoke(
        dbt_runner,
        ["run", "--selector", ENGINEERING_MART_SELECTOR, "--threads", SERIAL_DBT_WRITE_THREADS],
    )
    _invoke(dbt_runner, ["test", "--selector", ENGINEERING_MART_SELECTOR])
