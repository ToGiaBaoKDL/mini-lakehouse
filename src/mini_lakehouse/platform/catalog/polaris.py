"""Thin wrappers around the generated Apache Polaris SDK clients."""

import json
from collections.abc import Mapping
from contextlib import suppress
from typing import Any, Literal

from apache_polaris.sdk.catalog import (
    ApiClient as CatalogApiClient,
)
from apache_polaris.sdk.catalog import (
    Configuration as CatalogConfiguration,
)
from apache_polaris.sdk.catalog.api.o_auth2_api import OAuth2API
from apache_polaris.sdk.catalog.api.policy_api import PolicyAPI
from apache_polaris.sdk.catalog.exceptions import (
    ConflictException as CatalogConflictException,
)
from apache_polaris.sdk.catalog.exceptions import (
    NotFoundException as CatalogNotFoundException,
)
from apache_polaris.sdk.catalog.models import (
    ApplicablePolicy as PolarisPolicy,
)
from apache_polaris.sdk.catalog.models import (
    AttachPolicyRequest,
    CreatePolicyRequest,
    PolicyAttachmentTarget,
    PolicyIdentifier,
    UpdatePolicyRequest,
)
from apache_polaris.sdk.catalog.models import (
    Policy as SdkPolicy,
)
from apache_polaris.sdk.management import (
    ApiClient as ManagementApiClient,
)
from apache_polaris.sdk.management import (
    Configuration as ManagementConfiguration,
)
from apache_polaris.sdk.management.api.polaris_default_api import PolarisDefaultApi
from apache_polaris.sdk.management.exceptions import (
    NotFoundException as ManagementNotFoundException,
)
from apache_polaris.sdk.management.models import (
    AddGrantRequest,
    AwsStorageConfigInfo,
    CatalogGrant,
    CatalogPrivilege,
    CatalogProperties,
    CreateCatalogRequest,
    PolarisCatalog,
    UpdateCatalogRequest,
)
from apache_polaris.sdk.management.models import (
    Catalog as SdkCatalog,
)

from mini_lakehouse.config.settings import Settings, StorageSettings
from mini_lakehouse.contracts import TableIdentifier
from mini_lakehouse.contracts.maintenance import (
    MaintenancePolicy,
    PolicyTargetContract,
    policy_content_json,
)

_REQUEST_TIMEOUT_SECONDS = 15.0
_MAX_RETRIES = 12


def _internal_catalog(catalog: SdkCatalog) -> PolarisCatalog:
    if not isinstance(catalog, PolarisCatalog):
        raise RuntimeError(f"Polaris catalog {catalog.name!r} is not an internal catalog")
    if not isinstance(catalog.storage_config_info, AwsStorageConfigInfo):
        raise RuntimeError(
            f"Polaris catalog {catalog.name!r} does not use an S3 storage configuration"
        )
    return catalog


def policy_content_object(policy: SdkPolicy | PolarisPolicy) -> dict[str, Any]:
    if policy.content is None:
        raise ValueError(f"Policy {policy.name!r} has no content")
    value = json.loads(policy.content)
    if not isinstance(value, dict):
        raise ValueError(f"Policy {policy.name!r} content must be a JSON object")
    return value


def canonical_policy_content(policy: SdkPolicy | PolarisPolicy) -> str:
    if policy.content is None:
        return ""
    return json.dumps(policy_content_object(policy), sort_keys=True, separators=(",", ":"))


def _catalog_properties(payload: Mapping[str, object]) -> CatalogProperties:
    default_location = payload.get("default-base-location")
    if not isinstance(default_location, str):
        raise ValueError("Polaris catalog requires a string default-base-location")
    properties = CatalogProperties.from_dict(dict(payload))
    if properties is None:
        raise RuntimeError("Polaris SDK did not create catalog properties")
    return properties


class PolarisManagementClient:
    """Typed boundary around the generated Polaris management SDK."""

    def __init__(self, api: PolarisDefaultApi, storage: StorageSettings) -> None:
        self._api = api
        self._storage = storage

    def _storage_config(
        self,
        allowed_locations: tuple[str, ...],
    ) -> AwsStorageConfigInfo:
        return AwsStorageConfigInfo.model_validate(
            {
                "storageType": "S3",
                "allowedLocations": list(allowed_locations),
                "endpoint": self._storage.endpoints.external_url,
                "endpointInternal": self._storage.endpoints.internal_url,
                "pathStyleAccess": self._storage.path_style_access,
                "region": self._storage.region,
                "stsUnavailable": self._storage.sts_unavailable,
                "kmsUnavailable": self._storage.kms_unavailable,
            }
        )

    def load_catalog(self, catalog_name: str) -> PolarisCatalog | None:
        try:
            catalog = self._api.get_catalog(
                catalog_name,
                _request_timeout=_REQUEST_TIMEOUT_SECONDS,
            )
        except ManagementNotFoundException:
            return None
        return _internal_catalog(catalog)

    def create_catalog(
        self,
        name: str,
        catalog_type: Literal["INTERNAL"],
        properties: Mapping[str, object],
        allowed_locations: tuple[str, ...],
    ) -> PolarisCatalog:
        catalog = PolarisCatalog.model_validate(
            {
                "type": catalog_type,
                "name": name,
                "properties": _catalog_properties(properties),
                "storageConfigInfo": self._storage_config(allowed_locations),
            }
        )
        created = self._api.create_catalog(
            CreateCatalogRequest(catalog=catalog),
            _request_timeout=_REQUEST_TIMEOUT_SECONDS,
        )
        return _internal_catalog(created)

    def update_catalog(
        self,
        current: PolarisCatalog,
        properties: dict[str, str],
        allowed_locations: tuple[str, ...],
    ) -> PolarisCatalog:
        if current.entity_version is None:
            raise RuntimeError(
                f"Polaris catalog {current.name!r} has no entity version for an update"
            )
        request = UpdateCatalogRequest.model_validate(
            {
                "currentEntityVersion": current.entity_version,
                "properties": properties,
                "storageConfigInfo": self._storage_config(allowed_locations),
            }
        )
        updated = self._api.update_catalog(
            current.name,
            request,
            _request_timeout=_REQUEST_TIMEOUT_SECONDS,
        )
        return _internal_catalog(updated)

    def catalog_privileges(self, catalog_name: str, role: str) -> set[str]:
        resources = self._api.list_grants_for_catalog_role(
            catalog_name,
            role,
            _request_timeout=_REQUEST_TIMEOUT_SECONDS,
        )
        return {
            grant.privilege.value for grant in resources.grants if isinstance(grant, CatalogGrant)
        }

    def grant_catalog_privilege(
        self,
        catalog_name: str,
        role: str,
        privilege: str,
    ) -> None:
        self._api.add_grant_to_catalog_role(
            catalog_name,
            role,
            AddGrantRequest(
                grant=CatalogGrant(
                    type="catalog",
                    privilege=CatalogPrivilege(privilege),
                )
            ),
            _request_timeout=_REQUEST_TIMEOUT_SECONDS,
        )


class PolarisPolicyClient:
    """Typed boundary around the generated Polaris catalog-policy SDK."""

    def __init__(self, api: PolicyAPI, catalog_name: str) -> None:
        self._api = api
        self._catalog_name = catalog_name

    @staticmethod
    def _namespace(namespace: tuple[str, ...]) -> str:
        return "\x1f".join(namespace)

    def load_policy(self, namespace: tuple[str, ...], name: str) -> SdkPolicy | None:
        try:
            response = self._api.load_policy(
                self._catalog_name,
                self._namespace(namespace),
                name,
                _request_timeout=_REQUEST_TIMEOUT_SECONDS,
            )
        except CatalogNotFoundException:
            return None
        if response.policy is None:
            raise RuntimeError("Polaris did not return a policy object")
        return response.policy

    def list_policies(self, namespace: tuple[str, ...]) -> list[PolicyIdentifier]:
        identifiers: list[PolicyIdentifier] = []
        page_token: str | None = None
        seen_tokens: set[str] = set()
        while True:
            page = self._api.list_policies(
                self._catalog_name,
                self._namespace(namespace),
                page_token=page_token,
                _request_timeout=_REQUEST_TIMEOUT_SECONDS,
            )
            identifiers.extend(page.identifiers or ())
            page_token = page.next_page_token
            if page_token is None:
                return identifiers
            if page_token in seen_tokens:
                raise RuntimeError("Polaris returned a repeated policy page token")
            seen_tokens.add(page_token)

    def delete_policy(self, namespace: tuple[str, ...], name: str) -> None:
        with suppress(CatalogNotFoundException):
            self._api.drop_policy(
                self._catalog_name,
                self._namespace(namespace),
                name,
                detach_all=True,
                _request_timeout=_REQUEST_TIMEOUT_SECONDS,
            )

    def apply_policy(self, spec: MaintenancePolicy) -> Literal["created", "updated", "unchanged"]:
        desired_content = policy_content_json(spec)
        current = self.load_policy(spec.namespace, spec.name)
        if current is None:
            try:
                self._api.create_policy(
                    self._catalog_name,
                    self._namespace(spec.namespace),
                    CreatePolicyRequest(
                        name=spec.name,
                        type=spec.policy_type,
                        description=spec.description,
                        content=desired_content,
                    ),
                    _request_timeout=_REQUEST_TIMEOUT_SECONDS,
                )
                return "created"
            except CatalogConflictException:
                current = self.load_policy(spec.namespace, spec.name)
                if current is None:
                    raise RuntimeError(
                        f"Polaris policy {spec.name!r} disappeared after a concurrent create"
                    ) from None

        if current.policy_type != spec.policy_type:
            raise RuntimeError(
                f"Polaris policy {spec.name!r} has type {current.policy_type!r}; "
                f"expected {spec.policy_type!r}. Policy types are immutable."
            )
        for attempt in range(2):
            if (
                current.description == spec.description
                and canonical_policy_content(current) == desired_content
            ):
                return "unchanged"
            try:
                self._api.update_policy(
                    self._catalog_name,
                    self._namespace(spec.namespace),
                    spec.name,
                    UpdatePolicyRequest.model_validate(
                        {
                            "description": spec.description,
                            "content": desired_content,
                            "current-policy-version": current.version,
                        }
                    ),
                    _request_timeout=_REQUEST_TIMEOUT_SECONDS,
                )
                return "updated"
            except CatalogConflictException:
                if attempt == 1:
                    raise
                refreshed = self.load_policy(spec.namespace, spec.name)
                if refreshed is None:
                    raise RuntimeError(
                        f"Polaris policy {spec.name!r} disappeared during a concurrent update"
                    ) from None
                current = refreshed
        raise AssertionError("Policy update loop terminated unexpectedly")

    def attach_policy(
        self,
        spec: MaintenancePolicy,
        target: PolicyTargetContract,
    ) -> None:
        self._api.attach_policy(
            self._catalog_name,
            self._namespace(spec.namespace),
            spec.name,
            AttachPolicyRequest(
                target=PolicyAttachmentTarget(
                    type=target.type,
                    path=list(target.path),
                )
            ),
            _request_timeout=_REQUEST_TIMEOUT_SECONDS,
        )

    def _applicable_policies(
        self,
        namespace: tuple[str, ...],
        target_name: str | None = None,
    ) -> list[PolarisPolicy]:
        policies: list[PolarisPolicy] = []
        page_token: str | None = None
        seen_tokens: set[str] = set()
        while True:
            try:
                page = self._api.get_applicable_policies(
                    self._catalog_name,
                    page_token=page_token,
                    namespace=self._namespace(namespace),
                    target_name=target_name,
                    _request_timeout=_REQUEST_TIMEOUT_SECONDS,
                )
            except CatalogNotFoundException:
                return []
            policies.extend(page.applicable_policies)
            page_token = page.next_page_token
            if page_token is None:
                return policies
            if page_token in seen_tokens:
                raise RuntimeError("Polaris returned a repeated policy page token")
            seen_tokens.add(page_token)

    def applicable_policies(self, table: TableIdentifier) -> list[PolarisPolicy]:
        return self._applicable_policies(table.namespace, table.name)

    def policy_applies(
        self,
        spec: MaintenancePolicy,
        target: PolicyTargetContract,
    ) -> bool:
        if target.type == "table-like":
            policies = self._applicable_policies(target.path[:-1], target.path[-1])
        else:
            policies = self._applicable_policies(target.path)
        return any(
            not policy.inherited
            and tuple(policy.namespace) == spec.namespace
            and policy.name == spec.name
            and policy.policy_type == spec.policy_type
            for policy in policies
        )


class PolarisClients:
    """Own the authenticated generated SDK clients and their connection pools."""

    def __init__(self, settings: Settings) -> None:
        catalog_configuration = CatalogConfiguration(
            host=settings.polaris.uri.rstrip("/"),
            retries=_MAX_RETRIES,
        )
        self._catalog_api_client = CatalogApiClient(
            catalog_configuration,
            header_name="Polaris-Realm",
            header_value=settings.polaris.realm,
        )
        client_id, client_secret = settings.polaris.credential.get_secret_value().split(":", 1)
        token = (
            OAuth2API(self._catalog_api_client)
            .get_token(
                grant_type="client_credentials",
                scope=settings.polaris.scope,
                client_id=client_id,
                client_secret=client_secret,
                _request_timeout=10.0,
            )
            .access_token
        )
        catalog_configuration.access_token = token

        management_configuration = ManagementConfiguration(
            host=settings.polaris.management_uri.rstrip("/"),
            access_token=token,
            retries=_MAX_RETRIES,
        )
        self._management_api_client = ManagementApiClient(
            management_configuration,
            header_name="Polaris-Realm",
            header_value=settings.polaris.realm,
        )
        self.management = PolarisManagementClient(
            PolarisDefaultApi(self._management_api_client),
            settings.storage,
        )
        self.policies = PolarisPolicyClient(
            PolicyAPI(self._catalog_api_client),
            settings.polaris.catalog_name,
        )

    def __enter__(self) -> "PolarisClients":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def close(self) -> None:
        self._management_api_client.rest_client.pool_manager.clear()
        self._catalog_api_client.rest_client.pool_manager.clear()
