from typing import cast
from unittest.mock import call, create_autospec

import pytest
from apache_polaris.sdk.catalog.api.policy_api import PolicyAPI
from apache_polaris.sdk.catalog.models import (
    ApplicablePolicy,
    GetApplicablePoliciesResponse,
    LoadPolicyResponse,
    Policy,
    PolicyIdentifier,
)
from apache_polaris.sdk.management.api.polaris_default_api import PolarisDefaultApi
from apache_polaris.sdk.management.exceptions import NotFoundException
from apache_polaris.sdk.management.models import (
    AwsStorageConfigInfo,
    CatalogGrant,
    CatalogProperties,
    CatalogRole,
    CatalogRoles,
    GrantResource,
    GrantResources,
    NamespaceGrant,
    PolarisCatalog,
    Principal,
    PrincipalRole,
    PrincipalRoles,
    Principals,
)

from mini_lakehouse.config.settings import Settings
from mini_lakehouse.contracts import load_contracts
from mini_lakehouse.contracts.access import AccessContract, CatalogRoleContract
from mini_lakehouse.contracts.maintenance import policy_content_json
from mini_lakehouse.platform.catalog.access import (
    bootstrap_access,
    rotate_credentials,
    validate_access,
)
from mini_lakehouse.platform.catalog.admin import (
    require_valid,
    validation_payload,
)
from mini_lakehouse.platform.catalog.catalogs import bootstrap_catalog
from mini_lakehouse.platform.catalog.layout import (
    catalog_allowed_locations,
    catalog_properties,
)
from mini_lakehouse.platform.catalog.policies import PolarisPolicyClient
from mini_lakehouse.platform.catalog.policy_prune import (
    PolicyPruneItem,
    apply_policy_prune_plan,
    build_policy_prune_plan,
    plan_payload,
)


def _sdk_catalog(settings: Settings) -> PolarisCatalog:
    contracts = load_contracts()
    properties = CatalogProperties.from_dict(catalog_properties(settings, contracts))
    assert properties is not None
    storage = AwsStorageConfigInfo.model_validate(
        {
            "storageType": "S3",
            "allowedLocations": list(catalog_allowed_locations(settings, contracts)),
            "endpoint": settings.storage.endpoints.external_url,
            "endpointInternal": settings.storage.endpoints.internal_url,
            "pathStyleAccess": settings.storage.path_style_access,
            "region": settings.storage.region,
            "stsUnavailable": settings.storage.sts_unavailable,
            "kmsUnavailable": settings.storage.kms_unavailable,
        }
    )
    return PolarisCatalog.model_validate(
        {
            "type": "INTERNAL",
            "name": contracts.platform.catalog.name,
            "properties": properties,
            "storageConfigInfo": storage,
            "entityVersion": 7,
        }
    )


def _sdk_policy(spec_index: int = 0, *, description: str | None = None) -> Policy:
    spec = load_contracts().policies[spec_index]
    return Policy.model_validate(
        {
            "name": spec.name,
            "policy-type": spec.policy_type,
            "description": spec.description if description is None else description,
            "content": policy_content_json(spec),
            "version": 7,
            "inheritable": True,
        }
    )


def _sdk_grants(role: CatalogRoleContract) -> list[GrantResource]:
    grants: list[GrantResource] = []
    for grant in role.grants:
        for privilege in grant.privileges:
            if grant.type == "namespace":
                grants.append(
                    NamespaceGrant.model_validate(
                        {
                            "type": grant.type,
                            "privilege": privilege.value,
                            "namespace": list(grant.namespace),
                        }
                    )
                )
            else:
                grants.append(
                    CatalogGrant.model_validate(
                        {
                            "type": grant.type,
                            "privilege": privilege.value,
                        }
                    )
                )
    return grants


def test_bootstrap_catalog_is_a_noop_when_current_state_matches() -> None:
    settings = Settings()
    contracts = load_contracts()
    api = create_autospec(PolarisDefaultApi, instance=True)
    api.get_catalog.return_value = _sdk_catalog(settings)

    bootstrap_catalog(api, settings, contracts)

    api.create_catalog.assert_not_called()
    api.update_catalog.assert_not_called()


def test_bootstrap_catalog_updates_safe_drift() -> None:
    settings = Settings()
    contracts = load_contracts()
    api = create_autospec(PolarisDefaultApi, instance=True)
    current = _sdk_catalog(settings)
    current.properties.additional_properties["owner"] = "stale"
    api.get_catalog.return_value = current

    bootstrap_catalog(api, settings, contracts)

    api.update_catalog.assert_called_once()
    request = api.update_catalog.call_args.args[1]
    assert request.current_entity_version == current.entity_version
    assert request.properties == catalog_properties(settings, contracts)


def test_bootstrap_catalog_requires_explicit_migration_for_type_drift() -> None:
    settings = Settings()
    api = create_autospec(PolarisDefaultApi, instance=True)
    current = create_autospec(PolarisCatalog, instance=True)
    current.name = "prod"
    current.type = "EXTERNAL"
    current.properties = _sdk_catalog(settings).properties
    current.storage_config_info = _sdk_catalog(settings).storage_config_info
    api.get_catalog.return_value = current

    with pytest.raises(RuntimeError, match="explicit migration: type"):
        bootstrap_catalog(api, settings, load_contracts())

    api.update_catalog.assert_not_called()


def test_management_sdk_boundary_builds_typed_catalog_requests() -> None:
    settings = Settings()
    contracts = load_contracts()
    api = create_autospec(PolarisDefaultApi, instance=True)
    api.get_catalog.side_effect = NotFoundException()
    api.create_catalog.return_value = _sdk_catalog(settings)

    bootstrap_catalog(api, settings, contracts)

    request = api.create_catalog.call_args.args[0]
    assert request.catalog.name == "prod"
    assert request.catalog.properties.to_dict() == catalog_properties(settings, contracts)
    storage = request.catalog.storage_config_info
    assert isinstance(storage, AwsStorageConfigInfo)
    assert storage.endpoint == "http://localhost:9000"
    assert storage.endpoint_internal == "http://object-store:9000"


def test_access_validation_reports_unexpected_privileges() -> None:
    contracts = load_contracts()
    role = contracts.access.catalog_roles[0]
    contracts = contracts.model_copy(
        update={
            "access": AccessContract(
                version=1,
                catalog_roles=(role,),
            )
        }
    )
    api = create_autospec(PolarisDefaultApi, instance=True)
    api.list_catalog_roles.return_value = CatalogRoles(roles=[CatalogRole(name=role.name)])
    grants = _sdk_grants(role)
    grants.append(
        CatalogGrant.model_validate({"type": "catalog", "privilege": "CATALOG_MANAGE_ACCESS"})
    )
    api.list_grants_for_catalog_role.return_value = GrantResources(grants=grants)

    errors = validate_access(api, contracts)

    assert errors == (f"grant:{role.name}:catalog::CATALOG_MANAGE_ACCESS:unexpected",)


def test_access_bootstrap_provisions_a_missing_service_credential_once() -> None:
    contracts = load_contracts()
    identity = contracts.access.service_identities[0]
    role = contracts.access.catalog_roles[0]
    contracts = contracts.model_copy(
        update={
            "access": AccessContract(
                version=1,
                service_identities=(identity,),
                catalog_roles=(role,),
            )
        }
    )
    api = create_autospec(PolarisDefaultApi, instance=True)
    api.list_catalog_roles.return_value = CatalogRoles(roles=[CatalogRole(name=role.name)])
    api.list_grants_for_catalog_role.return_value = GrantResources(grants=_sdk_grants(role))
    api.list_principal_roles.return_value = PrincipalRoles(
        roles=[PrincipalRole(name=identity.name)]
    )
    api.list_principals.return_value = Principals(principals=[])
    api.list_principal_roles_assigned.return_value = PrincipalRoles(
        roles=[PrincipalRole(name=identity.name)]
    )
    api.list_catalog_roles_for_principal_role.return_value = CatalogRoles(
        roles=[CatalogRole(name=role.name)]
    )
    settings = Settings.model_validate(
        {
            "platform_admin": {
                "service_secrets": {
                    identity.name: "prefect-secret",
                }
            }
        }
    )

    bootstrap_access(api, settings, contracts)

    request = api.reset_credentials.call_args.args[1]
    assert request.client_id == identity.name
    assert request.client_secret == "prefect-secret"
    api.update_principal.assert_not_called()


def test_access_rotation_is_explicit() -> None:
    contracts = load_contracts()
    api = create_autospec(PolarisDefaultApi, instance=True)
    identity = contracts.access.service_identities[1]
    current = Principal.model_validate(
        {
            "name": identity.name,
            "clientId": identity.name,
        }
    )
    api.list_principals.return_value = Principals(principals=[current])
    settings = Settings.model_validate(
        {
            "platform_admin": {
                "service_secrets": {
                    principal.name: f"{principal.name}-secret"
                    for principal in contracts.access.service_identities
                }
            }
        }
    )

    rotate_credentials(api, settings, contracts, (identity.name,))

    request = api.reset_credentials.call_args.args[1]
    assert request.client_id == identity.name
    assert request.client_secret == f"{identity.name}-secret"


def test_policy_sdk_boundary_does_not_update_unchanged_content() -> None:
    spec = load_contracts().policies[0]
    api = create_autospec(PolicyAPI, instance=True)
    api.load_policy.return_value = LoadPolicyResponse(policy=_sdk_policy())
    client = PolarisPolicyClient(api, "prod")

    client.apply_policy(spec)
    api.create_policy.assert_not_called()
    api.update_policy.assert_not_called()


def test_policy_sdk_boundary_updates_with_server_version() -> None:
    spec = load_contracts().policies[0]
    api = create_autospec(PolicyAPI, instance=True)
    api.load_policy.return_value = LoadPolicyResponse(policy=_sdk_policy(description="stale"))
    client = PolarisPolicyClient(api, "prod")

    client.apply_policy(spec)
    request = api.update_policy.call_args.args[3]
    assert request.current_policy_version == 7
    assert request.description == spec.description


def test_policy_mapping_uses_typed_sdk_target_and_applicable_query() -> None:
    spec = load_contracts().policies[0]
    target = spec.targets[0]
    api = create_autospec(PolicyAPI, instance=True)
    applicable = ApplicablePolicy.model_validate(
        {
            **_sdk_policy().model_dump(by_alias=True),
            "inherited": False,
            "namespace": list(spec.namespace),
        }
    )
    api.get_applicable_policies.return_value = GetApplicablePoliciesResponse.model_validate(
        {"applicable-policies": [applicable]}
    )
    client = PolarisPolicyClient(api, "prod")

    client.attach_policy(spec, target)
    assert client.policy_applies(spec, target)

    request = api.attach_policy.call_args.args[3]
    assert request.target.type == target.type
    assert request.target.path == list(target.path)
    assert api.get_applicable_policies.call_args.kwargs["namespace"] == "\x1f".join(target.path)


def test_policy_mapping_requires_the_exact_direct_attachment() -> None:
    spec = load_contracts().policies[0]
    target = spec.targets[0]
    api = create_autospec(PolicyAPI, instance=True)
    inherited = ApplicablePolicy.model_validate(
        {
            **_sdk_policy().model_dump(by_alias=True),
            "inherited": True,
            "namespace": list(spec.namespace),
        }
    )
    api.get_applicable_policies.return_value = GetApplicablePoliciesResponse.model_validate(
        {"applicable-policies": [inherited]}
    )
    client = PolarisPolicyClient(api, "prod")

    assert not client.policy_applies(spec, target)


def test_policy_prune_remains_an_explicit_destructive_operation() -> None:
    contracts = load_contracts()
    desired = contracts.policies[0]
    client = create_autospec(PolarisPolicyClient, instance=True)
    client.list_policies.return_value = [
        PolicyIdentifier(namespace=list(desired.namespace), name=desired.name),
        PolicyIdentifier(namespace=["analytics"], name="mlh-stale-policy"),
        PolicyIdentifier(namespace=["curated"], name="team-owned-policy"),
    ]

    plan = build_policy_prune_plan(client, contracts)
    payload = plan_payload(plan)

    assert plan == (PolicyPruneItem(("analytics",), "mlh-stale-policy"),)
    assert len(cast(str, payload["plan_sha256"])) == 64
    apply_policy_prune_plan(client, plan)
    assert client.delete_policy.call_args_list == [call(("analytics",), "mlh-stale-policy")]


def test_live_validation_is_read_only_and_fails_with_all_drift() -> None:
    errors = ("catalog:prod:missing", "table:curated.github.events:schema")

    assert validation_payload(errors) == {"valid": False, "errors": list(errors)}
    with pytest.raises(RuntimeError, match="catalog:prod:missing"):
        require_valid(errors)
