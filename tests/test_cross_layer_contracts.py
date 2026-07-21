from pathlib import Path
from typing import cast

import yaml

from mini_lakehouse.contracts import load_contracts


def _yaml(path: str) -> dict[str, object]:
    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return cast(dict[str, object], payload)


def test_dbt_curated_source_matches_the_product_contract() -> None:
    product = load_contracts().product("github")
    dbt_source_file = _yaml("dbt/analytics/models/staging/github/_github__sources.yml")
    dbt_sources = cast(list[dict[str, object]], dbt_source_file["sources"])
    dbt_source = dbt_sources[0]
    tables = cast(list[dict[str, object]], dbt_source["tables"])
    source_config = cast(dict[str, object], dbt_source["config"])

    assert dbt_source["name"] == product.name
    assert dbt_source["schema"] == ".".join(product.curated_namespace)
    assert source_config["group"] == product.dbt_group
    assert {table["name"] for table in tables} == {table.name for table in product.tables}


def test_dbt_engineering_marts_match_the_domain_contract() -> None:
    domain = load_contracts().domain("engineering")
    dbt_model_file = _yaml("dbt/analytics/models/marts/engineering/_engineering__models.yml")
    models = cast(list[dict[str, object]], dbt_model_file["models"])

    assert {model["name"] for model in models} == {table.name for table in domain.tables}

    project_file = _yaml("dbt/analytics/dbt_project.yml")
    project_models = cast(dict[str, object], project_file["models"])
    project = cast(dict[str, object], project_models["mini_lakehouse_analytics"])
    staging = cast(dict[str, object], project["staging"])
    intermediate = cast(dict[str, object], project["intermediate"])
    marts = cast(dict[str, object], project["marts"])
    engineering = cast(dict[str, object], marts["engineering"])

    assert staging["+materialized"] == "ephemeral"
    assert intermediate["+materialized"] == "ephemeral"
    assert engineering["+materialized"] == "table"
    assert engineering["+schema"] == ".".join(domain.analytics_namespace)
    assert engineering["+group"] == domain.dbt_group


def test_dbt_groups_match_product_and_domain_ownership() -> None:
    contracts = load_contracts()
    group_file = _yaml("dbt/analytics/models/_groups.yml")
    groups = cast(list[dict[str, object]], group_file["groups"])
    group_names = {group["name"] for group in groups}

    assert contracts.product("github").dbt_group in group_names
    assert contracts.domain("engineering").dbt_group in group_names
