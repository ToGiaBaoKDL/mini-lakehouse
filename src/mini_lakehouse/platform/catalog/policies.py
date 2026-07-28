import json
from contextlib import suppress
from typing import Any

from apache_polaris.sdk.catalog.api.policy_api import PolicyAPI
from apache_polaris.sdk.catalog.exceptions import NotFoundException
from apache_polaris.sdk.catalog.models import (
    ApplicablePolicy,
    AttachPolicyRequest,
    CreatePolicyRequest,
    Policy,
    PolicyAttachmentTarget,
    PolicyIdentifier,
    UpdatePolicyRequest,
)

from mini_lakehouse.contracts import PlatformContracts, TableIdentifier
from mini_lakehouse.contracts.maintenance import (
    MaintenancePolicy,
    PolicyTargetContract,
    policy_content_json,
)

_REQUEST_TIMEOUT_SECONDS = 15.0


def policy_content_object(policy: Policy | ApplicablePolicy) -> dict[str, Any]:
    if policy.content is None:
        raise ValueError(f"Policy {policy.name!r} has no content")
    value = json.loads(policy.content)
    if not isinstance(value, dict):
        raise ValueError(f"Policy {policy.name!r} content must be a JSON object")
    return value


def canonical_policy_content(policy: Policy | ApplicablePolicy) -> str:
    return json.dumps(policy_content_object(policy), sort_keys=True, separators=(",", ":"))


class PolarisPolicyClient:
    """Policy operations that are not exposed through PyIceberg."""

    def __init__(self, api: PolicyAPI, catalog_name: str) -> None:
        self._api = api
        self._catalog_name = catalog_name

    @staticmethod
    def _namespace(namespace: tuple[str, ...]) -> str:
        return "\x1f".join(namespace)

    def load_policy(self, namespace: tuple[str, ...], name: str) -> Policy | None:
        try:
            response = self._api.load_policy(
                self._catalog_name,
                self._namespace(namespace),
                name,
                _request_timeout=_REQUEST_TIMEOUT_SECONDS,
            )
        except NotFoundException:
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
        with suppress(NotFoundException):
            self._api.drop_policy(
                self._catalog_name,
                self._namespace(namespace),
                name,
                detach_all=True,
                _request_timeout=_REQUEST_TIMEOUT_SECONDS,
            )

    def apply_policy(self, spec: MaintenancePolicy) -> None:
        desired_content = policy_content_json(spec)
        current = self.load_policy(spec.namespace, spec.name)
        if current is None:
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
            return

        if current.policy_type != spec.policy_type:
            raise RuntimeError(
                f"Polaris policy {spec.name!r} has immutable type "
                f"{current.policy_type!r}, expected {spec.policy_type!r}"
            )
        if (
            current.description == spec.description
            and canonical_policy_content(current) == desired_content
        ):
            return
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
    ) -> list[ApplicablePolicy]:
        policies: list[ApplicablePolicy] = []
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
            except NotFoundException:
                return []
            policies.extend(page.applicable_policies)
            page_token = page.next_page_token
            if page_token is None:
                return policies
            if page_token in seen_tokens:
                raise RuntimeError("Polaris returned a repeated policy page token")
            seen_tokens.add(page_token)

    def applicable_policies(self, table: TableIdentifier) -> list[ApplicablePolicy]:
        return self._applicable_policies(table.namespace, table.name)

    def policy_applies(
        self,
        spec: MaintenancePolicy,
        target: PolicyTargetContract,
    ) -> bool:
        policies = (
            self._applicable_policies(target.path[:-1], target.path[-1])
            if target.type == "table-like"
            else self._applicable_policies(target.path)
        )
        return any(
            not policy.inherited
            and tuple(policy.namespace) == spec.namespace
            and policy.name == spec.name
            and policy.policy_type == spec.policy_type
            for policy in policies
        )


def bootstrap_policies(
    client: PolarisPolicyClient,
    contracts: PlatformContracts,
) -> None:
    for policy in contracts.policies:
        client.apply_policy(policy)
        for target in policy.targets:
            if not client.policy_applies(policy, target):
                client.attach_policy(policy, target)


def validate_policies(
    client: PolarisPolicyClient,
    contracts: PlatformContracts,
    existing_namespaces: set[tuple[str, ...]],
    existing_tables: set[tuple[str, ...]],
) -> tuple[str, ...]:
    errors: list[str] = []
    for policy in contracts.policies:
        identifier = ".".join((*policy.namespace, policy.name))
        current = (
            client.load_policy(policy.namespace, policy.name)
            if policy.namespace in existing_namespaces
            else None
        )
        if current is None:
            errors.append(f"policy:{identifier}:missing")
            continue
        if current.policy_type != policy.policy_type:
            errors.append(f"policy:{identifier}:type")
        if current.description != policy.description:
            errors.append(f"policy:{identifier}:description")
        if canonical_policy_content(current) != policy_content_json(policy):
            errors.append(f"policy:{identifier}:content")
        for target in policy.targets:
            target_exists = (
                target.path in existing_namespaces
                if target.type == "namespace"
                else target.path in existing_tables
            )
            if target_exists and not client.policy_applies(policy, target):
                errors.append(
                    f"policy_mapping:{identifier}->{target.type}:{'.'.join(target.path)}:missing"
                )
    return tuple(errors)
