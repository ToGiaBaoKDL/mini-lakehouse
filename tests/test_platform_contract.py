from mini_lakehouse.config.settings import Settings
from mini_lakehouse.platform.catalog import catalog_contract
from mini_lakehouse.platform.namespaces import namespace_contract


def test_namespace_contract_separates_lifecycle_and_domain_ownership() -> None:
    contract = namespace_contract(Settings())

    assert contract[("landing",)]["location"] == "s3://landing"
    assert not any(path[:2] == ("landing", "api") for path in contract)
    assert contract[("curated", "github")]["owner"] == "data-platform"
    assert ("curated", "github", "internal") not in contract
    assert contract[("analytics", "engineering")]["owner"] == "engineering-analytics"


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
