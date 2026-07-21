"""Single Prefect/dbt invocation boundary used by ingestion flows."""

from prefect_dbt import PrefectDbtRunner, PrefectDbtSettings

from mini_lakehouse.config import get_settings


def run_dbt_pipeline(
    selector: str,
    *,
    freshness_selector: str | None = None,
) -> None:
    settings = get_settings()
    runner = PrefectDbtRunner(
        settings=PrefectDbtSettings(
            project_dir=settings.dbt.project_dir,
            profiles_dir=settings.dbt.profiles_dir,
        )
    )
    if freshness_selector is not None:
        runner.invoke(["source", "freshness", "--selector", freshness_selector])
    runner.invoke(["build", "--selector", selector])
