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

    assert source["database"] == "awsdatacatalog"
    assert source["schema"] == product.database
    assert source["description"] == product.description
    assert {table["name"] for table in cast(list[dict[str, object]], source["tables"])} <= {
        table.name for table in product.tables
    }


def test_dbt_athena_configuration_is_explicit_and_has_no_trino_fallback() -> None:
    profile = Path("dbt/analytics/profiles.yml").read_text(encoding="utf-8")
    project = _yaml("dbt/analytics/dbt_project.yml")
    models = cast(dict[str, object], project["models"])
    marts = cast(
        dict[str, object],
        cast(dict[str, object], models["lakehouse_platform_analytics"])["marts"],
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
