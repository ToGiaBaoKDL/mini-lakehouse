import re
from pathlib import Path
from typing import cast

import yaml

from mini_lakehouse.contracts import load_contracts


def _yaml(path: str) -> dict[str, object]:
    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return cast(dict[str, object], payload)


def test_dbt_curated_sources_match_product_contracts() -> None:
    contracts = load_contracts()
    source_paths = sorted(Path("dbt/analytics/models/staging").rglob("*_sources.yml"))
    seen_sources: set[str] = set()

    assert source_paths
    for path in source_paths:
        dbt_sources = cast(list[dict[str, object]], _yaml(str(path))["sources"])
        for dbt_source in dbt_sources:
            source_name = str(dbt_source["name"])
            assert source_name not in seen_sources
            seen_sources.add(source_name)
            product = contracts.curated_product(source_name)
            tables = cast(list[dict[str, object]], dbt_source["tables"])

            assert dbt_source["description"] == product.description
            assert dbt_source["schema"] == ".".join(product.curated_namespace)
            source_meta = cast(dict[str, object], dbt_source["meta"])
            assert source_meta == {
                "owner": product.owner,
                "contact": product.contact.email,
            }
            product_tables = {table.name: table for table in product.tables}
            assert {str(table["name"]) for table in tables} <= set(product_tables)
            for table in tables:
                contract = product_tables[str(table["name"])]
                assert table["description"] == contract.description
                contract_columns = {column.name: column for column in contract.columns}
                dbt_columns = cast(list[dict[str, object]], table.get("columns", []))
                assert {str(column["name"]) for column in dbt_columns} <= set(contract_columns)
                if len(contract.primary_key) == 1:
                    key = contract.primary_key[0]
                    dbt_key = next(column for column in dbt_columns if column["name"] == key)
                    assert set(cast(list[str], dbt_key["data_tests"])) >= {
                        "not_null",
                        "unique",
                    }


def test_dbt_engineering_marts_match_the_domain_contract() -> None:
    contracts = load_contracts()
    domain = contracts.domain("engineering")
    analytics_maintenance = next(
        tier for tier in contracts.maintenance.tiers if tier.tier == "analytics"
    )
    engineering_optimization = next(
        optimization
        for optimization in analytics_maintenance.optimizations
        if optimization.target.path == domain.analytics_namespace
    )
    dbt_model_file = _yaml("dbt/analytics/models/marts/engineering/_engineering__models.yml")
    models = cast(list[dict[str, object]], dbt_model_file["models"])

    assert models
    for model in models:
        meta = cast(dict[str, object], model["meta"])
        assert meta == {
            "owner": domain.owner,
            "business_owner": domain.business_owner,
        }
        config = cast(dict[str, object], model.get("config", {}))
        contract = cast(dict[str, object], config.get("contract", {}))
        assert contract.get("enforced", False) is False
        data_tests = cast(list[dict[str, object]], model["data_tests"])
        unique_test = cast(dict[str, object], data_tests[0]["unique_combination_of_columns"])
        arguments = cast(dict[str, object], unique_test["arguments"])
        assert len(cast(list[str], arguments["combination_of_columns"])) == 2
        sql = Path(f"dbt/analytics/models/marts/engineering/{model['name']}.sql").read_text(
            encoding="utf-8"
        )
        assert f"month({engineering_optimization.partition_field})" in sql

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

    assert "vars" not in project_file


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
    assert "delete_after_commit_enabled" not in macro
    assert "max_previous_versions" not in macro

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
