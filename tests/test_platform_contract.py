from pathlib import Path

from lakehouse.catalog.layout import managed_tables, namespace_properties
from lakehouse.contracts import load_contracts


def test_glue_databases_are_derived_from_owned_contracts() -> None:
    contracts = load_contracts()
    namespaces = {
        namespace.path: namespace_properties(namespace)
        for namespace in contracts.managed_namespaces()
    }

    assert namespaces[("landing_github_archive",)]["source"] == "github_archive"
    assert namespaces[("landing_arxiv",)]["data_tier"] == "landing"
    assert namespaces[("curated_github",)]["data_product"] == "github"
    assert namespaces[("curated_arxiv",)]["data_product"] == "arxiv"
    assert namespaces[("analytics_engineering",)]["business_domain"] == "engineering"


def test_table_locations_separate_raw_transport_and_iceberg_storage() -> None:
    bindings = {
        identifier.iceberg: location
        for identifier, location, _ in managed_tables(
            load_contracts(),
            landing_uri="s3://test-landing",
            curated_uri="s3://test-curated",
        )
    }

    assert bindings[("landing_github_archive", "events_raw")] == (
        "s3://test-landing/api/github_archive/tables/events_raw"
    )
    assert bindings[("landing_arxiv", "oai_records_raw")] == (
        "s3://test-landing/api/arxiv/tables/oai_records_raw"
    )
    assert bindings[("curated_github", "events")] == ("s3://test-curated/github/tables/events")


def test_table_location_uses_physical_name_instead_of_contract_lookup_key() -> None:
    contracts = load_contracts()
    source = contracts.source("github_archive")
    table = source.table("events_raw").model_copy(update={"key": "events"})
    renamed_source = source.model_copy(update={"tables": (table,)})
    updated_contracts = contracts.model_copy(
        update={
            "sources": tuple(
                renamed_source if item.name == source.name else item for item in contracts.sources
            )
        }
    )

    identifier, location, _ = next(
        binding
        for binding in managed_tables(
            updated_contracts,
            landing_uri="s3://test-landing",
            curated_uri="s3://test-curated",
        )
        if binding[0].iceberg == ("landing_github_archive", "events_raw")
    )

    assert identifier.name == "events_raw"
    assert location.endswith("/tables/events_raw")


def test_data_jobs_do_not_create_or_evolve_catalog_objects() -> None:
    forbidden = (
        "CREATE TABLE",
        "CREATE DATABASE",
        "mergeSchema",
        ".create_table(",
        ".create_namespace",
    )
    for path in Path("jobs/emr/src").rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert all(token not in source for token in forbidden), path
