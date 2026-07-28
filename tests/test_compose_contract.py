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
        "compose.ocr-review.yaml",
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
    platform = Path("infra/config/platform.container.env").read_text(encoding="utf-8")
    orchestration = Path("infra/config/orchestration.container.env").read_text(encoding="utf-8")
    review = Path("infra/config/ocr-review.container.env").read_text(encoding="utf-8")
    environment = f"{platform}\n{orchestration}\n{review}"

    assert "LAKEHOUSE_POLARIS__URI=http://polaris:8181/api/catalog" in platform
    assert "LAKEHOUSE_STORAGE__NETWORK_SCOPE=internal" in platform
    assert "LAKEHOUSE_STORAGE__ENDPOINTS__" not in platform
    assert "LAKEHOUSE_TRINO__HOST=trino" not in platform
    assert "LAKEHOUSE_TRINO__HOST=trino" in orchestration
    assert "LAKEHOUSE_TRINO__CATALOG" not in orchestration
    assert "LAKEHOUSE_TRINO__HOST=trino" in review
    assert "LAKEHOUSE_TRINO__USER=ocr-review" in review
    assert "LAKEHOUSE_POLARIS__URI" not in orchestration
    assert "ACCESS_KEY=" not in environment
    assert "SECRET_KEY=" not in environment
    assert "CREDENTIAL=" not in environment


def test_local_env_template_contains_complete_storage_runtime_defaults() -> None:
    environment = Path(".env.example").read_text(encoding="utf-8")

    for expected in (
        "LAKEHOUSE_STORAGE__BACKEND=s3",
        "LAKEHOUSE_STORAGE__NETWORK_SCOPE=external",
        "LAKEHOUSE_STORAGE__ENDPOINTS__EXTERNAL_URL=http://localhost:9000",
        "LAKEHOUSE_STORAGE__ENDPOINTS__INTERNAL_URL=http://object-store:9000",
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
        "mini-lakehouse:local": ["compose.core.yaml:platform-admin"],
        "mini-lakehouse-ocr-review:local": ["compose.ocr-review.yaml:ocr-review"],
        "mini-lakehouse-orchestration:local": ["compose.prefect.yaml:prefect-worker"],
    }


def test_restart_policies_match_service_lifecycle() -> None:
    services: dict[str, dict[str, Any]] = {}
    for path in sorted(Path.cwd().glob("compose*.yaml")):
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        services.update(payload.get("services", {}))

    long_running = {
        "postgres",
        "object-store",
        "polaris",
        "trino",
        "redis",
        "prefect-server",
        "prefect-services",
        "prefect-worker",
        "ocr-review",
    }
    one_shot = {
        "object-store-provision",
        "polaris-bootstrap",
        "platform-admin",
        "prefect-bootstrap",
        "prefect-deploy",
    }

    assert long_running | one_shot == set(services)
    assert all(services[name].get("restart") == "unless-stopped" for name in long_running)
    assert all(services[name].get("restart") == "no" for name in one_shot)


def test_application_base_images_are_version_pinned() -> None:
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")

    assert "FROM ghcr.io/astral-sh/uv:0.11.30 AS uv" in dockerfile
    assert "FROM python:3.13.14-slim AS base" in dockerfile
    assert ":latest" not in dockerfile
    assert 'CMD ["python", "-m", "mini_lakehouse.platform.validate"]' in dockerfile
    assert "lakehouse --help" not in dockerfile


def test_generic_application_cli_is_absent() -> None:
    project = Path("pyproject.toml").read_text(encoding="utf-8")

    assert not Path("src/mini_lakehouse/cli.py").exists()
    assert "[project.scripts]" not in project


def test_container_environment_files_follow_service_ownership() -> None:
    core = yaml.safe_load(Path("compose.core.yaml").read_text(encoding="utf-8"))
    prefect = yaml.safe_load(Path("compose.prefect.yaml").read_text(encoding="utf-8"))
    review = yaml.safe_load(Path("compose.ocr-review.yaml").read_text(encoding="utf-8"))

    assert core["services"]["platform-admin"]["env_file"] == [
        "./infra/config/platform.container.env"
    ]
    assert (
        core["services"]["platform-admin"]["environment"]["LAKEHOUSE_PLATFORM_ADMIN__ENABLED"]
        == "true"
    )
    assert (
        "./infra/config/platform-admin.guard:/run/secrets/platform_admin_guard:ro"
        in core["services"]["platform-admin"]["volumes"]
    )
    assert core["services"]["platform-admin"]["profiles"] == ["operations"]
    assert core["services"]["platform-admin"]["command"] == (
        "python -m mini_lakehouse.platform.catalog.admin validate"
    )
    assert core["services"]["trino"]["depends_on"] == {"polaris": {"condition": "service_healthy"}}
    assert prefect["x-lakehouse-runtime"]["env_file"] == [
        "./infra/config/platform.container.env",
        "./infra/config/orchestration.container.env",
    ]
    assert review["services"]["ocr-review"]["env_file"] == [
        "./infra/config/platform.container.env",
        "./infra/config/ocr-review.container.env",
    ]
    runtime_environment = prefect["x-lakehouse-runtime"]["environment"]
    for setting in (
        "LAKEHOUSE_STORAGE__ENDPOINTS__EXTERNAL_URL",
        "LAKEHOUSE_STORAGE__ENDPOINTS__INTERNAL_URL",
        "LAKEHOUSE_TRINO__CATALOG",
        "LAKEHOUSE_ARXIV__BASE_URL",
        "LAKEHOUSE_KAGGLE__API_TOKEN",
        "MODAL_TOKEN_SECRET",
        "LAKEHOUSE_NOTIFICATIONS__SLACK_BOT_TOKEN",
        "LAKEHOUSE_NOTIFICATIONS__GMAIL_APP_PASSWORD",
        "DBT_THREADS",
    ):
        assert setting in runtime_environment


def test_compose_uses_env_owned_routes_and_has_no_secret_fallbacks() -> None:
    core = Path("compose.core.yaml").read_text(encoding="utf-8")
    prefect = Path("compose.prefect.yaml").read_text(encoding="utf-8")
    review = Path("compose.ocr-review.yaml").read_text(encoding="utf-8")
    rendered_sources = f"{core}\n{prefect}\n{review}"

    assert ":-minioadmin" not in rendered_sources
    assert ":-secretpassword" not in rendered_sources
    assert "POSTGRES_PASSWORD:-lakehouse" not in rendered_sources
    assert (
        "OBJECT_STORE_ENDPOINT: "
        "${LAKEHOUSE_STORAGE__ENDPOINTS__INTERNAL_URL:-http://object-store:9000}"
    ) in core
    assert (
        "AWS_ENDPOINT_URL_S3: "
        "${LAKEHOUSE_STORAGE__ENDPOINTS__INTERNAL_URL:-http://object-store:9000}"
    ) in core
    assert (
        "S3_ENDPOINT: ${LAKEHOUSE_STORAGE__ENDPOINTS__INTERNAL_URL:-http://object-store:9000}"
    ) in core


def test_polaris_bootstrap_wraps_only_the_pinned_official_tool() -> None:
    payload = yaml.safe_load(Path("compose.core.yaml").read_text(encoding="utf-8"))
    bootstrap = payload["services"]["polaris-bootstrap"]

    assert bootstrap["image"] == "apache/polaris-admin-tool:1.6.0"
    assert bootstrap["command"][0] == "bootstrap"
    assert bootstrap["entrypoint"] == ["/bin/sh", "/opt/polaris-bootstrap.sh"]
    assert "./infra/polaris/bootstrap.sh:/opt/polaris-bootstrap.sh:ro" in bootstrap["volumes"]
    wrapper = Path("infra/polaris/bootstrap.sh").read_text(encoding="utf-8")
    assert 'java -jar /deployments/polaris-admin-tool.jar "$@"' in wrapper
    assert '"$status" -eq 3' in wrapper


def test_trino_identity_and_catalog_are_runtime_parameters() -> None:
    node = Path("infra/trino/etc/node.properties").read_text(encoding="utf-8")
    catalog = Path("infra/trino/etc/catalog/prod.properties").read_text(encoding="utf-8")

    assert "node.id=${ENV:TRINO_NODE_ID}" in node
    assert "ffffffff-ffff" not in node
    assert "iceberg.rest-catalog.warehouse=${ENV:POLARIS_CATALOG}" in catalog


def test_aistor_license_and_data_are_mounted_at_canonical_paths() -> None:
    payload = yaml.safe_load(Path("compose.core.yaml").read_text(encoding="utf-8"))
    object_store = payload["services"]["object-store"]
    provision = payload["services"]["object-store-provision"]

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
    assert provision["entrypoint"] == ["/bin/sh", "/opt/object-store/lifecycle-buckets.sh"]
    assert provision["command"] == ["provision"]
    assert (
        "./infra/object-store/lifecycle-buckets.sh:/opt/object-store/lifecycle-buckets.sh:ro"
        in provision["volumes"]
    )


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
        "build-ocr-review:",
        "start-core:",
        "start-ocr-review:",
        "start:",
        "up-core:",
        "up-ocr-review:",
        "up:",
        "down:",
        "clean:",
        "reset:",
        "ps:",
        "ps-all:",
        "logs:",
        "logs-follow:",
        "smoke-core:",
        "smoke-prefect:",
        "smoke-ocr-review:",
        "smoke:",
        "wait-prefect-deploy:",
        "prefect-deployments:",
        "prefect-deploy:",
        "platform-bootstrap:",
        "platform-validate:",
        "policy-prune-plan:",
        "policy-prune-apply:",
    ):
        assert target in makefile
    assert "--project-name $(PROJECT_NAME)" in makefile
    assert "CORE_RUN := COMPOSE_IGNORE_ORPHANS=true $(CORE_COMPOSE)" in makefile
    assert "OCR_REVIEW_RUN := COMPOSE_IGNORE_ORPHANS=true $(OCR_REVIEW_COMPOSE)" in makefile
    assert "THIRD_PARTY_SERVICES :=" in makefile
    assert "pull $(THIRD_PARTY_SERVICES)" in makefile
    assert "$(MAKE) build-core" in makefile
    assert "$(MAKE) build-orchestration" in makefile
    assert "$(MAKE) build-ocr-review" in makefile
    start_core_recipe = makefile.split("start-core:", maxsplit=1)[1].split("\n\n", maxsplit=1)[0]
    assert "--remove-orphans" not in start_core_recipe
    assert "up -d --no-build --remove-orphans --wait" in makefile
    assert 'exit_code="$$(docker wait "$$container_id")"' in makefile
    assert "down --volumes --remove-orphans" in makefile
    assert "python -m mini_lakehouse.platform.catalog.admin bootstrap" in makefile
    assert "python -m mini_lakehouse.platform.catalog.admin validate" in makefile
    assert "platform-plan:" not in makefile
    assert "platform-apply:" not in makefile
    reset_recipe = makefile.split("reset:", maxsplit=1)[1].split("\n\n", maxsplit=1)[0]
    assert "$(MAKE) start-core" in reset_recipe
    assert "$(MAKE) platform-plan" not in reset_recipe
    assert "$(MAKE) up" not in reset_recipe


def test_prefect_background_processes_expose_only_owned_health_signals() -> None:
    payload = yaml.safe_load(Path("compose.prefect.yaml").read_text(encoding="utf-8"))
    services = payload["services"]
    background = services["prefect-services"]
    worker = services["prefect-worker"]

    # Prefect's background services do not expose their own health endpoint.
    # Container process state is more truthful than proxying the server's health.
    assert "healthcheck" not in background
    assert background["restart"] == "unless-stopped"
    assert worker["command"] == "python -m orchestration.runtime worker"
    assert "localhost:8080/health" in worker["healthcheck"]["test"][-1]

    server_environment = background["environment"]
    assert server_environment["PREFECT_SERVER_DOCKET_URL"] == "redis://redis:6379/1"
    assert server_environment["PREFECT_SERVER_EVENTS_CAUSAL_ORDERING"] == "prefect_redis.ordering"
    assert (
        server_environment["PREFECT_SERVER_CONCURRENCY_LEASE_STORAGE"]
        == "prefect_redis.lease_storage"
    )
