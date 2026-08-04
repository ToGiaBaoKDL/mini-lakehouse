import os
import re
from pathlib import Path

os.environ.setdefault("AIRFLOW_HOME", "/tmp/lakehouse-airflow-tests")
os.environ.setdefault("HOST_AWS_IDENTITY_DIR", "/tmp")
os.environ["LAKEHOUSE_ENVIRONMENT"] = "ci"

from airflow.models import DagBag
from airflow.providers.amazon.aws.operators.emr import EmrServerlessStartJobOperator
from airflow.providers.docker.operators.docker import DockerOperator
from airflow.sdk import DAG

ALLOWED_JOB_TYPES = {"etl", "tl", "rpt", "mon", "man", "bk", "stm", "cat", "gov", "test"}
ALLOWED_WORKER_TYPES = {"emr", "glue", "k8spod", "afw", "docker", "mix"}
EXPECTED_DAGS = {
    "etl_docker_arxiv_document_ocr",
    "tl_docker_engineering_analytics",
    "etl_emr_arxiv_metadata",
    "etl_emr_github_archive",
    "man_emr_iceberg_maintenance",
}


def _bag() -> DagBag:
    return DagBag(dag_folder=Path("orchestration/bundle/dags"))


def _dag(bag: DagBag, dag_id: str) -> DAG:
    return bag.dags[dag_id]


def test_dagbag_loads_every_owned_dag_without_import_errors() -> None:
    bag = _bag()

    assert bag.import_errors == {}
    assert set(bag.dags) == EXPECTED_DAGS


def test_dag_files_are_domain_scoped_and_follow_worker_aware_naming() -> None:
    files = sorted(Path("orchestration/bundle/dags").rglob("*.py"))
    assert {path.stem for path in files} == EXPECTED_DAGS
    assert all(path.parent != Path("orchestration/bundle/dags") for path in files)
    for path in files:
        job_type, worker_type, description = path.stem.split("_", maxsplit=2)
        assert job_type in ALLOWED_JOB_TYPES
        assert worker_type in ALLOWED_WORKER_TYPES
        assert description

    sources = "\n".join(path.read_text(encoding="utf-8") for path in files)
    assert "DockerOperator(" not in sources
    assert "EmrServerlessStartJobOperator(" not in sources
    assert "Asset(" not in sources


def test_source_dags_are_bounded_parameterized_deferrable_emr_jobs() -> None:
    bag = _bag()
    schedules = {
        "etl_emr_github_archive": "0 8 * * *",
        "etl_emr_arxiv_metadata": "0 10 * * *",
    }
    for dag_id, schedule in schedules.items():
        dag = _dag(bag, dag_id)
        assert dag.schedule == schedule
        assert dag.max_active_runs == 1
        assert "source_date" in dag.params
        task = dag.tasks[0]
        assert isinstance(task, EmrServerlessStartJobOperator)
        assert task.deferrable is True
        assert task.cancel_on_kill is True
        assert task.enable_application_ui_links is True
        assert task.retries == 2
        assert re.fullmatch(r"[A-Za-z0-9._-]{1,64}", task.client_request_token)
        arguments = task.job_driver["sparkSubmit"]["entryPointArguments"]
        assert "--landing-uri" in arguments
        assert "--contracts-uri" in arguments
        assert "--catalog-name" not in arguments

    arxiv = _dag(bag, "etl_emr_arxiv_metadata").tasks[0]
    assert arxiv.outlets[0].uri == "lakehouse://curated/arxiv/metadata"


def test_github_asset_triggers_freshness_before_the_dbt_build() -> None:
    bag = _bag()
    producer = _dag(bag, "etl_emr_github_archive")
    analytics = _dag(bag, "tl_docker_engineering_analytics")

    produced_asset = producer.tasks[0].outlets[0]
    assert produced_asset.uri == "lakehouse://curated/github"
    assert analytics.schedule == [produced_asset]

    freshness = analytics.get_task("check_source_freshness")
    build = analytics.get_task("build_analytics")
    assert isinstance(freshness, DockerOperator)
    assert isinstance(build, DockerOperator)
    assert freshness.image == build.image == "dbt-task:runtime"
    assert freshness.command == ["source", "freshness"]
    assert build.command == ["build"]
    assert freshness.downstream_task_ids == {"build_analytics"}
    assert freshness.retries == build.retries == 0
    assert freshness.environment["AWS_CONFIG_FILE"] == "/run/aws/config"
    assert freshness.mounts[0]["Source"] == "/tmp/dbt-transformer"
    assert freshness.inlets == [produced_asset]
    assert build.inlets == [produced_asset]
    assert build.outlets[0].uri == "lakehouse://analytics/engineering"


def test_manual_ocr_dag_processes_exactly_one_requested_document() -> None:
    dag = _dag(_bag(), "etl_docker_arxiv_document_ocr")
    assert dag.schedule is None
    assert dag.max_active_runs == 1
    assert set(dag.params) == {"arxiv_id", "provider"}

    task = dag.get_task("process_arxiv_pdf")
    assert isinstance(task, DockerOperator)
    assert task.image == "ocr-worker:runtime"
    assert task.command == [
        "--arxiv-id",
        "{{ params.arxiv_id }}",
        "--provider",
        "{{ params.provider }}",
    ]
    assert task.retries == 0
    assert task.environment["AWS_CONFIG_FILE"] == "/run/aws/config"
    assert task.mounts[0]["Source"] == "/tmp/ocr-worker"
    assert task.inlets[0].uri == "lakehouse://curated/arxiv/metadata"
    assert task.outlets[0].uri == "lakehouse://curated/arxiv/ocr"


def test_maintenance_dag_uses_the_shared_emr_lifecycle() -> None:
    dag = _dag(_bag(), "man_emr_iceberg_maintenance")
    assert dag.schedule == "0 3 * * 0"
    assert dag.max_active_runs == 1

    task = dag.get_task("maintain_contract_tables")
    assert isinstance(task, EmrServerlessStartJobOperator)
    assert task.deferrable is True
    assert task.job_driver["sparkSubmit"]["entryPoint"].endswith(
        "/entrypoints/iceberg_maintenance.py"
    )
    assert "--catalog-name" not in task.job_driver["sparkSubmit"]["entryPointArguments"]
