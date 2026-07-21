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
        "compose.dashboard.yaml",
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
