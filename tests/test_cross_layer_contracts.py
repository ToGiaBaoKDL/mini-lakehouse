from pathlib import Path
from typing import cast

import yaml

from mini_lakehouse.contracts import load_contracts


def _yaml(path: str) -> dict[str, object]:
    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return cast(dict[str, object], payload)


def test_dbt_landing_source_matches_the_source_contract() -> None:
    source = load_contracts().source("github_archive")
    dbt_source_file = _yaml(
        "dbt_project/models/staging/github_archive/_github_archive__sources.yml"
    )
    dbt_sources = cast(list[dict[str, object]], dbt_source_file["sources"])
    dbt_source = dbt_sources[0]
    tables = cast(list[dict[str, object]], dbt_source["tables"])

    assert dbt_source["name"] == source.name
    assert dbt_source["schema"] == ".".join(source.landing_namespace)
    assert {table["name"] for table in tables} == {table.name for table in source.tables}


def test_dbt_engineering_marts_match_the_domain_contract() -> None:
    domain = load_contracts().domain("engineering")
    dbt_model_file = _yaml("dbt_project/models/marts/engineering/_engineering__models.yml")
    models = cast(list[dict[str, object]], dbt_model_file["models"])

    assert {model["name"] for model in models} == {table.name for table in domain.tables}


def test_dbt_groups_match_source_and_domain_ownership() -> None:
    contracts = load_contracts()
    group_file = _yaml("dbt_project/models/_groups.yml")
    groups = cast(list[dict[str, object]], group_file["groups"])
    group_names = {group["name"] for group in groups}

    assert contracts.source("github_archive").dbt_group in group_names
    assert contracts.domain("engineering").dbt_group in group_names
