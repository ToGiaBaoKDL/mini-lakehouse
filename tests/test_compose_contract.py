from collections.abc import Iterator
from pathlib import Path
from typing import Any

import yaml


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
