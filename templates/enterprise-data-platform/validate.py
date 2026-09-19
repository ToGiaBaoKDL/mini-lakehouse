"""Validate portable enterprise data-platform contracts."""

import argparse
import sys
from collections import Counter
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any, cast

import yaml
from jsonschema import FormatChecker
from jsonschema.exceptions import ValidationError
from jsonschema.validators import validator_for

ROOT = Path(__file__).parent
SCHEMAS = {
    "data-product": ROOT / "contracts/data-product.schema.yaml",
    "publication": ROOT / "contracts/publication.schema.yaml",
}
CLASSIFICATION = {"public": 0, "internal": 1, "confidential": 2, "restricted": 3}


def load_mapping(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("document root must be a mapping")
    return cast(dict[str, Any], payload)


def validate_json_schema(payload: dict[str, Any], schema_path: Path) -> None:
    schema = load_mapping(schema_path)
    validator_class = validator_for(schema)
    validator_class.check_schema(schema)
    validator_class(schema, format_checker=FormatChecker()).validate(payload)


def require_unique(values: list[str], label: str) -> None:
    duplicates = sorted(value for value, count in Counter(values).items() if count > 1)
    if duplicates:
        raise ValueError(f"{label} must be unique: {', '.join(duplicates)}")


def parse_timestamp(value: object, label: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be an ISO 8601 string")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{label} must be an ISO 8601 timestamp") from error


def validate_data_product(payload: dict[str, Any]) -> None:
    validate_json_schema(payload, SCHEMAS["data-product"])

    fields = cast(list[dict[str, Any]], payload["schema"]["fields"])
    field_names = [cast(str, field["name"]) for field in fields]
    require_unique(field_names, "schema field names")

    primary_key = cast(list[str], payload["primary_key"])
    missing_keys = sorted(set(primary_key) - set(field_names))
    if missing_keys:
        raise ValueError(f"primary_key references unknown fields: {', '.join(missing_keys)}")

    for time_field in ("event_time_field", "available_time_field"):
        value = payload.get(time_field)
        if value is not None and value not in field_names:
            raise ValueError(f"{time_field} references unknown field: {value}")

    product_classification = cast(str, payload["classification"])
    for field in fields:
        field_classification = cast(str, field.get("classification", product_classification))
        if CLASSIFICATION[field_classification] > CLASSIFICATION[product_classification]:
            raise ValueError(f"field {field['name']} is more sensitive than product classification")
        if field["logical_type"] == "decimal" and field["scale"] > field["precision"]:
            raise ValueError(f"decimal field {field['name']} has scale greater than precision")

    freshness = cast(dict[str, int], payload["service_level"]["freshness"])
    if freshness["warn_after_minutes"] > freshness["error_after_minutes"]:
        raise ValueError("freshness warning threshold must not exceed the error threshold")

    gates = cast(list[dict[str, Any]], payload["quality_gates"])
    require_unique([cast(str, gate["name"]) for gate in gates], "quality gate names")
    if not any(gate["severity"] == "blocking" for gate in gates):
        raise ValueError("a production data product requires at least one blocking quality gate")


def validate_publication(payload: dict[str, Any]) -> None:
    validate_json_schema(payload, SCHEMAS["publication"])

    logical_interval = cast(dict[str, object], payload["logical_interval"])
    interval_start = parse_timestamp(logical_interval["start"], "logical_interval.start")
    interval_end = parse_timestamp(logical_interval["end"], "logical_interval.end")
    if interval_start >= interval_end:
        raise ValueError("logical interval must be non-empty and half-open [start, end)")

    started_at = parse_timestamp(payload["started_at"], "started_at")
    if completed_at_value := payload.get("completed_at"):
        completed_at = parse_timestamp(completed_at_value, "completed_at")
        if completed_at < started_at:
            raise ValueError("completed_at must not precede started_at")

    inputs = cast(list[dict[str, Any]], payload["inputs"])
    input_ids = [f"{item['kind']}:{item['identifier']}:{item['version']}" for item in inputs]
    require_unique(input_ids, "input references")

    results = cast(list[dict[str, Any]], payload["quality_results"])
    require_unique([cast(str, result["gate"]) for result in results], "quality result gates")
    if payload["status"] in {"certified", "published"}:
        if not results:
            raise ValueError("certified or published output requires quality results")
        if any(result["outcome"] == "failed" for result in results):
            raise ValueError("certified or published output cannot contain a failed quality gate")
        if "failure" in payload:
            raise ValueError("certified or published output cannot contain a failure")


VALIDATORS: dict[str, Callable[[dict[str, Any]], None]] = {
    "data-product": validate_data_product,
    "publication": validate_publication,
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=sorted(VALIDATORS))
    parser.add_argument("documents", nargs="+", type=Path)
    arguments = parser.parse_args()

    failed = False
    for path in arguments.documents:
        try:
            VALIDATORS[arguments.kind](load_mapping(path))
        except (OSError, ValueError, ValidationError) as error:
            failed = True
            detail = error.message if isinstance(error, ValidationError) else str(error)
            print(f"{path}: {detail}", file=sys.stderr)

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
