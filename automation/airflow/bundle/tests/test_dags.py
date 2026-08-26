import os
import re
import warnings
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import Mock, PropertyMock, patch
from urllib.parse import ParseResult, urlparse

import pytest

os.environ.setdefault("AIRFLOW_HOME", "/tmp/lakehouse-airflow-tests")
os.environ.setdefault("HOST_AWS_IDENTITY_DIR", "/tmp")
os.environ["LAKEHOUSE_ENVIRONMENT"] = "ci"

from airflow.models import DagBag
from airflow.providers.standard.operators.python import ShortCircuitOperator
from airflow.sdk import DAG, Asset
from airflow.utils.file import list_py_file_paths
from airflow.utils.types import DagRunType
from jinja2 import StrictUndefined
from jinja2.sandbox import SandboxedEnvironment
from operators import emr as emr_module
from operators.docker import LoggedDockerOperator
from operators.emr import LoggedEmrServerlessStartJobOperator

ALLOWED_JOB_TYPES = {"etl", "tl", "rpt", "mon", "man", "bk", "stm", "cat", "gov", "test"}
ALLOWED_WORKER_TYPES = {"emr", "glue", "k8spod", "afw", "docker", "mix"}
EXPECTED_DAGS = {
    "tl_docker_analytics",
    "etl_emr_arxiv_metadata",
    "etl_emr_github_archive",
    "man_emr_iceberg_maintenance",
}


def _bag() -> DagBag:
    return DagBag(dag_folder=Path("automation/airflow/bundle/dags"))


def _dag(bag: DagBag, dag_id: str) -> DAG:
    return bag.dags[dag_id]


def test_dagbag_loads_every_owned_dag_without_import_errors() -> None:
    bag = _bag()

    assert bag.import_errors == {}
    assert set(bag.dags) == EXPECTED_DAGS


def test_runtime_bundle_discovery_excludes_test_modules() -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        discovered = list_py_file_paths("automation/airflow/bundle", safe_mode=False)

    assert discovered
    assert all("/tests/" not in path for path in discovered)


def test_dag_files_are_domain_scoped_and_follow_worker_aware_naming() -> None:
    files = sorted(Path("automation/airflow/bundle/dags").rglob("*.py"))
    assert {path.stem for path in files} == EXPECTED_DAGS
    assert all(path.parent != Path("automation/airflow/bundle/dags") for path in files)
    for path in files:
        job_type, worker_type, description = path.stem.split("_", maxsplit=2)
        assert job_type in ALLOWED_JOB_TYPES
        assert worker_type in ALLOWED_WORKER_TYPES
        assert description

    sources = "\n".join(path.read_text(encoding="utf-8") for path in files)
    assert "DockerOperator(" not in sources
    assert "EmrServerlessStartJobOperator(" not in sources
    assert "Asset(" not in sources


def test_source_dags_are_bounded_parameterized_emr_jobs() -> None:
    bag = _bag()
    schedules = {
        "etl_emr_github_archive": "30 7 * * *",
        "etl_emr_arxiv_metadata": "0 11 * * *",
    }
    for dag_id, schedule in schedules.items():
        dag = _dag(bag, dag_id)
        assert dag.schedule == schedule
        assert dag.max_active_runs == 1
        assert "source_date" in dag.params
        task = dag.tasks[0]
        assert isinstance(task, LoggedEmrServerlessStartJobOperator)
        assert task.wait_for_completion is True
        assert task.deferrable is False
        assert task.cancel_on_kill is True
        assert task.enable_application_ui_links is True
        assert task.retries == 1
        assert task.retry_delay == timedelta(minutes=10)
        assert re.fullmatch(r"[A-Za-z0-9._-]{1,64}", task.client_request_token)
        arguments = task.job_driver["sparkSubmit"]["entryPointArguments"]
        assert "--landing-uri" in arguments
        assert "--contracts-uri" in arguments
        assert "--catalog-name" not in arguments
        submit_parameters = task.job_driver["sparkSubmit"]["sparkSubmitParameters"]
        rendered_submit_parameters = (
            SandboxedEnvironment(undefined=StrictUndefined)
            .from_string(submit_parameters)
            .render(var=SimpleNamespace(value={"emr/code_uri": "s3://artifacts/emr/jobs/release"}))
        )
        assert "--archives s3://artifacts/emr/jobs/release/python.tar.gz#environment" in (
            rendered_submit_parameters
        )
        assert "'s3://" not in rendered_submit_parameters
        assert "spark.executor.instances=1" in submit_parameters
        assert "spark.dynamicAllocation.minExecutors=0" in submit_parameters
        assert "spark.dynamicAllocation.initialExecutors=1" in submit_parameters

    arxiv = _dag(bag, "etl_emr_arxiv_metadata").tasks[0]
    assert arxiv.outlets[0].uri == "lakehouse://curated/arxiv/metadata"


def test_curated_assets_schedule_one_domain_aware_analytics_dag() -> None:
    bag = _bag()
    github_producer = _dag(bag, "etl_emr_github_archive")
    arxiv_producer = _dag(bag, "etl_emr_arxiv_metadata")
    analytics = _dag(bag, "tl_docker_analytics")

    github_asset = github_producer.tasks[0].outlets[0]
    arxiv_asset = arxiv_producer.tasks[0].outlets[0]
    assert github_asset.uri == "lakehouse://curated/github"
    assert arxiv_asset.uri == "lakehouse://curated/arxiv/metadata"
    assert analytics.schedule == github_asset | arxiv_asset

    expectations = {
        "engineering": {
            "image": "dbt:runtime",
            "identity": "/tmp/dbt-engineering",
            "selector": "engineering",
            "inputs": [github_asset],
            "output": "lakehouse://analytics/engineering",
        },
        "research": {
            "image": "dbt:runtime",
            "identity": "/tmp/dbt-research",
            "selector": "research",
            "inputs": [arxiv_asset],
            "output": "lakehouse://analytics/research",
        },
    }
    for domain, expected in expectations.items():
        inputs = cast(list[Asset], expected["inputs"])
        selected = analytics.get_task(f"{domain}.should_run")
        freshness = analytics.get_task(f"{domain}.check_source_freshness")
        build = analytics.get_task(f"{domain}.build_analytics")
        assert isinstance(selected, ShortCircuitOperator)
        assert isinstance(freshness, LoggedDockerOperator)
        assert isinstance(build, LoggedDockerOperator)
        assert freshness.image == build.image == expected["image"]
        assert freshness.command == [
            "source",
            "freshness",
            "--selector",
            expected["selector"],
        ]
        assert build.command == ["build", "--selector", expected["selector"]]
        assert freshness.environment["DBT_DOMAIN"] == expected["selector"]
        assert freshness.environment["DBT_SCHEMA"] == f"analytics_{expected['selector']}"
        assert selected.op_kwargs == {"asset_uris": tuple(asset.uri for asset in inputs)}
        assert selected.downstream_task_ids == {f"{domain}.check_source_freshness"}
        assert freshness.downstream_task_ids == {f"{domain}.build_analytics"}
        assert freshness.retries == build.retries == 0
        assert freshness.environment["AWS_CONFIG_FILE"] == "/run/aws/config"
        assert freshness.mounts[0]["Source"] == expected["identity"]
        assert freshness.inlets == build.inlets == inputs
        assert build.outlets[0].uri == expected["output"]


def test_analytics_domain_gates_run_both_manually_and_only_affected_assets() -> None:
    bag = _bag()
    analytics = _dag(bag, "tl_docker_analytics")
    engineering = analytics.get_task("engineering.should_run")
    research = analytics.get_task("research.should_run")
    github_asset = _dag(bag, "etl_emr_github_archive").tasks[0].outlets[0]

    assert isinstance(engineering, ShortCircuitOperator)
    assert isinstance(research, ShortCircuitOperator)
    manual_run = SimpleNamespace(run_type=DagRunType.MANUAL)
    for gate in (engineering, research):
        assert (
            gate.python_callable(
                **gate.op_kwargs,
                dag_run=manual_run,
                triggering_asset_events={},
            )
            is True
        )

    asset_run = SimpleNamespace(run_type=DagRunType.ASSET_TRIGGERED)
    triggering_events = {github_asset: [object()]}
    assert (
        engineering.python_callable(
            **engineering.op_kwargs,
            dag_run=asset_run,
            triggering_asset_events=triggering_events,
        )
        is True
    )
    assert (
        research.python_callable(
            **research.op_kwargs,
            dag_run=asset_run,
            triggering_asset_events=triggering_events,
        )
        is False
    )


def test_dags_share_timezone_and_expose_job_and_worker_tags() -> None:
    bag = _bag()

    for dag_id in EXPECTED_DAGS:
        dag = _dag(bag, dag_id)
        job_type, worker_type, _ = dag_id.split("_", maxsplit=2)
        assert dag.timezone.name == "Asia/Ho_Chi_Minh"
        assert job_type in dag.tags
        assert worker_type in dag.tags


def test_maintenance_dag_uses_the_shared_emr_lifecycle() -> None:
    dag = _dag(_bag(), "man_emr_iceberg_maintenance")
    assert dag.schedule == "0 3 * * 0"
    assert dag.max_active_runs == 1

    task = dag.get_task("maintain_contract_tables")
    assert isinstance(task, LoggedEmrServerlessStartJobOperator)
    assert task.wait_for_completion is True
    assert task.deferrable is False
    assert task.job_driver["sparkSubmit"]["entryPoint"].endswith(
        "/entrypoints/iceberg_maintenance.py"
    )
    assert "--catalog-name" not in task.job_driver["sparkSubmit"]["entryPointArguments"]


def test_emr_operator_logs_an_ephemeral_driver_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = _dag(_bag(), "man_emr_iceberg_maintenance").tasks[0]
    assert isinstance(task, LoggedEmrServerlessStartJobOperator)
    task.job_id = "job-123"
    logger = Mock()

    def dashboard_url(**_: object) -> ParseResult:
        return urlparse("https://dashboard.example/?authToken=temporary")

    monkeypatch.setattr(emr_module, "get_serverless_dashboard_url", dashboard_url)

    with (
        patch.object(
            LoggedEmrServerlessStartJobOperator,
            "hook",
            new_callable=PropertyMock,
            return_value=SimpleNamespace(conn=object()),
        ),
        patch.object(
            LoggedEmrServerlessStartJobOperator,
            "log",
            new_callable=PropertyMock,
            return_value=logger,
        ),
    ):
        task._log_driver_stdout_url()  # pyright: ignore[reportPrivateUsage]

    logger.info.assert_called_once_with(
        "Spark driver stdout (single-use URL, expires in one hour): %s",
        "https://dashboard.example/logs/SPARK_DRIVER/stdout.gz?authToken=temporary",
    )
