from collections.abc import Sequence

from apache_polaris.sdk.management.api.polaris_default_api import PolarisDefaultApi
from apache_polaris.sdk.management.models import (
    AddGrantRequest,
    CatalogGrant,
    CatalogRole,
    CreateCatalogRoleRequest,
    CreatePrincipalRequest,
    CreatePrincipalRoleRequest,
    GrantCatalogRoleRequest,
    GrantPrincipalRoleRequest,
    NamespaceGrant,
    Principal,
    PrincipalRole,
    ResetPrincipalRequest,
)
from pydantic import SecretStr

from mini_lakehouse.config.settings import Settings
from mini_lakehouse.contracts import PlatformContracts
from mini_lakehouse.contracts.access import CatalogRoleContract, NamespaceGrantContract

_REQUEST_TIMEOUT_SECONDS = 15.0

type GrantKey = tuple[str, tuple[str, ...], str]


def _catalog_roles(api: PolarisDefaultApi, catalog_name: str) -> set[str]:
    return {
        role.name
        for role in api.list_catalog_roles(
            catalog_name,
            _request_timeout=_REQUEST_TIMEOUT_SECONDS,
        ).roles
    }


def _principal_roles(api: PolarisDefaultApi) -> set[str]:
    return {
        role.name
        for role in api.list_principal_roles(
            _request_timeout=_REQUEST_TIMEOUT_SECONDS,
        ).roles
    }


def _principals(api: PolarisDefaultApi) -> dict[str, Principal]:
    return {
        principal.name: principal
        for principal in api.list_principals(
            _request_timeout=_REQUEST_TIMEOUT_SECONDS,
        ).principals
    }


def _assigned_principal_roles(api: PolarisDefaultApi, principal: str) -> set[str]:
    return {
        role.name
        for role in api.list_principal_roles_assigned(
            principal,
            _request_timeout=_REQUEST_TIMEOUT_SECONDS,
        ).roles
    }


def _assigned_catalog_roles(
    api: PolarisDefaultApi,
    principal_role: str,
    catalog_name: str,
) -> set[str]:
    return {
        role.name
        for role in api.list_catalog_roles_for_principal_role(
            principal_role,
            catalog_name,
            _request_timeout=_REQUEST_TIMEOUT_SECONDS,
        ).roles
    }


def _service_secrets(
    settings: Settings,
    contracts: PlatformContracts,
) -> dict[str, SecretStr]:
    expected = {identity.name for identity in contracts.access.service_identities}
    configured = settings.platform_admin.service_secrets
    if set(configured) != expected:
        missing = sorted(expected - set(configured))
        unexpected = sorted(set(configured) - expected)
        raise RuntimeError(
            "Polaris service secrets do not match access.yaml: "
            f"missing={missing!r}, unexpected={unexpected!r}"
        )
    return configured


def _grants(
    api: PolarisDefaultApi,
    catalog_name: str,
    role_name: str,
) -> set[GrantKey]:
    resources = api.list_grants_for_catalog_role(
        catalog_name,
        role_name,
        _request_timeout=_REQUEST_TIMEOUT_SECONDS,
    )
    result: set[GrantKey] = set()
    for grant in resources.grants:
        if isinstance(grant, CatalogGrant):
            result.add(("catalog", (), grant.privilege.value))
        elif isinstance(grant, NamespaceGrant):
            result.add(("namespace", tuple(grant.namespace), grant.privilege.value))
    return result


def _desired_grants(role: CatalogRoleContract) -> set[GrantKey]:
    return {
        (
            grant.type,
            grant.namespace if isinstance(grant, NamespaceGrantContract) else (),
            privilege.value,
        )
        for grant in role.grants
        for privilege in grant.privileges
    }


def _grant_missing(
    api: PolarisDefaultApi,
    catalog_name: str,
    role: CatalogRoleContract,
) -> None:
    current = _grants(api, catalog_name, role.name)
    for grant in role.grants:
        if isinstance(grant, NamespaceGrantContract):
            for privilege in grant.privileges:
                if (grant.type, grant.namespace, privilege.value) in current:
                    continue
                resource = NamespaceGrant(
                    type="namespace",
                    namespace=list(grant.namespace),
                    privilege=privilege,
                )
                api.add_grant_to_catalog_role(
                    catalog_name,
                    role.name,
                    AddGrantRequest(grant=resource),
                    _request_timeout=_REQUEST_TIMEOUT_SECONDS,
                )
        else:
            for privilege in grant.privileges:
                if (grant.type, (), privilege.value) in current:
                    continue
                api.add_grant_to_catalog_role(
                    catalog_name,
                    role.name,
                    AddGrantRequest(grant=CatalogGrant(type="catalog", privilege=privilege)),
                    _request_timeout=_REQUEST_TIMEOUT_SECONDS,
                )


def _assign_missing(
    api: PolarisDefaultApi,
    catalog_name: str,
    identity_name: str,
    desired_catalog_roles: tuple[str, ...],
) -> None:
    principal_roles = _assigned_principal_roles(api, identity_name)
    if identity_name not in principal_roles:
        api.assign_principal_role(
            identity_name,
            GrantPrincipalRoleRequest(
                principalRole=PrincipalRole(name=identity_name),
            ),
            _request_timeout=_REQUEST_TIMEOUT_SECONDS,
        )

    catalog_roles = _assigned_catalog_roles(api, identity_name, catalog_name)
    for role_name in desired_catalog_roles:
        if role_name not in catalog_roles:
            api.assign_catalog_role_to_principal_role(
                identity_name,
                catalog_name,
                GrantCatalogRoleRequest(
                    catalogRole=CatalogRole(name=role_name),
                ),
                _request_timeout=_REQUEST_TIMEOUT_SECONDS,
            )


def bootstrap_access(
    api: PolarisDefaultApi,
    settings: Settings,
    contracts: PlatformContracts,
) -> None:
    catalog_name = contracts.platform.catalog.name
    secrets = _service_secrets(settings, contracts)

    catalog_roles = _catalog_roles(api, catalog_name)
    for role in contracts.access.catalog_roles:
        if role.name not in catalog_roles:
            api.create_catalog_role(
                catalog_name,
                CreateCatalogRoleRequest(
                    catalogRole=CatalogRole(name=role.name),
                ),
                _request_timeout=_REQUEST_TIMEOUT_SECONDS,
            )
        _grant_missing(api, catalog_name, role)

    principal_roles = _principal_roles(api)
    principals = _principals(api)
    for identity in contracts.access.service_identities:
        if identity.name not in principal_roles:
            api.create_principal_role(
                CreatePrincipalRoleRequest(
                    principalRole=PrincipalRole(name=identity.name),
                ),
                _request_timeout=_REQUEST_TIMEOUT_SECONDS,
            )
        if identity.name not in principals:
            api.create_principal(
                CreatePrincipalRequest(
                    principal=Principal(
                        name=identity.name,
                        clientId=identity.name,
                    ),
                    credentialRotationRequired=False,
                ),
                _request_timeout=_REQUEST_TIMEOUT_SECONDS,
            )
            api.reset_credentials(
                identity.name,
                ResetPrincipalRequest(
                    clientId=identity.name,
                    clientSecret=secrets[identity.name].get_secret_value(),
                ),
                _request_timeout=_REQUEST_TIMEOUT_SECONDS,
            )
        elif principals[identity.name].client_id != identity.name:
            raise RuntimeError(
                f"Polaris principal {identity.name!r} uses client ID "
                f"{principals[identity.name].client_id!r}; rotate it explicitly"
            )
        _assign_missing(
            api,
            catalog_name,
            identity.name,
            identity.catalog_roles,
        )


def rotate_credentials(
    api: PolarisDefaultApi,
    settings: Settings,
    contracts: PlatformContracts,
    identities: Sequence[str] = (),
) -> None:
    secrets = _service_secrets(settings, contracts)
    selected = tuple(identities) or tuple(sorted(secrets))
    unknown = sorted(set(selected) - set(secrets))
    if unknown:
        raise ValueError(f"Unknown Polaris service identities: {unknown!r}")
    missing = sorted(set(selected) - set(_principals(api)))
    if missing:
        raise RuntimeError(f"Polaris principals do not exist: {missing!r}")
    for name in selected:
        api.reset_credentials(
            name,
            ResetPrincipalRequest(
                clientId=name,
                clientSecret=secrets[name].get_secret_value(),
            ),
            _request_timeout=_REQUEST_TIMEOUT_SECONDS,
        )


def validate_access(
    api: PolarisDefaultApi,
    contracts: PlatformContracts,
) -> tuple[str, ...]:
    errors: list[str] = []
    catalog_name = contracts.platform.catalog.name
    catalog_roles = _catalog_roles(api, catalog_name)
    for role in contracts.access.catalog_roles:
        if role.name not in catalog_roles:
            errors.append(f"catalog_role:{role.name}:missing")
            continue
        current = _grants(api, catalog_name, role.name)
        desired = _desired_grants(role)
        errors.extend(
            f"grant:{role.name}:{grant_type}:{'.'.join(path)}:{privilege}:missing"
            for grant_type, path, privilege in sorted(desired - current)
        )
        errors.extend(
            f"grant:{role.name}:{grant_type}:{'.'.join(path)}:{privilege}:unexpected"
            for grant_type, path, privilege in sorted(current - desired)
        )

    principal_roles = _principal_roles(api)
    principals = _principals(api)
    for identity in contracts.access.service_identities:
        principal = principals.get(identity.name)
        if principal is None:
            errors.append(f"principal:{identity.name}:missing")
            continue
        if principal.client_id != identity.name:
            errors.append(f"principal:{identity.name}:client_id")
        if identity.name not in principal_roles:
            errors.append(f"principal_role:{identity.name}:missing")
            continue

        assigned_principal_roles = _assigned_principal_roles(api, identity.name)
        if identity.name not in assigned_principal_roles:
            errors.append(f"principal_role_assignment:{identity.name}:{identity.name}:missing")
        errors.extend(
            f"principal_role_assignment:{identity.name}:{role_name}:unexpected"
            for role_name in sorted(assigned_principal_roles - {identity.name})
        )

        assigned_catalog_roles = _assigned_catalog_roles(
            api,
            identity.name,
            catalog_name,
        )
        errors.extend(
            f"catalog_role_assignment:{identity.name}:{role_name}:missing"
            for role_name in identity.catalog_roles
            if role_name not in assigned_catalog_roles
        )
        errors.extend(
            f"catalog_role_assignment:{identity.name}:{role_name}:unexpected"
            for role_name in sorted(assigned_catalog_roles - set(identity.catalog_roles))
        )

    return tuple(errors)
