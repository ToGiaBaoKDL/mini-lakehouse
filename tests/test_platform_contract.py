from mini_lakehouse.config.settings import Settings
from mini_lakehouse.contracts import load_contracts
from mini_lakehouse.platform.catalog import catalog_contract
from mini_lakehouse.platform.namespaces import namespace_contract
from mini_lakehouse.platform.runtime import source_table_storage_uri


def test_namespace_contract_separates_lifecycle_and_domain_ownership() -> None:
    contract = namespace_contract(Settings())

    assert contract[("landing",)]["location"] == "s3://landing"
    assert not any(path[:2] == ("landing", "api") for path in contract)
    assert contract[("curated", "github")]["owner"] == "data-platform"
    assert contract[("curated", "github")]["location"] == "s3://curated/github/"
    assert ("curated", "github", "internal") not in contract
    assert contract[("analytics", "engineering")]["owner"] == "engineering-analytics"
    assert contract[("analytics", "engineering")]["location"] == "s3://analytics/engineering/"


def test_catalog_contract_enables_bucket_root_namespaces() -> None:
    payload = catalog_contract(Settings())

    assert payload["properties"]["polaris.config.namespace-custom-location.enabled"] == "true"
    assert payload["storageConfigInfo"]["allowedLocations"] == [
        "s3://landing/_catalog",
        "s3://landing",
        "s3://curated",
        "s3://analytics",
    ]


def test_catalog_contract_uses_the_configured_data_plane_endpoint() -> None:
    settings = Settings.model_validate({"storage": {"endpoint_url": "http://object-store:9000"}})

    payload = catalog_contract(settings)

    assert payload["storageConfigInfo"]["endpoint"] == "http://object-store:9000"
    assert payload["storageConfigInfo"]["endpointInternal"] == ("http://object-store:9000")


def test_landing_table_locations_follow_source_ownership_not_shared_namespace_root() -> None:
    settings = Settings()
    contracts = load_contracts()

    assert source_table_storage_uri(
        settings,
        contracts.source("arxiv"),
        "oai_records_raw",
    ) == "s3://landing/api/arxiv/tables/oai_records_raw"
    assert source_table_storage_uri(
        settings,
        contracts.source("github_archive"),
        "events_raw",
    ) == "s3://landing/api/github_archive/tables/events_raw"
