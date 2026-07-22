import re
from pathlib import Path
from typing import cast

import yaml

from mini_lakehouse.contracts import load_contracts, partition_expression


def _yaml(path: str) -> dict[str, object]:
    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return cast(dict[str, object], payload)


def test_dbt_curated_source_matches_the_product_contract() -> None:
    product = load_contracts().curated_product("github")
    dbt_source_file = _yaml("dbt/analytics/models/staging/github/_github__sources.yml")
    dbt_sources = cast(list[dict[str, object]], dbt_source_file["sources"])
    dbt_source = dbt_sources[0]
    tables = cast(list[dict[str, object]], dbt_source["tables"])

    assert dbt_source["name"] == product.name
    assert dbt_source["schema"] == ".".join(product.curated_namespace)
    source_meta = cast(dict[str, object], dbt_source["meta"])
    assert source_meta["owner"] == product.owner
    assert source_meta["contact"] == product.contact.email
    assert {table["name"] for table in tables} == {table.name for table in product.tables}


def test_dbt_engineering_marts_match_the_domain_contract() -> None:
    domain = load_contracts().domain("engineering")
    dbt_model_file = _yaml("dbt/analytics/models/marts/engineering/_engineering__models.yml")
    models = cast(list[dict[str, object]], dbt_model_file["models"])

    assert {model["name"] for model in models} == {table.name for table in domain.tables}
    model_by_name = {str(model["name"]): model for model in models}
    for table in domain.tables:
        model = model_by_name[table.name]
        config = cast(dict[str, object], model.get("config", {}))
        contract = cast(dict[str, object], config.get("contract", {}))
        assert contract.get("enforced", False) is False
        data_tests = cast(list[dict[str, object]], model["data_tests"])
        unique_test = cast(dict[str, object], data_tests[0]["unique_combination_of_columns"])
        arguments = cast(dict[str, object], unique_test["arguments"])
        assert arguments["combination_of_columns"] == list(table.grain)
        sql = Path(f"dbt/analytics/models/marts/engineering/{table.name}.sql").read_text(
            encoding="utf-8"
        )
        for partition in table.partitioning:
            assert partition_expression(partition) in sql

    project_file = _yaml("dbt/analytics/dbt_project.yml")
    project_models = cast(dict[str, object], project_file["models"])
    project = cast(dict[str, object], project_models["mini_lakehouse_analytics"])
    staging = cast(dict[str, object], project["staging"])
    intermediate = cast(dict[str, object], project["intermediate"])
    marts = cast(dict[str, object], project["marts"])
    engineering = cast(dict[str, object], marts["engineering"])

    assert staging["+materialized"] == "ephemeral"
    assert intermediate["+materialized"] == "ephemeral"
    assert marts["+materialized"] == "table"
    assert marts["+on_table_exists"] == "replace"
    assert engineering["+schema"] == ".".join(domain.analytics_namespace)


def test_dbt_does_not_use_groups_for_current_scope() -> None:
    assert not Path("dbt/analytics/models/_groups.yml").exists()
    dbt_files = [
        Path("dbt/analytics/dbt_project.yml"),
        Path("dbt/analytics/profiles.yml"),
        Path("dbt/analytics/selectors.yml"),
        *sorted(Path("dbt/analytics/models").rglob("*.yml")),
    ]
    assert dbt_files
    for path in dbt_files:
        payload = path.read_text(encoding="utf-8")
        assert "group:" not in payload
        assert "+group:" not in payload


def test_dbt_sql_uses_explicit_projection_and_namespace_owned_locations() -> None:
    model_paths = sorted(Path("dbt/analytics/models").rglob("*.sql"))

    assert model_paths
    for path in model_paths:
        sql = path.read_text(encoding="utf-8")
        assert re.search(r"\bselect\s+\*", sql, flags=re.IGNORECASE) is None, path

    for path in Path("dbt/analytics/models/marts").rglob("*.sql"):
        sql = path.read_text(encoding="utf-8")
        assert "analytics_iceberg_properties('engineering'" in sql
        assert "on_table_exists" not in sql

    macro = Path("dbt/analytics/macros/iceberg_properties.sql").read_text(encoding="utf-8")
    assert "location" not in macro
    assert "LAKEHOUSE_STORAGE__ANALYTICS_URI" not in macro
    assert "split(" not in macro
    assert "expected_schema = 'analytics.' ~ domain" in macro

    trino_catalog = Path("infra/trino/etc/catalog/prod.properties").read_text(encoding="utf-8")
    assert "iceberg.unique-table-location=false" in trino_catalog


def test_dbt_selectors_define_orchestration_boundaries() -> None:
    selectors_file = _yaml("dbt/analytics/selectors.yml")
    selectors = cast(list[dict[str, object]], selectors_file["selectors"])
    selector_by_name = {str(selector["name"]): selector for selector in selectors}

    assert set(selector_by_name) == {"github_sources", "engineering_marts"}
    github_definition = cast(dict[str, object], selector_by_name["github_sources"]["definition"])
    mart_definition = cast(dict[str, object], selector_by_name["engineering_marts"]["definition"])
    assert github_definition == {"method": "source", "value": "github"}
    assert mart_definition == {
        "method": "fqn",
        "value": "mini_lakehouse_analytics.marts.engineering",
    }
