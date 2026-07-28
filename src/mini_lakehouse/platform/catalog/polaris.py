"""Authenticated lifecycle for the generated Apache Polaris SDK clients."""

import apache_polaris.sdk.catalog
import apache_polaris.sdk.management
from apache_polaris.sdk.catalog.api.o_auth2_api import OAuth2API
from apache_polaris.sdk.catalog.api.policy_api import PolicyAPI
from apache_polaris.sdk.management.api.polaris_default_api import PolarisDefaultApi

from mini_lakehouse.config import Settings
from mini_lakehouse.platform.catalog.policies import PolarisPolicyClient

_MAX_RETRIES = 12
_TOKEN_TIMEOUT_SECONDS = 10.0


class PolarisClients:
    """Own shared authentication and connection pools for official SDK clients."""

    def __init__(self, settings: Settings) -> None:
        catalog_configuration = apache_polaris.sdk.catalog.Configuration(
            host=settings.polaris.uri.rstrip("/"),
            retries=_MAX_RETRIES,
        )
        self._catalog_client = apache_polaris.sdk.catalog.ApiClient(
            catalog_configuration,
            header_name="Polaris-Realm",
            header_value=settings.polaris.realm,
        )
        token = (
            OAuth2API(self._catalog_client)
            .get_token(
                grant_type="client_credentials",
                scope=settings.polaris.scope,
                client_id=settings.polaris.client_id,
                client_secret=settings.polaris.client_secret.get_secret_value(),
                _request_timeout=_TOKEN_TIMEOUT_SECONDS,
            )
            .access_token
        )
        catalog_configuration.access_token = token

        management_configuration = apache_polaris.sdk.management.Configuration(
            host=settings.polaris.management_uri.rstrip("/"),
            access_token=token,
            retries=_MAX_RETRIES,
        )
        self._management_client = apache_polaris.sdk.management.ApiClient(
            management_configuration,
            header_name="Polaris-Realm",
            header_value=settings.polaris.realm,
        )
        self.management = PolarisDefaultApi(self._management_client)
        self.policies = PolarisPolicyClient(
            PolicyAPI(self._catalog_client),
            settings.polaris.catalog_name,
        )

    def __enter__(self) -> "PolarisClients":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def close(self) -> None:
        self._management_client.rest_client.pool_manager.clear()
        self._catalog_client.rest_client.pool_manager.clear()
