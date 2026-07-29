from pathlib import Path
from typing import Any

import yaml


def _compose(path: str) -> dict[str, Any]:
    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def test_compose_owns_only_self_hosted_application_services() -> None:
    files = sorted(path.name for path in Path.cwd().glob("compose*.yaml"))
    assert files == ["compose.airflow.yaml", "compose.document-inspector.yaml"]

    airflow = _compose("compose.airflow.yaml")["services"]
    assert set(airflow) == {
        "airflow-postgres",
        "airflow-init",
        "airflow-api-server",
        "airflow-scheduler",
        "airflow-dag-processor",
        "airflow-triggerer",
    }
    assert set(_compose("compose.document-inspector.yaml")["services"]) == {"document-inspector"}


def test_airflow_uses_local_executor_and_deferrable_runtime_components() -> None:
    payload = _compose("compose.airflow.yaml")
    common = payload["x-airflow-common"]
    environment = common["environment"]

    assert environment["AIRFLOW__CORE__EXECUTOR"] == "LocalExecutor"
    assert environment["AIRFLOW__CORE__LOAD_EXAMPLES"] == "false"
    assert environment["AIRFLOW__SECRETS__BACKEND"].endswith("AwsSecretsBackend")
    assert "variables_prefix" in environment["AIRFLOW__SECRETS__BACKEND_KWARGS"]
    assert payload["services"]["airflow-triggerer"]["command"] == "airflow triggerer"
    assert payload["services"]["airflow-dag-processor"]["command"] == "airflow dag-processor"
    assert payload["services"]["airflow-init"]["command"] == "airflow db migrate"
    assert "./orchestration:/opt/airflow/orchestration:ro" in common["volumes"]
    assert all("dist/" not in volume for volume in common["volumes"])


def test_compose_uses_aws_credential_chain_without_static_keys() -> None:
    rendered = "\n".join(
        Path(path).read_text(encoding="utf-8")
        for path in ("compose.airflow.yaml", "compose.document-inspector.yaml", ".env.example")
    )

    assert "AWS_ACCESS_KEY_ID" not in rendered
    assert "AWS_SECRET_ACCESS_KEY" not in rendered
    assert "AWS_PROFILE" in rendered
    assert "AWS_CONFIG_DIR" in rendered
    assert "LAKEHOUSE_POLARIS" not in rendered
    assert "LAKEHOUSE_TRINO" not in rendered
    assert "MINIO_" not in rendered


def test_all_container_images_are_immutable() -> None:
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")
    compose = Path("compose.airflow.yaml").read_text(encoding="utf-8")

    assert "ghcr.io/astral-sh/uv:0.11.30" in dockerfile
    assert "python:3.12.13-slim" in dockerfile
    assert "ARG AIRFLOW_VERSION=3.3.0" in dockerfile
    assert "apache/airflow:${AIRFLOW_VERSION}-python3.12" in dockerfile
    assert '"apache-airflow==${AIRFLOW_VERSION}"' in dockerfile
    assert "postgres:17.10" in compose
    assert ":latest" not in f"{dockerfile}\n{compose}"


def test_makefile_exposes_owned_operational_entrypoints() -> None:
    makefile = Path("Makefile").read_text(encoding="utf-8")
    for target in (
        "terraform-state-apply:",
        "terraform-plan:",
        "terraform-apply:",
        "airflow-up:",
        "airflow-down:",
        "document-inspector-up:",
        "document-inspector-down:",
        "catalog-apply:",
        "catalog-validate:",
        "emr-jobs-package:",
        "emr-jobs-publish:",
    ):
        assert target in makefile

    assert "compose.core.yaml" not in makefile
    assert "compose.prefect.yaml" not in makefile
