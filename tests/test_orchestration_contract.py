import ast
from pathlib import Path
from typing import cast

import yaml

ORCHESTRATION_DIR = Path("orchestration")
FLOWS_DIR = ORCHESTRATION_DIR / "flows"
ALLOWED_JOB_TYPES = {"etl", "el", "tl", "rpt", "mon", "bk", "gov", "test"}


def _dag_files() -> list[Path]:
    return sorted(path for path in FLOWS_DIR.glob("*.py") if not path.name.startswith("_"))


def test_each_dag_lives_in_flows_and_uses_the_job_type_convention() -> None:
    dag_files = _dag_files()

    assert dag_files
    assert not list(ORCHESTRATION_DIR.glob("*.py"))
    assert not list(FLOWS_DIR.glob("_*.py"))
    assert not (ORCHESTRATION_DIR / "__init__.py").exists()
    assert not (ORCHESTRATION_DIR / "tasks.py").exists()

    for dag_file in dag_files:
        job_type, separator, description = dag_file.stem.partition("_")
        assert separator and description
        assert job_type in ALLOWED_JOB_TYPES

    assert not (FLOWS_DIR / "__init__.py").exists()
    assert not (ORCHESTRATION_DIR / "utils" / "__init__.py").exists()
    assert not (ORCHESTRATION_DIR / "plugins" / "__init__.py").exists()


def test_prefect_entrypoints_reference_convention_named_dags() -> None:
    prefect_config = Path("prefect.yaml").read_text(encoding="utf-8")

    for dag_file in _dag_files():
        expected_entrypoint = f"entrypoint: {dag_file}:{dag_file.stem}"
        assert expected_entrypoint in prefect_config


def test_prefect_deployments_reuse_declared_work_pool_and_concurrency_contracts() -> None:
    payload = yaml.safe_load(Path("prefect.yaml").read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    config = cast(dict[str, object], payload)
    definitions = cast(dict[str, object], config["definitions"])
    deployments = cast(list[dict[str, object]], config["deployments"])

    assert {deployment["name"] for deployment in deployments} == {
        "el_github_archive",
        "tl_github_analytics",
        "gov_iceberg_maintenance",
    }
    assert set(cast(dict[str, object], definitions["work_pools"])) == {
        "ingestion",
        "transformation",
        "maintenance",
    }
    assert all(
        deployment["concurrency_limit"] == {"limit": 1, "collision_strategy": "ENQUEUE"}
        for deployment in deployments
    )
    ingestion = next(
        deployment for deployment in deployments if deployment["name"] == "el_github_archive"
    )
    transformation = next(
        deployment for deployment in deployments if deployment["name"] == "tl_github_analytics"
    )
    assert ingestion["parameters"] == {"archive_hour": None}
    assert transformation["parameters"] == {"archive_hour": None}
    assert all("triggers" not in deployment for deployment in deployments)
    assert ingestion["schedules"] == [{"cron": "15 * * * *", "timezone": "UTC", "active": True}]
    assert transformation["schedules"] == [
        {"cron": "30 * * * *", "timezone": "UTC", "active": True}
    ]


def test_scheduled_dbt_pipeline_runs_freshness_then_the_full_project() -> None:
    source = (ORCHESTRATION_DIR / "utils" / "dbt.py").read_text(encoding="utf-8")
    assert '_invoke(dbt_runner, ["source", "freshness"])' in source
    assert '_invoke(dbt_runner, ["build"])' in source
    assert '"--selector"' not in source
    assert '"--select"' not in source


def test_scheduled_archive_hour_uses_prefect_scheduled_start_time() -> None:
    source = (ORCHESTRATION_DIR / "utils" / "scheduling.py").read_text(encoding="utf-8")

    assert "flow_run.scheduled_start_time" in source
    assert "datetime.now" not in source


def test_all_reusable_prefect_tasks_and_flows_have_failure_hooks() -> None:
    for path in FLOWS_DIR.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        module = ast.parse(source)
        decorators = [
            decorator
            for node in module.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            for decorator in node.decorator_list
            if isinstance(decorator, ast.Call)
            and isinstance(decorator.func, ast.Name)
            and decorator.func.id in {"flow", "task"}
        ]
        assert decorators
        for decorator in decorators:
            keywords = {keyword.arg for keyword in decorator.keywords}
            assert "on_failure" in keywords


def test_every_dag_flow_has_lifecycle_notification_hooks() -> None:
    for path in _dag_files():
        module = ast.parse(path.read_text(encoding="utf-8"))
        flow_decorators = [
            decorator
            for node in module.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            for decorator in node.decorator_list
            if isinstance(decorator, ast.Call)
            and isinstance(decorator.func, ast.Name)
            and decorator.func.id == "flow"
        ]
        assert len(flow_decorators) == 1
        keywords = {keyword.arg for keyword in flow_decorators[0].keywords}
        assert {
            "on_running",
            "on_completion",
            "on_failure",
            "on_cancellation",
            "on_crashed",
        } <= keywords
