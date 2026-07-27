from mini_lakehouse.config.settings import Settings
from mini_lakehouse.contracts import load_contracts
from mini_lakehouse.platform.desired_state import DesiredPlatformState, compile_desired_state
from mini_lakehouse.platform.runtime import source_table_storage_uri


def _state(settings: Settings | None = None) -> DesiredPlatformState:
    return compile_desired_state(settings or Settings(), load_contracts())


def test_namespace_contract_separates_lifecycle_and_domain_ownership() -> None:
    state = _state()
    contract = {namespace.path: namespace.iceberg_properties() for namespace in state.namespaces}

    assert contract[("landing",)]["location"] == "s3://landing"
    assert not any(path[:2] == ("landing", "api") for path in contract)
    assert contract[("curated", "github")]["owner"] == "data-platform"
    assert contract[("curated", "github")]["location"] == "s3://curated/github/"
    assert ("curated", "github", "internal") not in contract
    assert contract[("analytics", "engineering")]["owner"] == "engineering-analytics"
    assert contract[("analytics", "engineering")]["location"] == "s3://analytics/engineering/"
    assert contract[("analytics", "engineering")]["business_domain"] == "engineering"
    assert contract[("analytics", "engineering")]["business_owner"] == "engineering"


def test_catalog_contract_enables_bucket_root_namespaces() -> None:
    payload = _state().catalog.management_payload()

    assert payload["properties"]["polaris.config.namespace-custom-location.enabled"] == "true"
    assert payload["storageConfigInfo"]["allowedLocations"] == [
        "s3://landing/_catalog",
        "s3://landing",
        "s3://curated",
        "s3://analytics",
    ]


def test_catalog_contract_uses_the_configured_data_plane_endpoint() -> None:
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

    payload = _state(settings).catalog.management_payload()

    assert payload["storageConfigInfo"]["endpoint"] == "https://storage.example.com"
    assert payload["storageConfigInfo"]["endpointInternal"] == ("http://object-store:9000")


def test_catalog_contract_omits_custom_endpoints_for_native_s3() -> None:
    settings = Settings.model_validate(
        {
            "storage": {
                "endpoints": {
                    "external_url": None,
                    "internal_url": None,
                }
            }
        }
    )

    storage = _state(settings).catalog.management_payload()["storageConfigInfo"]

    assert "endpoint" not in storage
    assert "endpointInternal" not in storage


def test_catalog_contract_deduplicates_shared_allowed_locations() -> None:
    settings = Settings.model_validate(
        {
            "storage": {
                "landing_uri": "s3://shared",
                "curated_uri": "s3://shared",
                "analytics_uri": "s3://shared",
            }
        }
    )

    payload = _state(settings).catalog.management_payload()

    assert payload["storageConfigInfo"]["allowedLocations"] == [
        "s3://shared/_catalog",
        "s3://shared",
    ]


def test_landing_table_locations_follow_source_ownership_not_shared_namespace_root() -> None:
    settings = Settings()
    contracts = load_contracts()

    assert (
        source_table_storage_uri(
            settings,
            contracts.source("arxiv"),
            "oai_records_raw",
        )
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
