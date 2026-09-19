import subprocess
import sys
from pathlib import Path
from typing import Any, cast

import yaml

TEMPLATE = Path("templates/enterprise-data-platform")
VALIDATOR = TEMPLATE / "validate.py"
PRODUCT = TEMPLATE / "contracts/examples/customer-transactions.yaml"
PUBLICATION = TEMPLATE / "contracts/examples/customer-transactions-publication.yaml"


def load_mapping(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return cast(dict[str, Any], payload)


def validate(kind: str, path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, VALIDATOR.as_posix(), kind, path.as_posix()],
        check=False,
        capture_output=True,
        text=True,
    )


def write_yaml(tmp_path: Path, name: str, payload: dict[str, Any]) -> Path:
    path = tmp_path / f"{name}.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return path


def test_template_is_complete_and_does_not_speculate_about_platform_profiles() -> None:
    expected = {
        "README.md",
        "architecture.md",
        "validate.py",
        "contracts/data-product.schema.yaml",
        "contracts/publication.schema.yaml",
        "contracts/examples/customer-transactions.yaml",
        "contracts/examples/customer-transactions-publication.yaml",
        "delivery/lifecycle.md",
        "delivery/readiness-gates.yaml",
        "capabilities/batch.md",
        "capabilities/cdc.md",
        "capabilities/streaming.md",
        "capabilities/machine-learning.md",
        "capabilities/data-sharing.md",
        "operations/backfill.md",
        "operations/schema-change.md",
        "operations/cutover.md",
        "operations/incident-recovery.md",
        "operations/disaster-recovery.md",
    }

    assert expected <= {
        path.relative_to(TEMPLATE).as_posix() for path in TEMPLATE.rglob("*") if path.is_file()
    }
    assert not (TEMPLATE / "profiles").exists()


def test_reference_contracts_validate() -> None:
    for kind, path in (("data-product", PRODUCT), ("publication", PUBLICATION)):
        result = validate(kind, path)
        assert result.returncode == 0, result.stderr


def assert_invalid(
    tmp_path: Path,
    kind: str,
    name: str,
    payload: dict[str, Any],
    error: str,
) -> None:
    result = validate(kind, write_yaml(tmp_path, name, payload))
    assert result.returncode == 1
    assert error in result.stderr


def test_data_product_validator_fails_closed(tmp_path: Path) -> None:
    missing_owner = load_mapping(PRODUCT)
    missing_owner.pop("owner")

    unknown_key = load_mapping(PRODUCT)
    cast(list[str], unknown_key["primary_key"]).append("missing_field")

    underclassified = load_mapping(PRODUCT)
    fields = cast(list[dict[str, Any]], underclassified["schema"]["fields"])
    fields[0]["classification"] = "restricted"

    invalid_freshness = load_mapping(PRODUCT)
    freshness = cast(dict[str, int], invalid_freshness["service_level"]["freshness"])
    freshness["warn_after_minutes"] = 180

    cases = [
        ("missing-owner", missing_owner, "'owner' is a required property"),
        ("unknown-key", unknown_key, "primary_key references unknown fields"),
        (
            "underclassified",
            underclassified,
            "is more sensitive than product classification",
        ),
        ("invalid-freshness", invalid_freshness, "warning threshold must not exceed"),
    ]
    for name, payload, error in cases:
        assert_invalid(tmp_path, "data-product", name, payload, error)


def test_publication_validator_fails_closed(tmp_path: Path) -> None:
    missing_output = load_mapping(PUBLICATION)
    missing_output.pop("output")

    failed_gate = load_mapping(PUBLICATION)
    results = cast(list[dict[str, Any]], failed_gate["quality_results"])
    results[0]["outcome"] = "failed"

    invalid_interval = load_mapping(PUBLICATION)
    interval = cast(dict[str, str], invalid_interval["logical_interval"])
    interval["start"] = "2026-09-20T00:00:00Z"

    cases = [
        ("missing-output", missing_output, "'output' is a required property"),
        ("failed-gate", failed_gate, "cannot contain a failed quality gate"),
        ("invalid-interval", invalid_interval, "logical interval must be non-empty"),
    ]
    for name, payload, error in cases:
        assert_invalid(tmp_path, "publication", name, payload, error)


def test_readiness_gates_form_one_ordered_dependency_chain() -> None:
    payload = load_mapping(TEMPLATE / "delivery/readiness-gates.yaml")
    gates = cast(list[dict[str, Any]], payload["gates"])
    gate_ids = [cast(str, gate["id"]) for gate in gates]

    assert payload["version"] == 1
    assert gate_ids == [
        "specified",
        "implementation_ready",
        "release_ready",
        "production_ready",
        "operable",
    ]
    for index, gate in enumerate(gates):
        assert gate["required_evidence"]
        assert gate["blocks_on"]
        assert gate.get("requires", []) == ([] if index == 0 else [gate_ids[index - 1]])
