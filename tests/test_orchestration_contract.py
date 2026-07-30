import ast
from pathlib import Path

ALLOWED_JOB_TYPES = {
    "etl",
    "rpt",
    "mon",
    "man",
    "bk",
    "stm",
    "cat",
    "gov",
    "test",
}
ALLOWED_WORKER_TYPES = {"emr", "glue", "k8spod", "afw", "mix"}


def _dag_files() -> list[Path]:
    return sorted(Path("orchestration/dags").rglob("*.py"))


def test_dags_are_domain_scoped_and_follow_worker_aware_naming() -> None:
    dag_files = _dag_files()
    assert {path.as_posix() for path in dag_files} == {
        "orchestration/dags/arxiv/etl_emr_arxiv_metadata.py",
        "orchestration/dags/github/etl_emr_github_archive.py",
    }
    assert not list(Path("orchestration").rglob("__init__.py"))
    assert not Path("orchestration/tasks.py").exists()

    for path in dag_files:
        job_type, worker_type, description = path.stem.split("_", maxsplit=2)
        assert job_type in ALLOWED_JOB_TYPES
        assert worker_type in ALLOWED_WORKER_TYPES
        assert description


def test_daily_source_dags_are_bounded_and_parameterized() -> None:
    github = Path("orchestration/dags/github/etl_emr_github_archive.py").read_text(encoding="utf-8")
    arxiv = Path("orchestration/dags/arxiv/etl_emr_arxiv_metadata.py").read_text(encoding="utf-8")

    assert 'schedule="0 8 * * *"' in github
    assert 'schedule="0 10 * * *"' in arxiv
    for source in (github, arxiv):
        assert "source_date" in source
        assert "catchup=False" in source
        assert "max_active_runs=1" in source
        assert "dag_failure_callbacks()" in source
        assert "dag_success_callbacks()" in source


def test_emr_operator_uses_official_deferrable_lifecycle() -> None:
    source = Path("orchestration/operators/emr.py").read_text(encoding="utf-8")
    templates = Path("orchestration/config/templates.py").read_text(encoding="utf-8")

    assert "EmrServerlessStartJobOperator" in source
    assert "deferrable=True" in source
    assert "cancel_on_kill=True" in source
    assert "enable_application_ui_links=True" in source
    assert "client_request_token=" in source
    assert "dag_run.run_after" in templates
    assert '"--archives"' in source
    assert "python.tar.gz#environment" in source
    assert '"--py-files"' not in source
    assert "task_failure_callbacks()" in source
    assert 'config={"executionTimeoutMinutes": EMR_JOB_TIMEOUT_MINUTES}' in source
    assert "execution_timeout=timedelta(" in source
    assert "boto3" not in source
    assert "sleep(" not in source
    assert "def emr_source_job(" in source
    assert "--landing-uri" in source
    assert "--contracts-uri" in source
    assert "--catalog-name" in source
    assert "lakehouse_source_arguments" not in templates


def test_emr_artifacts_are_built_in_the_pinned_runtime() -> None:
    dockerfile = Path("jobs/emr/Dockerfile").read_text(encoding="utf-8")

    assert "amazonlinux:2023-minimal@sha256:" in dockerfile
    assert "dnf install -y python3.11" in dockerfile
    assert ":latest" not in dockerfile
    assert "venv-pack" in dockerfile
    assert "python.tar.gz" in dockerfile

    makefile = Path("Makefile").read_text(encoding="utf-8")
    assert "jobs/emr/.venv/lib/python" not in makefile
    assert "--file jobs/emr/Dockerfile" in makefile
    assert "lakehouse_jobs.zip" not in makefile


def test_emr_artifact_sources_are_python_311_compatible() -> None:
    runtime_sources = (
        *Path("src/lakehouse_platform").rglob("*.py"),
        *Path("jobs/emr/src").rglob("*.py"),
        *Path("jobs/emr/entrypoints").glob("*.py"),
    )

    for path in runtime_sources:
        ast.parse(
            path.read_text(encoding="utf-8"),
            filename=str(path),
            feature_version=(3, 11),
        )


def test_emr_jobs_resolve_storage_and_tables_from_contract_bundle() -> None:
    contracts = Path("jobs/emr/src/lakehouse_jobs/common/contracts.py").read_text(encoding="utf-8")
    assert "DataContracts.model_validate_json" in contracts
    assert "StructType" in contracts

    for path in (
        Path("jobs/emr/entrypoints/github_archive.py"),
        Path("jobs/emr/entrypoints/arxiv_metadata.py"),
    ):
        source = path.read_text(encoding="utf-8")
        ast.parse(source)
        assert "from lakehouse_jobs." in source
        assert "typer.run(main)" in source
        assert "CREATE TABLE" not in source
        assert "CREATE DATABASE" not in source

    jobs = "\n".join(
        path.read_text(encoding="utf-8")
        for path in Path("jobs/emr/src/lakehouse_jobs").rglob("*.py")
    )
    assert "ContractBundle" not in jobs
    assert "ProductBinding" not in jobs
    assert "CREATE TABLE" not in jobs
    assert "CREATE DATABASE" not in jobs
