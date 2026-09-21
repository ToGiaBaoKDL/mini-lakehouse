"""Measure portable data-product SLOs from a versioned schedule and publication evidence."""

import argparse
import json
import sys
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

from jsonschema.exceptions import ValidationError
from validate import (
    load_mapping,
    parse_timestamp,
    require_unique,
    validate_data_product,
    validate_eligible_intervals,
    validate_publication,
    validate_publication_set,
)

Interval = tuple[datetime, datetime]


def interval_key(value: dict[str, Any]) -> Interval:
    interval = cast(dict[str, object], value["logical_interval"])
    return (
        parse_timestamp(interval["start"], "logical_interval.start"),
        parse_timestamp(interval["end"], "logical_interval.end"),
    )


def measure(
    product: dict[str, Any],
    schedule: dict[str, Any],
    publications: list[dict[str, Any]],
    publication_sets: list[dict[str, Any]],
) -> dict[str, Any]:
    validate_data_product(product)
    validate_eligible_intervals(schedule)
    measurement = cast(dict[str, Any], product["service_level"]["measurement"])
    if schedule["schedule_ref"] != measurement["eligible_intervals_ref"]:
        raise ValueError("schedule_ref does not match the product's eligible_intervals_ref")
    if schedule["unit"] != measurement["completeness_unit"]:
        raise ValueError("schedule unit does not match the product's completeness_unit")

    by_id = {cast(str, record["publication_id"]): record for record in publications}
    visible: dict[Interval, list[tuple[datetime, dict[str, Any]]]] = {}
    for record in publications:
        if record["product"] == product["name"] and record["status"] == "published":
            visible.setdefault(interval_key(record), []).append(
                (parse_timestamp(record["published_at"], "published_at"), record)
            )
    for group in publication_sets:
        published_at = parse_timestamp(group["published_at"], "published_at")
        for member in cast(list[dict[str, Any]], group["members"]):
            if member["product"] == product["name"]:
                record = by_id[member["publication_id"]]
                visible.setdefault(interval_key(record), []).append((published_at, record))

    as_of = parse_timestamp(schedule["as_of"], "as_of")
    window_start = as_of - timedelta(days=measurement["rolling_window_days"])
    eligible = 0
    on_time = 0
    published = 0
    warn_late = 0
    error_late = 0
    excluded_empty = 0
    pending = 0
    observed_total = 0
    expected_total = 0
    completeness_gate = cast(str, measurement["completeness_gate"])
    warn_after = cast(int, product["service_level"]["freshness"]["warn_after_minutes"])
    error_after = cast(int, product["service_level"]["freshness"]["error_after_minutes"])

    for item in cast(list[dict[str, Any]], schedule["intervals"]):
        start = parse_timestamp(item["start"], "interval.start")
        end = parse_timestamp(item["end"], "interval.end")
        if not window_start < end <= as_of:
            continue
        if end + timedelta(minutes=error_after) > as_of:
            pending += 1
            continue
        expected = cast(int, item["expected_units"])
        if (
            expected == 0
            and measurement["empty_interval_policy"] == "skip_with_evidence"
            and "empty_evidence" in item
        ):
            excluded_empty += 1
            continue
        eligible += 1
        expected_total += expected
        candidates = sorted(
            ((time, record) for time, record in visible.get((start, end), []) if time <= as_of),
            key=lambda candidate: candidate[0],
        )
        if not candidates:
            continue
        published_at, record = candidates[0]
        published += 1
        result = next(
            result
            for result in cast(list[dict[str, Any]], record["quality_results"])
            if result["gate"] == completeness_gate
        )
        if result["expected"] != expected:
            raise ValueError("publication completeness denominator differs from source schedule")
        observed_total += cast(int, result["observed"])
        lag_minutes = max(0.0, (published_at - end).total_seconds() / 60)
        if lag_minutes > warn_after:
            warn_late += 1
        if lag_minutes > error_after:
            error_late += 1
        else:
            on_time += 1

    availability = 100 * on_time / eligible if eligible else None
    completeness = 100 * observed_total / expected_total if expected_total else None
    availability_target = cast(float, product["service_level"]["availability_percent"])
    completeness_target = cast(float, product["service_level"]["completeness_percent"])
    availability_bad = eligible - on_time
    availability_budget = float(
        Decimal(eligible) * (Decimal(100) - Decimal(str(availability_target))) / Decimal(100)
    )
    completeness_missing = expected_total - observed_total
    completeness_budget = float(
        Decimal(expected_total) * (Decimal(100) - Decimal(str(completeness_target))) / Decimal(100)
    )
    status = "not_applicable" if not eligible else "met"
    if status == "met" and (
        (availability is not None and availability < availability_target)
        or (completeness is not None and completeness < completeness_target)
    ):
        status = "breached"
    return {
        "product": product["name"],
        "schedule_ref": schedule["schedule_ref"],
        "as_of": schedule["as_of"],
        "rolling_window_days": measurement["rolling_window_days"],
        "eligible_intervals": eligible,
        "pending_intervals": pending,
        "excluded_empty_intervals": excluded_empty,
        "published_intervals": published,
        "on_time_intervals": on_time,
        "freshness_warning_intervals": warn_late,
        "freshness_error_intervals": error_late,
        "availability_percent": availability,
        "availability_target_percent": availability_target,
        "availability_bad_intervals": availability_bad,
        "availability_error_budget_intervals": availability_budget,
        "availability_error_budget_remaining_intervals": availability_budget - availability_bad,
        "completeness_observed_units": observed_total,
        "completeness_expected_units": expected_total,
        "completeness_percent": completeness,
        "completeness_target_percent": completeness_target,
        "completeness_missing_units": completeness_missing,
        "completeness_error_budget_units": completeness_budget,
        "completeness_error_budget_remaining_units": completeness_budget - completeness_missing,
        "status": status,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--product", required=True)
    parser.add_argument("--product-contract", action="append", type=Path, required=True)
    parser.add_argument("--eligible-intervals", type=Path, required=True)
    parser.add_argument("--publication", action="append", type=Path, default=[])
    parser.add_argument("--publication-set", action="append", type=Path, default=[])
    arguments = parser.parse_args()
    try:
        products = [load_mapping(path) for path in arguments.product_contract]
        for product in products:
            validate_data_product(product)
        require_unique(
            [cast(str, product["name"]) for product in products], "product contract names"
        )
        by_name = {cast(str, product["name"]): product for product in products}
        product = by_name[arguments.product]
        schedule = load_mapping(arguments.eligible_intervals)
        publications = [load_mapping(path) for path in arguments.publication]
        require_unique(
            [cast(str, record["publication_id"]) for record in publications],
            "publication IDs",
        )
        for record in publications:
            validate_publication(record, by_name[record["product"]])
        by_id = {cast(str, record["publication_id"]): record for record in publications}
        publication_sets = [load_mapping(path) for path in arguments.publication_set]
        for group in publication_sets:
            members = [by_id[member["publication_id"]] for member in group["members"]]
            validate_publication_set(group, members)
        result = measure(product, schedule, publications, publication_sets)
    except (OSError, ValueError, ValidationError, KeyError) as error:
        detail = error.message if isinstance(error, ValidationError) else str(error)
        print(detail, file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
