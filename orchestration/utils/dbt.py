"""Single Prefect/dbt invocation boundary used by analytics flows."""

from prefect_dbt import PrefectDbtRunner, PrefectDbtSettings

from mini_lakehouse.config import get_settings


def run_dbt_pipeline() -> None:
    settings = get_settings()
    runner = PrefectDbtRunner(
        settings=PrefectDbtSettings(
            project_dir=settings.dbt.project_dir,
            profiles_dir=settings.dbt.profiles_dir,
        )
    )
    runner.invoke(["source", "freshness"])
    runner.invoke(["build"])
