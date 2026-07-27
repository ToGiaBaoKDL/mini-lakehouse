from mini_lakehouse.config.settings import Settings
from mini_lakehouse.contracts import load_contracts
from mini_lakehouse.platform.catalog.layout import (
    catalog_allowed_locations,
    catalog_properties,
    managed_tables,
    namespace_storage_uri,
    source_table_storage_uri,
)


def test_namespace_contract_separates_lifecycle_and_domain_ownership() -> None:
    settings = Settings()
    contracts = load_contracts()
    namespaces = {
        namespace.path: namespace.iceberg_properties(
            namespace_storage_uri(settings, namespace.path)
        )
        for namespace in contracts.managed_namespaces()
    }

    assert namespaces[("landing",)]["location"] == "s3://landing"
    assert not any(path[:2] == ("landing", "api") for path in namespaces)
    assert namespaces[("curated", "github")]["owner"] == "data-platform"
    assert namespaces[("curated", "github")]["location"] == "s3://curated/github/"
    assert ("curated", "github", "internal") not in namespaces
    assert namespaces[("analytics", "engineering")]["owner"] == "engineering-analytics"
    assert namespaces[("analytics", "engineering")]["location"] == "s3://analytics/engineering/"
    assert namespaces[("analytics", "engineering")]["business_domain"] == "engineering"


def test_catalog_contract_enables_bucket_root_namespaces() -> None:
    settings = Settings()
    contracts = load_contracts()

    assert (
        catalog_properties(settings, contracts)["polaris.config.namespace-custom-location.enabled"]
        == "true"
    )
    assert catalog_allowed_locations(settings, contracts) == (
        "s3://landing/_catalog",
        "s3://landing",
        "s3://curated",
        "s3://analytics",
    )


def test_catalog_storage_uses_both_configured_data_plane_endpoints() -> None:
    settings = Settings.model_validate(
        {
            "storage": {
                "endpoints": {
                    "external_url": "https://storage.example.com",
                    "internal_url": "http://object-store:9000",
                }
            }
        }
    )

    assert settings.storage.endpoints.external_url == "https://storage.example.com"
    assert settings.storage.endpoints.internal_url == "http://object-store:9000"


def test_catalog_allowed_locations_are_deduplicated() -> None:
    settings = Settings.model_validate(
        {
            "storage": {
                "landing_uri": "s3://shared",
                "curated_uri": "s3://shared",
                "analytics_uri": "s3://shared",
            }
        }
    )

    assert catalog_allowed_locations(settings, load_contracts()) == (
        "s3://shared/_catalog",
        "s3://shared",
    )


def test_managed_tables_compile_once_without_a_desired_state_copy() -> None:
    settings = Settings()
    contracts = load_contracts()
    bindings = list(managed_tables(settings, contracts))
    expected = sum(len(source.tables) for source in contracts.sources) + sum(
        len(product.tables) for product in contracts.curated
    )

    assert len(bindings) == expected
    assert len({identifier.iceberg for identifier, _, _ in bindings}) == expected
    assert len({location for _, location, _ in bindings}) == expected


def test_landing_table_locations_follow_source_ownership() -> None:
    settings = Settings()
    contracts = load_contracts()

    assert (
        source_table_storage_uri(settings, contracts.source("arxiv"), "oai_records_raw")
        == "s3://landing/api/arxiv/tables/oai_records_raw"
    )
    assert (
        source_table_storage_uri(
            settings,
            contracts.source("github_archive"),
            "events_raw",
        )
        == "s3://landing/api/github_archive/tables/events_raw"
    )
