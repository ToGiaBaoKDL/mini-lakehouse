from mini_lakehouse.config.settings import Settings
from mini_lakehouse.platform.bootstrap import catalog_contract, namespace_contract


def test_namespace_contract_encodes_transport_and_domain_ownership() -> None:
    contract = namespace_contract(Settings())

    assert contract[("landing",)]["location"] == "s3://landing"
    assert contract[("landing", "api")]["transport"] == "api"
    assert contract[("curated", "github")]["owner"] == "data-platform"
    assert contract[("analytics", "engineering")]["owner"] == "engineering-analytics"


def test_catalog_contract_enables_bucket_root_namespaces() -> None:
    payload = catalog_contract(Settings())

    assert payload["properties"]["polaris.config.namespace-custom-location.enabled"] == "true"
    assert payload["storageConfigInfo"]["allowedLocations"] == [
        "s3://landing",
        "s3://curated",
        "s3://analytics",
    ]
