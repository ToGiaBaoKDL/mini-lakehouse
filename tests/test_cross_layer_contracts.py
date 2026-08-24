import re
from pathlib import Path
from typing import cast

import yaml
from lakehouse.contracts import load_contracts

DBT_PROJECT = Path("analytics/dbt-project")


def _yaml(path: str) -> dict[str, object]:
    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return cast(dict[str, object], payload)


def test_dbt_sources_match_curated_product_contracts() -> None:
    contracts = load_contracts()
    sources: dict[str, list[dict[str, object]]] = {}
    for product_name in ("github", "arxiv"):
        source_file = _yaml((DBT_PROJECT / f"models/sources/{product_name}.yml").as_posix())
        source = cast(list[dict[str, object]], source_file["sources"])[0]
        product = contracts.curated_product(product_name)
        source_tables = cast(list[dict[str, object]], source["tables"])
        sources[product_name] = source_tables
        contract_tables = {table.name: table for table in product.tables}
        assert source["database"] == "awsdatacatalog"
        assert source["schema"] == product.database
        assert source["description"] == product.description
        assert source["loader"] == "emr-serverless"
        assert source["meta"] == {
            "owner": product.owner,
            "contact": product.contact.email,
        }
        assert {table["name"] for table in source_tables} == set(contract_tables)
        for source_table in source_tables:
            contract = contract_tables[cast(str, source_table["name"])]
            assert source_table["description"] == contract.description
            tested_columns = {
                cast(str, column["name"])
                for column in cast(list[dict[str, object]], source_table["columns"])
            }
            assert tested_columns <= {column.name for column in contract.columns}

    events = next(table for table in sources["github"] if table["name"] == "events")
    events_config = cast(dict[str, object], events["config"])
    assert events_config["loaded_at_field"] == "source_hour"
    assert events_config["freshness"] == {
        "warn_after": {"count": 30, "period": "hour"},
        "error_after": {"count": 54, "period": "hour"},
    }
    papers = next(table for table in sources["arxiv"] if table["name"] == "papers")
    papers_config = cast(dict[str, object], papers["config"])
    assert papers_config["loaded_at_field"] == "curated_at"
    assert papers_config["freshness"] == {
        "warn_after": {"count": 96, "period": "hour"},
        "error_after": {"count": 168, "period": "hour"},
    }


def test_dbt_athena_configuration_is_explicit() -> None:
    contracts = load_contracts()
    contract_domains = {domain.name for domain in contracts.domains}
    project = _yaml((DBT_PROJECT / "dbt_project.yml").as_posix())
    profile = (DBT_PROJECT / "profiles.yml").read_text(encoding="utf-8")
    profile_config = _yaml((DBT_PROJECT / "profiles.yml").as_posix())
    project_models = cast(
        dict[str, object],
        cast(dict[str, object], project["models"])["lakehouse_analytics"],
    )
    marts = cast(dict[str, object], project_models["marts"])
    outputs = cast(
        dict[str, object],
        cast(dict[str, object], profile_config["lakehouse_analytics"])["outputs"],
    )
    output = cast(dict[str, object], outputs["runtime"])

    assert project["profile"] == "lakehouse_analytics"
    assert set(outputs) == {"runtime"}
    assert "type: athena" in profile
    assert "aws_access_key_id" not in profile
    assert marts["+materialized"] == "table"
    assert marts["+table_type"] == "iceberg"
    assert marts["+access"] == "public"
    assert "+on_table_exists" not in marts
    assert "+format" not in marts
    assert "+write_compression" not in marts
    assert "+native_drop" not in marts
    assert "work_group: primary" in profile
    assert "DBT_QUERY_RESULTS_URI" in profile
    assert "aws_profile_name" not in profile
    assert "num_retries" not in profile
    assert "poll_interval" not in profile
    assert output["schema"] == "{{ env_var('DBT_SCHEMA') }}"
    assert output["s3_data_dir"] == (
        "{{ env_var('DBT_ANALYTICS_URI') }}/{{ env_var('DBT_DOMAIN') }}"
    )
    assert output["s3_data_naming"] == "table_unique"

    for domain_name in sorted(contract_domains):
        domain = contracts.domain(domain_name)
        domain_marts = cast(dict[str, object], marts[domain_name])
        mart_meta = cast(dict[str, object], domain_marts["+meta"])

        assert domain_marts["+group"] == domain_name
        assert domain_name in cast(list[str], domain_marts["+tags"])
        assert mart_meta == {
            "owner": domain.owner,
            "contact": domain.contact.email,
            "business_owner": domain.business_owner,
        }

    data_operations = Path("make/data.mk").read_text(encoding="utf-8")
    assert "/athena/dbt_$(DBT_DOMAIN)_output_uri" in data_operations
    assert "DBT_QUERY_RESULTS_URI" in data_operations
    assert "athena/workgroup" not in data_operations


def test_dbt_uses_first_party_adapter_and_standard_test_package() -> None:
    root_project = Path("pyproject.toml").read_text(encoding="utf-8")
    analytics_project = (DBT_PROJECT / "runtime/pyproject.toml").read_text(encoding="utf-8")
    packages = _yaml((DBT_PROJECT / "packages.yml").as_posix())
    mart_models = (DBT_PROJECT / "models/marts/engineering/_engineering__models.yml").read_text(
        encoding="utf-8"
    )

    assert '"dbt-athena==1.11.0"' in analytics_project
    assert "dbt-athena" not in root_project
    assert "dbt-athena-community" not in root_project
    assert "dbt-athena-community" not in analytics_project
    assert packages == {"packages": [{"package": "dbt-labs/dbt_utils", "version": "1.4.1"}]}
    assert "dbt_utils.unique_combination_of_columns" in mart_models
    assert not list(DBT_PROJECT.rglob("tests/generic/test_unique_combination_of_columns.sql"))


def test_dbt_models_use_explicit_projections() -> None:
    for path in (DBT_PROJECT / "models").rglob("*.sql"):
        sql = path.read_text(encoding="utf-8")
        assert re.search(r"\bselect\s+\*", sql, flags=re.IGNORECASE) is None, path


def test_every_dbt_model_and_column_is_documented() -> None:
    documented_models: dict[str, dict[str, object]] = {}
    for path in (DBT_PROJECT / "models").rglob("*__models.yml"):
        payload = _yaml(path.as_posix())
        for model in cast(list[dict[str, object]], payload["models"]):
            documented_models[cast(str, model["name"])] = model

    sql_models = {path.stem for path in (DBT_PROJECT / "models").rglob("*.sql")}
    assert set(documented_models) == sql_models
    for model in documented_models.values():
        assert cast(str, model["description"]).strip()
        columns = cast(list[dict[str, object]], model["columns"])
        assert columns
        for column in columns:
            assert cast(str, column["description"]).strip()
            assert cast(str, column["data_type"]).strip()
