"""Validate portable enterprise data-platform contracts."""

import argparse
import sys
from collections import Counter
from datetime import datetime
from itertools import pairwise
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
    "publication-set": ROOT / "contracts/publication-set.schema.yaml",
    "eligible-intervals": ROOT / "contracts/eligible-intervals.schema.yaml",
}
CLASSIFICATION = {"public": 0, "internal": 1, "confidential": 2, "restricted": 3}
OUTPUT_KINDS = {
    "object_collection": "object_manifest",
    "table": "table_snapshot",
    "view": "view_revision",
    "stream": "stream_position",
    "feature_set": "table_snapshot",
}


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

    fields = cast(list[dict[str, Any]], payload.get("schema", {}).get("fields", []))
    field_names = [cast(str, field["name"]) for field in fields]
    require_unique(field_names, "schema field names")

    primary_key = cast(list[str], payload.get("primary_key", []))
    missing_keys = sorted(set(primary_key) - set(field_names))
    if missing_keys:
        raise ValueError(f"primary_key references unknown fields: {', '.join(missing_keys)}")
    optional_keys = sorted(
        set(primary_key) - {cast(str, field["name"]) for field in fields if field["required"]}
    )
    if optional_keys:
        raise ValueError(f"primary_key references optional fields: {', '.join(optional_keys)}")

    for time_field in ("event_time_field", "available_time_field"):
        value = payload.get(time_field)
        if value is not None:
            if value not in field_names:
                raise ValueError(f"{time_field} references unknown field: {value}")
            field = next(field for field in fields if field["name"] == value)
            if field["logical_type"] != "timestamp":
                raise ValueError(f"{time_field} must reference a timestamp field: {value}")

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
    completeness_gate = cast(str, payload["service_level"]["measurement"]["completeness_gate"])
    matching_gates = [gate for gate in gates if gate["name"] == completeness_gate]
    if not matching_gates or matching_gates[0]["dimension"] not in {
        "completeness",
        "reconciliation",
    }:
        raise ValueError(
            "service_level completeness_gate must name a completeness/reconciliation gate"
        )
    if matching_gates[0]["severity"] != "blocking":
        raise ValueError("service_level completeness_gate must be blocking")
    if (
        matching_gates[0].get("unit")
        != payload["service_level"]["measurement"]["completeness_unit"]
    ):
        raise ValueError("service_level completeness_unit must match the completeness gate unit")


def validate_publication(payload: dict[str, Any], product: dict[str, Any]) -> None:
    validate_json_schema(payload, SCHEMAS["publication"])
    validate_data_product(product)
    if payload["product"] != product["name"] or payload["contract_version"] != product["version"]:
        raise ValueError("publication product and contract_version must match the product contract")
    if payload["publisher"]["identity"] != product["write_owner"]:
        raise ValueError("publication publisher identity must match the product write_owner")

    logical_interval = cast(dict[str, object], payload["logical_interval"])
    interval_start = parse_timestamp(logical_interval["start"], "logical_interval.start")
    interval_end = parse_timestamp(logical_interval["end"], "logical_interval.end")
    if interval_start >= interval_end:
        raise ValueError("logical interval must be non-empty and half-open [start, end)")

    started_at = parse_timestamp(payload["started_at"], "started_at")
    completed_at = parse_timestamp(payload["completed_at"], "completed_at")
    if completed_at < started_at:
        raise ValueError("completed_at must not precede started_at")
    if published_at_value := payload.get("published_at"):
        published_at = parse_timestamp(published_at_value, "published_at")
        if published_at < completed_at:
            raise ValueError("published_at must not precede completed_at")

    inputs = cast(list[dict[str, Any]], payload["inputs"])
    input_ids = [f"{item['kind']}:{item['identifier']}:{item['version']}" for item in inputs]
    require_unique(input_ids, "input references")

    results = cast(list[dict[str, Any]], payload["quality_results"])
    require_unique([cast(str, result["gate"]) for result in results], "quality result gates")
    declared_gates = {
        cast(str, gate["name"]): gate
        for gate in cast(list[dict[str, Any]], product["quality_gates"])
    }
    result_gates = {cast(str, result["gate"]): result for result in results}
    unknown_gates = sorted(set(result_gates) - set(declared_gates))
    if unknown_gates:
        raise ValueError(
            f"publication contains undeclared quality gates: {', '.join(unknown_gates)}"
        )
    for name, result in result_gates.items():
        if result["control_ref"] != declared_gates[name]["control_ref"]:
            raise ValueError(f"quality gate {name} control_ref differs from product contract")
    if payload["status"] in {"certified", "published"}:
        missing_gates = sorted(set(declared_gates) - set(result_gates))
        if missing_gates:
            raise ValueError(f"publication is missing quality gates: {', '.join(missing_gates)}")
        blocked = sorted(
            name
            for name, gate in declared_gates.items()
            if gate["severity"] == "blocking" and result_gates[name]["outcome"] != "passed"
        )
        if blocked:
            raise ValueError(f"blocking quality gates must pass: {', '.join(blocked)}")
        expected_kind = OUTPUT_KINDS[cast(str, product["kind"])]
        if payload["output"]["kind"] != expected_kind:
            raise ValueError(f"{product['kind']} output must reference a {expected_kind}")
        for name, result in result_gates.items():
            if result.get("evaluated_output_version") != payload["output"]["version"]:
                raise ValueError(
                    f"quality gate {name} did not evaluate the published output version"
                )
        if "failure" in payload:
            raise ValueError("certified or published output cannot contain a failure")
        completeness_gate = cast(str, product["service_level"]["measurement"]["completeness_gate"])
        completeness = result_gates[completeness_gate]
        observed, expected = completeness.get("observed"), completeness.get("expected")
        if type(observed) is not int or type(expected) is not int or not 0 <= observed <= expected:
            raise ValueError("completeness gate requires integer 0 <= observed <= expected")


def validate_publication_set(
    payload: dict[str, Any], member_publications: list[dict[str, Any]]
) -> None:
    validate_json_schema(payload, SCHEMAS["publication-set"])
    interval = cast(dict[str, object], payload["logical_interval"])
    if parse_timestamp(interval["start"], "logical_interval.start") >= parse_timestamp(
        interval["end"], "logical_interval.end"
    ):
        raise ValueError("logical interval must be non-empty and half-open [start, end)")
    members = cast(list[dict[str, Any]], payload["members"])
    require_unique([cast(str, member["product"]) for member in members], "member products")
    require_unique([cast(str, member["publication_id"]) for member in members], "member IDs")
    by_id = {
        cast(str, publication["publication_id"]): publication for publication in member_publications
    }
    require_unique(
        [cast(str, publication["publication_id"]) for publication in member_publications],
        "supplied publication IDs",
    )
    if set(by_id) != {member["publication_id"] for member in members}:
        raise ValueError("publication set members must match supplied publications exactly")
    published_at = parse_timestamp(payload["published_at"], "published_at")
    for member in members:
        publication = by_id[member["publication_id"]]
        if publication["status"] != "certified":
            raise ValueError(
                "publication set members must be certified and not individually visible"
            )
        if any(
            publication[key] != member[key]
            for key in ("product", "publication_id", "contract_version")
        ):
            raise ValueError("publication set member identity does not match certified publication")
        if publication["logical_interval"] != payload["logical_interval"]:
            raise ValueError("publication set members must share the logical interval")
        if parse_timestamp(publication["completed_at"], "completed_at") > published_at:
            raise ValueError("publication set cannot precede member certification")


def validate_eligible_intervals(payload: dict[str, Any]) -> None:
    validate_json_schema(payload, SCHEMAS["eligible-intervals"])
    parse_timestamp(payload["as_of"], "as_of")
    ordered = []
    for item in cast(list[dict[str, Any]], payload["intervals"]):
        start = parse_timestamp(item["start"], "interval.start")
        end = parse_timestamp(item["end"], "interval.end")
        if start >= end:
            raise ValueError("eligible interval must be non-empty and half-open [start, end)")
        if item["expected_units"] > 0 and "empty_evidence" in item:
            raise ValueError("non-empty eligible interval cannot have empty_evidence")
        ordered.append((start, end))
    ordered.sort()
    if any(previous[1] > current[0] for previous, current in pairwise(ordered)):
        raise ValueError("eligible intervals must not overlap")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=sorted(SCHEMAS))
    parser.add_argument("documents", nargs="+", type=Path)
    parser.add_argument("--product-contract", action="append", type=Path, default=[])
    parser.add_argument("--member-publication", action="append", type=Path, default=[])
    arguments = parser.parse_args()

    failed = False
    try:
        products = [load_mapping(path) for path in arguments.product_contract]
        for product in products:
            validate_data_product(product)
        require_unique(
            [cast(str, product["name"]) for product in products], "product contract names"
        )
        product_by_name = {cast(str, product["name"]): product for product in products}
        publications = [load_mapping(path) for path in arguments.member_publication]
        if arguments.kind in {"publication", "publication-set"} and not products:
            raise ValueError("publication validation requires --product-contract")
        if arguments.kind == "publication-set" and len(publications) < 2:
            raise ValueError(
                "publication-set validation requires at least two --member-publication"
            )
        if arguments.kind != "publication-set" and publications:
            raise ValueError("--member-publication is only valid for publication-set")
        for publication in publications:
            product_name = publication.get("product")
            if not isinstance(product_name, str):
                raise ValueError("member publication must name a product")
            product = product_by_name.get(product_name)
            if product is None:
                raise ValueError(f"no product contract for {product_name}")
            validate_publication(publication, product)
    except (OSError, ValueError, ValidationError, KeyError) as error:
        detail = error.message if isinstance(error, ValidationError) else str(error)
        print(detail, file=sys.stderr)
        return 1
    for path in arguments.documents:
        try:
            payload = load_mapping(path)
            if arguments.kind == "data-product":
                validate_data_product(payload)
            elif arguments.kind == "eligible-intervals":
                validate_eligible_intervals(payload)
            elif arguments.kind == "publication":
                product_name = payload.get("product")
                if not isinstance(product_name, str):
                    raise ValueError("publication must name a product")
                product = product_by_name.get(product_name)
                if product is None:
                    raise ValueError(f"no product contract for {product_name}")
                validate_publication(payload, product)
            else:
                validate_publication_set(payload, publications)
        except (OSError, ValueError, ValidationError, KeyError) as error:
            failed = True
            detail = error.message if isinstance(error, ValidationError) else str(error)
            print(f"{path}: {detail}", file=sys.stderr)

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
