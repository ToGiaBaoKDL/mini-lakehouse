import json
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

import yaml

TEMPLATE = Path("templates/enterprise-data-platform")
VALIDATOR = TEMPLATE / "validate.py"
MEASURER = TEMPLATE / "measure.py"
PRODUCT = TEMPLATE / "contracts/examples/customer-transactions.yaml"
PUBLICATION = TEMPLATE / "contracts/examples/customer-transactions-publication.yaml"
DAILY_PRODUCT = TEMPLATE / "contracts/examples/customer-transaction-daily.yaml"
CERTIFIED = TEMPLATE / "contracts/examples/customer-transactions-certified.yaml"
DAILY_CERTIFIED = TEMPLATE / "contracts/examples/customer-transaction-daily-certified.yaml"
PUBLICATION_SET = TEMPLATE / "contracts/examples/customer-transactions-set.yaml"
INTERVALS = TEMPLATE / "contracts/examples/posted-transactions-intervals.yaml"


def load_mapping(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return cast(dict[str, Any], payload)


def validate(
    kind: str,
    path: Path,
    *,
    products: tuple[Path, ...] | None = None,
    members: tuple[Path, ...] = (),
) -> subprocess.CompletedProcess[str]:
    if products is None:
        products = (PRODUCT,) if kind == "publication" else ()
    command = [sys.executable, VALIDATOR.as_posix(), kind]
    for product in products:
        command.extend(("--product-contract", product.as_posix()))
    for member in members:
        command.extend(("--member-publication", member.as_posix()))
    command.append(path.as_posix())
    return subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
    )


def write_yaml(tmp_path: Path, name: str, payload: dict[str, Any]) -> Path:
    path = tmp_path / f"{name}.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return path


def measure_slo(
    product: str,
    intervals: Path,
    *,
    products: tuple[Path, ...],
    publications: tuple[Path, ...] = (),
    publication_sets: tuple[Path, ...] = (),
) -> subprocess.CompletedProcess[str]:
    command = [
        sys.executable,
        MEASURER.as_posix(),
        "--product",
        product,
        "--eligible-intervals",
        intervals.as_posix(),
    ]
    for path in products:
        command.extend(("--product-contract", path.as_posix()))
    for path in publications:
        command.extend(("--publication", path.as_posix()))
    for path in publication_sets:
        command.extend(("--publication-set", path.as_posix()))
    return subprocess.run(command, check=False, capture_output=True, text=True)


def test_template_is_complete_and_does_not_speculate_about_platform_profiles() -> None:
    expected = {
        "README.md",
        "architecture.md",
        "validate.py",
        "measure.py",
        "contracts/data-product.schema.yaml",
        "contracts/publication.schema.yaml",
        "contracts/publication-set.schema.yaml",
        "contracts/eligible-intervals.schema.yaml",
        "contracts/examples/customer-transactions.yaml",
        "contracts/examples/customer-transactions-publication.yaml",
        "contracts/examples/customer-transactions-certified.yaml",
        "contracts/examples/customer-transaction-daily.yaml",
        "contracts/examples/customer-transaction-daily-certified.yaml",
        "contracts/examples/customer-transactions-set.yaml",
        "contracts/examples/posted-transactions-intervals.yaml",
        "delivery/lifecycle.md",
        "delivery/curation.md",
        "delivery/publication.md",
        "delivery/slo.md",
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
    cases = [
        ("data-product", PRODUCT, (), ()),
        ("data-product", DAILY_PRODUCT, (), ()),
        ("eligible-intervals", INTERVALS, (), ()),
        ("publication", PUBLICATION, (PRODUCT,), ()),
        ("publication", CERTIFIED, (PRODUCT,), ()),
        ("publication", DAILY_CERTIFIED, (DAILY_PRODUCT,), ()),
        (
            "publication-set",
            PUBLICATION_SET,
            (PRODUCT, DAILY_PRODUCT),
            (CERTIFIED, DAILY_CERTIFIED),
        ),
    ]
    for kind, path, products, members in cases:
        result = validate(kind, path, products=products, members=members)
        assert result.returncode == 0, result.stderr


def assert_invalid(
    tmp_path: Path,
    kind: str,
    name: str,
    payload: dict[str, Any],
    error: str,
    *,
    products: tuple[Path, ...] | None = None,
    members: tuple[Path, ...] = (),
) -> None:
    result = validate(kind, write_yaml(tmp_path, name, payload), products=products, members=members)
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

    missing_protection_policy = load_mapping(PRODUCT)
    protected_fields = cast(list[dict[str, Any]], missing_protection_policy["schema"]["fields"])
    cast(dict[str, str], protected_fields[2]["protection"]).pop("policy_ref")

    optional_key = load_mapping(PRODUCT)
    cast(list[dict[str, Any]], optional_key["schema"]["fields"])[0]["required"] = False

    wrong_time_type = load_mapping(PRODUCT)
    wrong_time_type["available_time_field"] = "amount"

    unknown_slo_gate = load_mapping(PRODUCT)
    cast(dict[str, Any], unknown_slo_gate["service_level"]["measurement"])["completeness_gate"] = (
        "unknown_gate"
    )

    wrong_slo_unit = load_mapping(PRODUCT)
    cast(dict[str, Any], wrong_slo_unit["service_level"]["measurement"])["completeness_unit"] = (
        "account"
    )

    cases = [
        ("missing-owner", missing_owner, "'owner' is a required property"),
        ("unknown-key", unknown_key, "primary_key references unknown fields"),
        (
            "underclassified",
            underclassified,
            "is more sensitive than product classification",
        ),
        ("invalid-freshness", invalid_freshness, "warning threshold must not exceed"),
        (
            "missing-protection-policy",
            missing_protection_policy,
            "'policy_ref' is a required property",
        ),
        ("optional-key", optional_key, "primary_key references optional fields"),
        ("wrong-time-type", wrong_time_type, "must reference a timestamp field"),
        ("unknown-slo-gate", unknown_slo_gate, "completeness_gate must name"),
        ("wrong-slo-unit", wrong_slo_unit, "completeness_unit must match"),
    ]
    for name, payload, error in cases:
        assert_invalid(tmp_path, "data-product", name, payload, error)


def test_kind_specific_contracts(tmp_path: Path) -> None:
    object_product = load_mapping(PRODUCT)
    object_product["kind"] = "object_collection"
    object_product.pop("schema")
    object_product.pop("primary_key")
    object_product.pop("event_time_field")
    object_product.pop("available_time_field")
    object_product["object_contract"] = {
        "format": "jsonl",
        "identity": "One immutable object per capture and source interval.",
        "schema_ref": "schemas/transaction-change-v1",
    }
    object_path = write_yaml(tmp_path, "object-valid", object_product)
    assert validate("data-product", object_path).returncode == 0

    missing_object_contract = load_mapping(object_path)
    missing_object_contract.pop("object_contract")
    assert_invalid(
        tmp_path,
        "data-product",
        "object-missing-contract",
        missing_object_contract,
        "'object_contract' is a required property",
    )

    missing_object_schema = load_mapping(object_path)
    cast(dict[str, str], missing_object_schema["object_contract"]).pop("schema_ref")
    assert_invalid(
        tmp_path,
        "data-product",
        "object-missing-schema",
        missing_object_schema,
        "'schema_ref' is a required property",
    )

    stream_product = load_mapping(PRODUCT)
    stream_product["kind"] = "stream"
    assert (
        validate("data-product", write_yaml(tmp_path, "stream-valid", stream_product)).returncode
        == 0
    )
    stream_product.pop("available_time_field")
    assert_invalid(
        tmp_path,
        "data-product",
        "stream-missing-time",
        stream_product,
        "'available_time_field' is a required property",
    )

    for kind in ("view", "feature_set"):
        product = load_mapping(PRODUCT)
        product["kind"] = kind
        assert validate("data-product", write_yaml(tmp_path, kind, product)).returncode == 0


def test_output_reference_kind_follows_the_product_kind(tmp_path: Path) -> None:
    for kind, output_kind in (
        ("object_collection", "object_manifest"),
        ("view", "view_revision"),
        ("stream", "stream_position"),
        ("feature_set", "table_snapshot"),
    ):
        product = load_mapping(PRODUCT)
        product["kind"] = kind
        if kind == "object_collection":
            product.pop("schema")
            product.pop("primary_key")
            product.pop("event_time_field")
            product.pop("available_time_field")
            product["object_contract"] = {
                "format": "binary",
                "identity": "One immutable object per source capture.",
            }
        product_path = write_yaml(tmp_path, f"{kind}-product", product)
        publication = load_mapping(PUBLICATION)
        cast(dict[str, Any], publication["output"])["kind"] = output_kind
        publication_path = write_yaml(tmp_path, f"{kind}-publication", publication)
        result = validate("publication", publication_path, products=(product_path,))
        assert result.returncode == 0, result.stderr

        cast(dict[str, Any], publication["output"])["kind"] = "raw_manifest"
        assert_invalid(
            tmp_path,
            "publication",
            f"{kind}-wrong-output",
            publication,
            f"{kind} output must reference a {output_kind}",
            products=(product_path,),
        )


def test_publication_validator_fails_closed(tmp_path: Path) -> None:
    missing_output = load_mapping(PUBLICATION)
    missing_output.pop("output")

    failed_gate = load_mapping(PUBLICATION)
    results = cast(list[dict[str, Any]], failed_gate["quality_results"])
    results[0]["outcome"] = "failed"

    warned_blocker = load_mapping(PUBLICATION)
    cast(list[dict[str, Any]], warned_blocker["quality_results"])[0]["outcome"] = "warned"

    missing_gate = load_mapping(PUBLICATION)
    cast(list[dict[str, Any]], missing_gate["quality_results"]).pop()

    unknown_gate = load_mapping(PUBLICATION)
    cast(list[dict[str, Any]], unknown_gate["quality_results"]).append(
        {
            "gate": "not_declared",
            "outcome": "passed",
            "control_ref": "controls/not-declared-v1",
            "evidence_ref": "evidence/customer-transactions/2026-09-19/not-declared",
            "evaluated_output_version": "1123581321",
        }
    )

    wrong_output_kind = load_mapping(PUBLICATION)
    cast(dict[str, Any], wrong_output_kind["output"])["kind"] = "object_manifest"

    wrong_contract_version = load_mapping(PUBLICATION)
    wrong_contract_version["contract_version"] = "2.0.0"

    wrong_writer = load_mapping(PUBLICATION)
    cast(dict[str, str], wrong_writer["publisher"])["identity"] = "another-writer"

    stale_control = load_mapping(PUBLICATION)
    cast(list[dict[str, Any]], stale_control["quality_results"])[0]["control_ref"] = (
        "controls/customer-transactions-key-v0"
    )

    stale_output_proof = load_mapping(PUBLICATION)
    cast(list[dict[str, Any]], stale_output_proof["quality_results"])[0][
        "evaluated_output_version"
    ] = "older-snapshot"

    invalid_completeness = load_mapping(PUBLICATION)
    cast(list[dict[str, Any]], invalid_completeness["quality_results"])[1]["observed"] = 50199

    early_publication = load_mapping(PUBLICATION)
    early_publication["published_at"] = "2026-09-19T17:08:00Z"

    certified_with_published_at = load_mapping(CERTIFIED)
    certified_with_published_at["published_at"] = "2026-09-19T17:09:00Z"

    invalid_interval = load_mapping(PUBLICATION)
    interval = cast(dict[str, str], invalid_interval["logical_interval"])
    interval["start"] = "2026-09-20T00:00:00Z"

    cases = [
        ("missing-output", missing_output, "'output' is a required property"),
        ("failed-gate", failed_gate, "blocking quality gates must pass"),
        ("warned-blocker", warned_blocker, "blocking quality gates must pass"),
        ("missing-gate", missing_gate, "publication is missing quality gates"),
        ("unknown-gate", unknown_gate, "publication contains undeclared quality gates"),
        ("wrong-output-kind", wrong_output_kind, "table output must reference a table_snapshot"),
        ("wrong-contract-version", wrong_contract_version, "must match the product contract"),
        ("wrong-writer", wrong_writer, "publisher identity must match"),
        ("stale-control", stale_control, "control_ref differs from product contract"),
        ("stale-output-proof", stale_output_proof, "did not evaluate the published output version"),
        ("invalid-completeness", invalid_completeness, "integer 0 <= observed <= expected"),
        ("early-publication", early_publication, "published_at must not precede"),
        ("certified-with-published-at", certified_with_published_at, "should not be valid"),
        ("invalid-interval", invalid_interval, "logical interval must be non-empty"),
    ]
    for name, payload, error in cases:
        assert_invalid(tmp_path, "publication", name, payload, error)


def test_warning_gate_can_fail_without_certifying_a_blocker(tmp_path: Path) -> None:
    product = load_mapping(PRODUCT)
    cast(list[dict[str, Any]], product["quality_gates"]).append(
        {
            "name": "optional_distribution_check",
            "dimension": "consistency",
            "severity": "warning",
            "expectation": "Distribution remains within the reviewed historical envelope.",
            "control_ref": "controls/distribution-v1",
        }
    )
    product_path = write_yaml(tmp_path, "warning-product", product)
    publication = load_mapping(PUBLICATION)
    cast(list[dict[str, Any]], publication["quality_results"]).append(
        {
            "gate": "optional_distribution_check",
            "outcome": "failed",
            "control_ref": "controls/distribution-v1",
            "evidence_ref": "evidence/customer-transactions/2026-09-19/distribution",
            "evaluated_output_version": "1123581321",
        }
    )
    result = validate(
        "publication",
        write_yaml(tmp_path, "warning-publication", publication),
        products=(product_path,),
    )
    assert result.returncode == 0, result.stderr


def test_publication_set_requires_one_consistent_certified_cut(tmp_path: Path) -> None:
    members = (CERTIFIED, DAILY_CERTIFIED)
    products = (PRODUCT, DAILY_PRODUCT)

    duplicate_product = load_mapping(PUBLICATION_SET)
    cast(list[dict[str, Any]], duplicate_product["members"])[1]["product"] = "customer_transactions"
    assert_invalid(
        tmp_path,
        "publication-set",
        "duplicate-member-product",
        duplicate_product,
        "member products must be unique",
        products=products,
        members=members,
    )

    early_set = load_mapping(PUBLICATION_SET)
    early_set["published_at"] = "2026-09-19T17:09:00Z"
    assert_invalid(
        tmp_path,
        "publication-set",
        "early-set",
        early_set,
        "cannot precede member certification",
        products=products,
        members=members,
    )

    visible_member = load_mapping(CERTIFIED)
    visible_member["status"] = "published"
    visible_member["published_at"] = "2026-09-19T17:09:00Z"
    visible_path = write_yaml(tmp_path, "individually-published-member", visible_member)
    assert_invalid(
        tmp_path,
        "publication-set",
        "visible-member-set",
        load_mapping(PUBLICATION_SET),
        "must be certified and not individually visible",
        products=products,
        members=(visible_path, DAILY_CERTIFIED),
    )

    changed_interval = load_mapping(DAILY_CERTIFIED)
    cast(dict[str, str], changed_interval["logical_interval"])["end"] = "2026-09-19T18:00:00Z"
    changed_path = write_yaml(tmp_path, "changed-member-interval", changed_interval)
    assert_invalid(
        tmp_path,
        "publication-set",
        "mismatched-member-interval",
        load_mapping(PUBLICATION_SET),
        "members must share the logical interval",
        products=products,
        members=(CERTIFIED, changed_path),
    )


def test_eligible_intervals_reject_overlap_and_false_empty_evidence(tmp_path: Path) -> None:
    overlap = load_mapping(INTERVALS)
    cast(list[dict[str, Any]], overlap["intervals"]).append(
        {
            "start": "2026-09-19T16:00:00Z",
            "end": "2026-09-20T16:00:00Z",
            "expected_units": 10,
        }
    )
    assert_invalid(tmp_path, "eligible-intervals", "overlap", overlap, "must not overlap")

    false_empty = load_mapping(INTERVALS)
    cast(list[dict[str, Any]], false_empty["intervals"])[0]["empty_evidence"] = {
        "kind": "raw_manifest",
        "identifier": "raw/posted/2026-09-19",
        "version": "1",
    }
    assert_invalid(
        tmp_path,
        "eligible-intervals",
        "false-empty",
        false_empty,
        "non-empty eligible interval cannot have empty_evidence",
    )


def test_slo_reference_measures_independent_and_group_publication() -> None:
    independent = measure_slo(
        "customer_transactions", INTERVALS, products=(PRODUCT,), publications=(PUBLICATION,)
    )
    assert independent.returncode == 0, independent.stderr
    independent_result = json.loads(independent.stdout)
    assert independent_result["status"] == "met"
    assert independent_result["availability_percent"] == 100
    assert independent_result["completeness_percent"] == 100
    assert independent_result["availability_bad_intervals"] == 0
    assert independent_result["completeness_missing_units"] == 0

    grouped = measure_slo(
        "customer_transaction_daily",
        INTERVALS,
        products=(PRODUCT, DAILY_PRODUCT),
        publications=(CERTIFIED, DAILY_CERTIFIED),
        publication_sets=(PUBLICATION_SET,),
    )
    assert grouped.returncode == 0, grouped.stderr
    grouped_result = json.loads(grouped.stdout)
    assert grouped_result["status"] == "met"
    assert grouped_result["published_intervals"] == 1


def test_slo_reference_counts_missing_late_and_unproven_empty_intervals(tmp_path: Path) -> None:
    missing = load_mapping(INTERVALS)
    missing["as_of"] = "2026-09-21T17:00:00Z"
    cast(list[dict[str, Any]], missing["intervals"]).append(
        {
            "start": "2026-09-19T17:00:00Z",
            "end": "2026-09-20T17:00:00Z",
            "expected_units": 100,
        }
    )
    missing_result = measure_slo(
        "customer_transactions",
        write_yaml(tmp_path, "missing-interval", missing),
        products=(PRODUCT,),
        publications=(PUBLICATION,),
    )
    assert missing_result.returncode == 0, missing_result.stderr
    missing_report = json.loads(missing_result.stdout)
    assert missing_report["status"] == "breached"
    assert missing_report["eligible_intervals"] == 2
    assert missing_report["availability_percent"] == 50
    assert missing_report["completeness_expected_units"] == 50298
    assert missing_report["availability_bad_intervals"] == 1
    assert missing_report["availability_error_budget_remaining_intervals"] < 0

    late = load_mapping(PUBLICATION)
    late["published_at"] = "2026-09-19T19:30:00Z"
    late_result = measure_slo(
        "customer_transactions",
        INTERVALS,
        products=(PRODUCT,),
        publications=(write_yaml(tmp_path, "late-publication", late),),
    )
    assert late_result.returncode == 0, late_result.stderr
    late_report = json.loads(late_result.stdout)
    assert late_report["availability_percent"] == 0
    assert late_report["freshness_error_intervals"] == 1

    empty_product = load_mapping(PRODUCT)
    cast(dict[str, Any], empty_product["service_level"]["measurement"])["empty_interval_policy"] = (
        "skip_with_evidence"
    )
    empty_product_path = write_yaml(tmp_path, "empty-product", empty_product)
    empty_schedule = load_mapping(INTERVALS)
    interval = cast(list[dict[str, Any]], empty_schedule["intervals"])[0]
    interval["expected_units"] = 0
    interval["empty_evidence"] = {
        "kind": "raw_manifest",
        "identifier": "raw/posted/2026-09-19",
        "version": "1",
    }
    empty_result = measure_slo(
        "customer_transactions",
        write_yaml(tmp_path, "proven-empty", empty_schedule),
        products=(empty_product_path,),
    )
    assert empty_result.returncode == 0, empty_result.stderr
    assert json.loads(empty_result.stdout)["status"] == "not_applicable"

    interval.pop("empty_evidence")
    unproven_result = measure_slo(
        "customer_transactions",
        write_yaml(tmp_path, "unproven-empty", empty_schedule),
        products=(empty_product_path,),
    )
    assert unproven_result.returncode == 0, unproven_result.stderr
    assert json.loads(unproven_result.stdout)["status"] == "breached"


def test_slo_does_not_fail_an_interval_before_its_deadline(tmp_path: Path) -> None:
    pending = load_mapping(INTERVALS)
    pending["as_of"] = "2026-09-19T17:30:00Z"
    result = measure_slo(
        "customer_transactions",
        write_yaml(tmp_path, "pending-interval", pending),
        products=(PRODUCT,),
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["status"] == "not_applicable"
    assert report["pending_intervals"] == 1
    assert report["eligible_intervals"] == 0


def test_slo_rejects_publication_source_denominator_drift(tmp_path: Path) -> None:
    drifted = load_mapping(INTERVALS)
    cast(list[dict[str, Any]], drifted["intervals"])[0]["expected_units"] = 50199
    result = measure_slo(
        "customer_transactions",
        write_yaml(tmp_path, "drifted-denominator", drifted),
        products=(PRODUCT,),
        publications=(PUBLICATION,),
    )
    assert result.returncode == 1
    assert "denominator differs from source schedule" in result.stderr


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
