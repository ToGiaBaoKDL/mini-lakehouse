import re
from pathlib import Path
from typing import cast

import yaml

from lakehouse_platform.contracts import load_contracts


def _yaml(path: str) -> dict[str, object]:
    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return cast(dict[str, object], payload)


def test_dbt_sources_match_curated_product_contracts() -> None:
    contracts = load_contracts()
    source_file = _yaml("dbt/analytics/models/staging/github/_github__sources.yml")
    source = cast(list[dict[str, object]], source_file["sources"])[0]
    product = contracts.curated_product("github")
    source_tables = cast(list[dict[str, object]], source["tables"])
    contract_tables = {table.name: table for table in product.tables}
    type_names = {
        "boolean": "boolean",
        "date": "date",
        "long": "bigint",
        "string": "varchar",
        "timestamptz": "timestamp",
    }

    assert source["database"] == "awsdatacatalog"
    assert source["schema"] == product.database
    assert source["description"] == product.description
    assert {table["name"] for table in source_tables} == set(contract_tables)
    for source_table in source_tables:
        contract = contract_tables[cast(str, source_table["name"])]
        assert source_table["description"] == contract.description
        documented_columns = {
            cast(str, column["name"]): column
            for column in cast(list[dict[str, object]], source_table["columns"])
        }
        assert set(documented_columns) == {column.name for column in contract.columns}
        for column in contract.columns:
            documented = documented_columns[column.name]
            assert documented["description"] == column.description
            assert documented["data_type"] == type_names[column.data_type]


def test_dbt_athena_configuration_is_explicit_and_has_no_trino_fallback() -> None:
    profile_path = "dbt/analytics/profiles.yml"
    profile = Path(profile_path).read_text(encoding="utf-8")
    profile_config = _yaml(profile_path)
    project = _yaml("dbt/analytics/dbt_project.yml")
    models = cast(dict[str, object], project["models"])
    marts = cast(
        dict[str, object],
        cast(dict[str, object], models["lakehouse_platform_analytics"])["marts"],
    )
    project_config = cast(dict[str, object], models["lakehouse_platform_analytics"])
    domain = load_contracts().domain("engineering")
    project_meta = cast(dict[str, object], project_config["+meta"])
    mart_meta = cast(dict[str, object], cast(dict[str, object], marts["engineering"])["+meta"])
    output = cast(
        dict[str, object],
        cast(
            dict[str, object],
            cast(dict[str, object], profile_config["lakehouse_platform"])["outputs"],
        )["dev"],
    )

    assert "type: athena" in profile
    assert "type: trino" not in profile
    assert "aws_access_key_id" not in profile
    assert marts["+materialized"] == "table"
    assert marts["+table_type"] == "iceberg"
    assert "+s3_data_naming" not in marts
    assert "+on_table_exists" not in marts
    assert "+format" not in marts
    assert "+write_compression" not in marts
    assert "+native_drop" not in marts
    assert "work_group: primary" in profile
    assert "s3_staging_dir" in profile
    assert "DBT_QUERY_RESULTS_URI" in profile
    assert "aws_profile_name" not in profile
    assert "num_retries" not in profile
    assert "poll_interval" not in profile
    assert "LAKEHOUSE_ATHENA" not in profile
    assert "LAKEHOUSE_STORAGE" not in profile
    assert project_meta == {
        "owner": domain.owner,
        "contact": domain.contact.email,
    }
    assert mart_meta["business_owner"] == domain.business_owner
    assert output["schema"] == domain.database

    makefile = Path("Makefile").read_text(encoding="utf-8")
    assert "/storage/query_results_uri" in makefile
    assert "DBT_QUERY_RESULTS_URI" in makefile
    assert "/athena/workgroup" not in makefile


def test_dbt_uses_first_party_adapter_and_standard_test_package() -> None:
    project = Path("pyproject.toml").read_text(encoding="utf-8")
    packages = _yaml("dbt/analytics/packages.yml")
    mart_models = Path("dbt/analytics/models/marts/engineering/_engineering__models.yml").read_text(
        encoding="utf-8"
    )

    assert '"dbt-athena==1.11.0"' in project
    assert "dbt-athena-community" not in project
    assert packages == {"packages": [{"package": "dbt-labs/dbt_utils", "version": "1.4.1"}]}
    assert "dbt_utils.unique_combination_of_columns" in mart_models
    assert not Path("dbt/analytics/tests/generic/test_unique_combination_of_columns.sql").exists()


def test_dbt_models_use_explicit_projections() -> None:
    for path in Path("dbt/analytics/models").rglob("*.sql"):
        sql = path.read_text(encoding="utf-8")
        assert re.search(r"\bselect\s+\*", sql, flags=re.IGNORECASE) is None, path


def test_every_dbt_model_and_column_is_documented() -> None:
    documented_models: dict[str, dict[str, object]] = {}
    for path in Path("dbt/analytics/models").rglob("*__models.yml"):
        payload = _yaml(path.as_posix())
        for model in cast(list[dict[str, object]], payload["models"]):
            documented_models[cast(str, model["name"])] = model

    sql_models = {path.stem for path in Path("dbt/analytics/models").rglob("*.sql")}
    assert set(documented_models) == sql_models
    for model in documented_models.values():
        assert cast(str, model["description"]).strip()
        columns = cast(list[dict[str, object]], model["columns"])
        assert columns
        for column in columns:
            assert cast(str, column["description"]).strip()
            assert cast(str, column["data_type"]).strip()
