from collections.abc import Iterator
from pathlib import Path
from typing import Any

import yaml

PINNED_SERVICE_IMAGES = {
    "postgres:18.4",
    "quay.io/minio/aistor/minio:RELEASE.2026-06-06T02-44-06Z",
    "quay.io/minio/aistor/mc:RELEASE.2026-04-21T04-26-49Z",
    "apache/polaris-admin-tool:1.6.0",
    "apache/polaris:1.6.0",
    "trinodb/trino:483",
    "redis:8.8.0",
    "prefecthq/prefect:3.7.8-python3.12",
}


def _env_files(value: Any) -> Iterator[str]:
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "env_file" and isinstance(child, list):
                yield from (item for item in child if isinstance(item, str))
            yield from _env_files(child)
    elif isinstance(value, list):
        for child in value:
            yield from _env_files(child)


def test_compose_modules_are_clean_clone_safe() -> None:
    compose_files = sorted(Path.cwd().glob("compose*.yaml"))

    assert [path.name for path in compose_files] == [
        "compose.core.yaml",
        "compose.prefect.yaml",
    ]
    for compose_file in compose_files:
        payload = yaml.safe_load(compose_file.read_text(encoding="utf-8"))
        for relative_path in _env_files(payload):
            assert "infra/env/" not in relative_path
            assert (compose_file.parent / relative_path).is_file(), (
                compose_file,
                relative_path,
            )


def test_tracked_container_environment_contains_routing_but_no_secrets() -> None:
    environment = Path("infra/config/lakehouse.container.env").read_text(encoding="utf-8")

    assert "LAKEHOUSE_POLARIS__URI=http://polaris:8181/api/catalog" in environment
    assert "LAKEHOUSE_TRINO__HOST=trino" in environment
    assert "ACCESS_KEY=" not in environment
    assert "SECRET_KEY=" not in environment
    assert "CREDENTIAL=" not in environment


def test_local_env_template_contains_complete_storage_runtime_defaults() -> None:
    environment = Path(".env.example").read_text(encoding="utf-8")

    for expected in (
        "LAKEHOUSE_STORAGE__BACKEND=s3",
        "LAKEHOUSE_STORAGE__ENDPOINT_URL=http://localhost:9000",
        "LAKEHOUSE_STORAGE__PATH_STYLE_ACCESS=true",
        "LAKEHOUSE_STORAGE__ICEBERG_ACCESS_DELEGATION=none",
        "LAKEHOUSE_STORAGE__STS_UNAVAILABLE=true",
        "LAKEHOUSE_STORAGE__KMS_UNAVAILABLE=true",
        "LAKEHOUSE_STORAGE__LANDING_URI=s3://landing",
        "LAKEHOUSE_STORAGE__CURATED_URI=s3://curated",
        "LAKEHOUSE_STORAGE__ANALYTICS_URI=s3://analytics",
    ):
        assert expected in environment


def test_docs_do_not_reference_removed_dashboard_stack() -> None:
    docs = [Path("README.md"), *sorted(Path("docs").glob("*.md"))]

    for path in docs:
        content = path.read_text(encoding="utf-8")
        assert "compose.dashboard.yaml" not in content
        assert "Streamlit" not in content


def test_external_service_images_are_version_pinned() -> None:
    images: set[str] = set()
    for path in sorted(Path.cwd().glob("compose*.yaml")):
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        for service in payload.get("services", {}).values():
            image = service.get("image")
            if isinstance(image, str) and not image.startswith("mini-lakehouse"):
                images.add(image)

    assert images == PINNED_SERVICE_IMAGES
    assert all(not image.endswith(":latest") for image in images)


def test_each_local_image_has_one_compose_build_owner() -> None:
    build_owners: dict[str, list[str]] = {}
    for path in sorted(Path.cwd().glob("compose*.yaml")):
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        for service_name, service in payload.get("services", {}).items():
            image = service.get("image")
            if isinstance(image, str) and "build" in service:
                build_owners.setdefault(image, []).append(f"{path.name}:{service_name}")

    assert build_owners == {
        "mini-lakehouse:local": ["compose.core.yaml:lakehouse-bootstrap"],
        "mini-lakehouse-orchestration:local": ["compose.prefect.yaml:prefect-worker"],
    }


def test_application_base_images_are_version_pinned() -> None:
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")

    assert "FROM ghcr.io/astral-sh/uv:0.11.30 AS uv" in dockerfile
    assert "FROM python:3.13.14-slim AS base" in dockerfile
    assert ":latest" not in dockerfile


def test_aistor_license_and_data_are_mounted_at_canonical_paths() -> None:
    payload = yaml.safe_load(Path("compose.core.yaml").read_text(encoding="utf-8"))
    object_store = payload["services"]["object-store"]
    bootstrap = payload["services"]["object-store-bootstrap"]

    assert "./minio.license:/minio.license:ro" in object_store["volumes"]
    assert "object-store-data:/mnt/data" in object_store["volumes"]
    assert object_store["command"] == [
        "minio",
        "server",
        "/mnt/data",
        "--console-address",
        ":9001",
        "--license",
        "/minio.license",
    ]
    assert "mc license info local >/dev/null" in bootstrap["command"][0]


def test_makefile_is_the_local_operations_entrypoint() -> None:
    makefile = Path("Makefile").read_text(encoding="utf-8")

    for target in (
        "setup:",
        "preflight:",
        "config:",
        "check:",
        "pull:",
        "build:",
        "build-core:",
        "build-orchestration:",
        "start-core:",
        "start:",
        "up-core:",
        "up:",
        "down:",
        "clean:",
        "reset:",
        "ps:",
        "ps-all:",
        "logs:",
        "logs-follow:",
        "smoke:",
        "wait-prefect-deploy:",
        "prefect-deployments:",
        "prefect-deploy:",
        "reconcile:",
    ):
        assert target in makefile
    assert "--project-name $(PROJECT_NAME)" in makefile
    assert "THIRD_PARTY_SERVICES :=" in makefile
    assert "pull $(THIRD_PARTY_SERVICES)" in makefile
    assert "$(MAKE) build-core" in makefile
    assert "$(MAKE) build-orchestration" in makefile
    assert "up -d --no-build --remove-orphans --wait" in makefile
    assert 'exit_code="$$(docker wait "$$container_id")"' in makefile
    assert "down --volumes --remove-orphans" in makefile


def test_prefect_background_processes_have_meaningful_healthchecks() -> None:
    payload = yaml.safe_load(Path("compose.prefect.yaml").read_text(encoding="utf-8"))
    services = payload["services"]
    background = services["prefect-services"]
    worker = services["prefect-worker"]

    assert "prefect-server:4200/api/health" in background["healthcheck"]["test"][-1]
    assert worker["command"] == "python -m mini_lakehouse.platform.prefect_runtime worker"
    assert "localhost:8080/health" in worker["healthcheck"]["test"][-1]

    server_environment = background["environment"]
    assert server_environment["PREFECT_SERVER_DOCKET_URL"] == "redis://redis:6379/1"
    assert server_environment["PREFECT_SERVER_EVENTS_CAUSAL_ORDERING"] == "prefect_redis.ordering"
    assert (
        server_environment["PREFECT_SERVER_CONCURRENCY_LEASE_STORAGE"]
        == "prefect_redis.lease_storage"
    )
