from pathlib import Path
from typing import Any

import yaml


def _compose(path: str) -> dict[str, Any]:
    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def test_compose_owns_only_self_hosted_application_services() -> None:
    files = sorted(path.name for path in Path.cwd().glob("compose*.yaml"))
    assert files == ["compose.airflow.yaml", "compose.arxiv-inspector.yaml"]

    airflow = _compose("compose.airflow.yaml")["services"]
    assert set(airflow) == {
        "airflow-postgres",
        "airflow-init",
        "airflow-api-server",
        "airflow-scheduler",
        "airflow-dag-processor",
        "airflow-triggerer",
    }
    assert set(_compose("compose.arxiv-inspector.yaml")["services"]) == {"arxiv-inspector"}


def test_airflow_uses_local_executor_and_deferrable_runtime_components() -> None:
    payload = _compose("compose.airflow.yaml")
    common = payload["x-airflow-common"]
    environment = common["environment"]

    assert environment["AIRFLOW__CORE__EXECUTOR"] == "LocalExecutor"
    assert environment["AIRFLOW__CORE__LOAD_EXAMPLES"] == "false"
    assert common["image"] == "${AIRFLOW_IMAGE:-airflow:local}"
    assert common["user"] == "${LOCAL_UID}:0"
    assert "build" not in common
    assert environment["AIRFLOW__SECRETS__BACKEND"].endswith("AwsSecretsBackend")
    assert "variables_prefix" in environment["AIRFLOW__SECRETS__BACKEND_KWARGS"]
    assert "profile_name" not in environment["AIRFLOW__SECRETS__BACKEND_KWARGS"]
    assert payload["services"]["airflow-triggerer"]["command"] == "airflow triggerer"
    assert payload["services"]["airflow-dag-processor"]["command"] == "airflow dag-processor"
    assert payload["services"]["airflow-init"]["command"] == "airflow db migrate"
    assert all("./orchestration:" not in volume for volume in common["volumes"])
    assert all("dist/" not in volume for volume in common["volumes"])
    scheduler = payload["services"]["airflow-scheduler"]
    assert "/var/run/docker.sock:/var/run/docker.sock" in scheduler["volumes"]
    assert scheduler["group_add"] == ["${DOCKER_GID}"]
    assert environment["OCR_AWS_PROFILE"] == "${OCR_AWS_PROFILE}"
    assert environment["HOST_AWS_CONFIG_DIR"] == "${AWS_CONFIG_DIR}"
    assert "OCR_TASK_IMAGE" not in environment
    assert "DOCKER_TASK_USER" not in environment
    assert "AIRFLOW_CONN_AWS_DEFAULT" not in environment
    assert "AIRFLOW_BOOTSTRAP_VERSION" not in environment
    assert "AWS_REGION" not in environment


def test_airflow_bootstrap_secrets_are_service_scoped_files() -> None:
    payload = _compose("compose.airflow.yaml")
    services = payload["services"]
    environment = payload["x-airflow-common"]["environment"]

    assert "env_file" not in payload["x-airflow-common"]
    assert "env_file" not in services["airflow-init"]
    assert "AIRFLOW__DATABASE__SQL_ALCHEMY_CONN" not in environment
    assert "AIRFLOW__CORE__FERNET_KEY" not in environment
    assert "AIRFLOW__API_AUTH__JWT_SECRET" not in environment
    assert environment["AIRFLOW__DATABASE__SQL_ALCHEMY_CONN_CMD"].startswith("python -c")
    assert environment["AIRFLOW__CORE__FERNET_KEY_CMD"].startswith("cat /run/secrets/")
    assert environment["AIRFLOW__API_AUTH__JWT_SECRET_CMD"].startswith("cat /run/secrets/")
    assert services["airflow-postgres"]["environment"]["POSTGRES_PASSWORD_FILE"] == (
        "/run/secrets/airflow_db_password"
    )
    assert set(payload["secrets"]) == {
        "airflow_db_password",
        "airflow_fernet_key",
        "airflow_jwt_secret",
    }
    assert all("environment" in secret for secret in payload["secrets"].values())


def test_airflow_runtime_components_have_role_appropriate_healthchecks() -> None:
    services = _compose("compose.airflow.yaml")["services"]

    assert "/api/v2/version" in services["airflow-api-server"]["healthcheck"]["test"][-1]
    assert "SchedulerJob" in services["airflow-scheduler"]["healthcheck"]["test"][-1]
    assert "DagProcessorJob" in services["airflow-dag-processor"]["healthcheck"]["test"][-1]
    assert "TriggererJob" in services["airflow-triggerer"]["healthcheck"]["test"][-1]
    assert "healthcheck" in services["airflow-postgres"]
    assert "healthcheck" not in services["airflow-init"]


def test_compose_uses_aws_credential_chain_without_static_keys() -> None:
    rendered = "\n".join(
        Path(path).read_text(encoding="utf-8")
        for path in ("compose.airflow.yaml", "compose.arxiv-inspector.yaml")
    )

    assert "AWS_ACCESS_KEY_ID" not in rendered
    assert "AWS_SECRET_ACCESS_KEY" not in rendered
    assert "AWS_PROFILE" in rendered
    assert "AWS_CONFIG_DIR" in rendered


def test_local_runtime_does_not_use_dotenv_files() -> None:
    assert not Path(".env").exists()
    assert not Path(".env.example").exists()

    makefile = Path("Makefile").read_text(encoding="utf-8")
    settings = Path("platform/src/lakehouse/config/settings.py").read_text(encoding="utf-8")
    ocr_settings = Path("ocr/src/document_ocr/settings.py").read_text(encoding="utf-8")

    assert "make setup" not in makefile
    assert 'env_file=".env"' not in settings
    assert 'env_file=".env"' not in ocr_settings


def test_arxiv_inspector_receives_only_its_explicit_environment() -> None:
    service = _compose("compose.arxiv-inspector.yaml")["services"]["arxiv-inspector"]

    assert "env_file" not in service
    assert "build" not in service
    assert service["image"] == ("${ARXIV_INSPECTOR_IMAGE:-arxiv-inspector:local}")
    assert service["user"] == "${LOCAL_UID}:0"
    assert set(service["environment"]) == {"LAKEHOUSE_ENVIRONMENT", "AWS_PROFILE"}
    assert service["volumes"] == ["${AWS_CONFIG_DIR}:/tmp/.aws:ro"]


def test_all_container_images_are_immutable() -> None:
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")
    emr_dockerfile = Path("jobs/emr/Dockerfile").read_text(encoding="utf-8")
    compose = Path("compose.airflow.yaml").read_text(encoding="utf-8")

    assert "ghcr.io/astral-sh/uv:0.11.30@sha256:" in dockerfile
    assert "python:3.12.13-slim@sha256:" in dockerfile
    assert "apache/airflow:3.3.0-python3.12@sha256:" in dockerfile
    assert "FROM base AS airflow-requirements" not in dockerfile
    assert "COPY --chown=airflow:0 orchestration /opt/airflow/orchestration" in dockerfile
    assert "FROM ocr-worker-dependencies AS ocr-worker" in dockerfile
    assert 'ENTRYPOINT ["document-ocr"]' in dockerfile
    assert '"apache-airflow==3.3.0"' in Path("orchestration/pyproject.toml").read_text(
        encoding="utf-8"
    )
    assert "postgres:17.10@sha256:" in compose
    assert "public.ecr.aws/amazonlinux/amazonlinux:2023-minimal@sha256:" in emr_dockerfile
    assert "ghcr.io/astral-sh/uv:0.11.30@sha256:" in emr_dockerfile
    assert ":latest" not in f"{dockerfile}\n{compose}"
    assert "apps/arxiv_inspector/.streamlit ./.streamlit" in dockerfile
    assert "USER inspector" in dockerfile
    assert "USER worker" in dockerfile


def test_python_dependencies_are_owned_by_their_runtime_domain() -> None:
    workspace = Path("pyproject.toml").read_text(encoding="utf-8")
    platform = Path("platform/pyproject.toml").read_text(encoding="utf-8")
    orchestration = Path("orchestration/pyproject.toml").read_text(encoding="utf-8")
    inspector = Path("apps/arxiv_inspector/pyproject.toml").read_text(encoding="utf-8")
    analytics = Path("dbt/analytics/pyproject.toml").read_text(encoding="utf-8")
    ocr = Path("ocr/pyproject.toml").read_text(encoding="utf-8")

    assert "apache-airflow" not in workspace
    assert "dbt-athena" not in workspace
    assert "streamlit" not in workspace
    assert 'name = "lakehouse"' in platform
    assert '"apache-airflow==3.3.0"' in orchestration
    assert '"apache-airflow-providers-amazon[aiobotocore]==9.31.0"' in orchestration
    assert '"apache-airflow-providers-docker==4.5.7"' in orchestration
    assert '"apache-airflow-providers-smtp==3.0.1"' in orchestration
    assert "constraint-dependencies" not in orchestration
    assert "document-ocr" not in orchestration
    assert '"lakehouse"' not in orchestration
    assert '"streamlit>=1.60,<1.61"' in inspector
    for dependency in (
        "awswrangler",
        "boto3",
        "document-ocr",
        "lakehouse",
        "loguru",
        "pandas",
        "pydantic",
        "streamlit",
    ):
        assert dependency in inspector
    assert "kaggle-publish" in ocr
    assert "worker = [" in ocr
    assert "providers = [" not in ocr
    assert "s3fs" not in ocr
    assert '"dbt-athena==1.11.0"' in analytics
    assert 'members = ["apps/arxiv_inspector", "dbt/analytics", "ocr", "platform"]' in workspace
    assert 'exclude = ["jobs/emr", "ocr/runners/glm_ocr", "orchestration"]' in workspace
    assert Path("orchestration/uv.lock").is_file()
    assert "apache-airflow" not in Path("uv.lock").read_text(encoding="utf-8")
    assert "opentelemetry" not in Path("uv.lock").read_text(encoding="utf-8")
    assert "apache-airflow" in Path("orchestration/uv.lock").read_text(encoding="utf-8")
    assert "opentelemetry" in Path("orchestration/uv.lock").read_text(encoding="utf-8")


def test_platform_control_plane_is_one_owned_workspace_package() -> None:
    assert Path("platform/pyproject.toml").is_file()
    assert Path("platform/contracts").is_dir()
    assert Path("platform/src/lakehouse/catalog").is_dir()
    assert not Path("src").exists()
    assert not Path("contracts").exists()

    sources = "\n".join(
        path.read_text(encoding="utf-8") for path in Path("platform/src/lakehouse").rglob("*.py")
    )
    assert "lakehouse.platform" not in sources


def test_makefile_exposes_owned_operational_entrypoints() -> None:
    makefile = Path("Makefile").read_text(encoding="utf-8")
    for target in (
        "terraform-state-apply:",
        "terraform-plan:",
        "terraform-apply:",
        "airflow-up:",
        "airflow-down:",
        "arxiv-inspector-up:",
        "arxiv-inspector-down:",
        "services-up:",
        "services-down:",
        "services-ps:",
        "catalog-apply:",
        "catalog-validate:",
        "airflow-bootstrap-init:",
        "ecr-login:",
        "ecr-publish:",
        "ecr-deploy:",
        "ocr-worker-build:",
        "emr-jobs-package:",
        "emr-jobs-publish:",
        "ocr-kaggle-runner-publish:",
    ):
        assert target in makefile

    assert "compose.core.yaml" not in makefile
    assert "docker push" in makefile
    assert "ecr describe-images" in makefile
    assert "already published" in makefile
    assert 'imageTag="$(RELEASE)"' in makefile
    assert "ocr-worker:runtime" in makefile
    assert "AIRFLOW_PARALLELISM ?=" not in makefile
    assert "AIRFLOW_USERS ?=" not in makefile
    assert "PROJECT_NAME ?=" not in makefile
    assert "AWS_REGION ?=" not in makefile
    assert "OCR_TASK_IMAGE" not in makefile
    assert "up -d --build" not in makefile
