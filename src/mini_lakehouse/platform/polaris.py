from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import quote

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from mini_lakehouse.config.settings import Settings
from mini_lakehouse.contracts import TableIdentifier
from mini_lakehouse.contracts.policies import PolicyContract, policy_content_json
from mini_lakehouse.platform.policies import (
    ApplicablePoliciesResponse,
    ListPoliciesResponse,
    PolarisPolicy,
    PolicyIdentifier,
)


def create_retry_session() -> requests.Session:
    retry = Retry(
        total=12,
        backoff_factor=1,
        # Do not blanket-retry 500: Polaris may use it for deterministic policy
        # errors such as POLICY_MAPPING_NOT_FOUND. Those must fail fast.
        status_forcelist=(429, 502, 503, 504),
        allowed_methods=frozenset({"GET", "POST", "PUT"}),
        respect_retry_after_header=True,
    )
    session = requests.Session()
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


def request_oauth_token(session: requests.Session, settings: Settings) -> str:
    client_id, client_secret = settings.polaris.credential.get_secret_value().split(":", 1)
    response = session.post(
        settings.polaris.oauth2_server_uri,
        data={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": settings.polaris.scope,
        },
        headers={"Polaris-Realm": settings.polaris.realm},
        timeout=10,
    )
    response.raise_for_status()
    token = response.json().get("access_token")
    if not isinstance(token, str) or not token:
        raise RuntimeError("Polaris did not return an OAuth access token")
    return token


class PolarisManagementClient:
    """Small HTTP boundary for Polaris desired-state reconciliation."""

    def __init__(
        self,
        session: requests.Session,
        settings: Settings,
        token: str,
    ) -> None:
        self._session = session
        self._base_url = settings.polaris.management_uri.rstrip("/")
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Polaris-Realm": settings.polaris.realm,
        }

    @staticmethod
    def _segment(value: str) -> str:
        return quote(value, safe="")

    def get_catalog(self, catalog: str) -> requests.Response:
        return self._session.get(
            f"{self._base_url}/catalogs/{self._segment(catalog)}",
            headers=self._headers,
            timeout=15,
        )

    def create_catalog(self, payload: dict[str, Any]) -> requests.Response:
        return self._session.post(
            f"{self._base_url}/catalogs",
            json=payload,
            headers=self._headers,
            timeout=15,
        )

    def update_catalog(
        self,
        catalog: str,
        payload: dict[str, Any],
    ) -> requests.Response:
        return self._session.put(
            f"{self._base_url}/catalogs/{self._segment(catalog)}",
            json=payload,
            headers=self._headers,
            timeout=15,
        )

    def get_catalog_role_grants(self, catalog: str, role: str) -> requests.Response:
        return self._session.get(
            f"{self._base_url}/catalogs/{self._segment(catalog)}"
            f"/catalog-roles/{self._segment(role)}/grants",
            headers=self._headers,
            timeout=15,
        )

    def grant_catalog_privilege(
        self,
        catalog: str,
        role: str,
        privilege: str,
    ) -> requests.Response:
        return self._session.put(
            f"{self._base_url}/catalogs/{self._segment(catalog)}"
            f"/catalog-roles/{self._segment(role)}/grants",
            json={"type": "catalog", "privilege": privilege},
            headers=self._headers,
            timeout=15,
        )


@dataclass(frozen=True, slots=True)
class PolicyReconcileResult:
    policy: str
    action: Literal["created", "updated", "unchanged"]
    ensured_mappings: int
    pending_mappings: int = 0


class PolarisPolicyClient:
    def __init__(
        self,
        session: requests.Session,
        settings: Settings,
        token: str,
    ) -> None:
        catalog = quote(settings.polaris.catalog_name, safe="")
        self._base_url = f"{settings.polaris.uri.rstrip('/')}/polaris/v1/{catalog}"
        self._session = session
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Polaris-Realm": settings.polaris.realm,
        }

    @staticmethod
    def _namespace_path(namespace: tuple[str, ...]) -> str:
        return quote("\x1f".join(namespace), safe="")

    def _policies_url(self, namespace: tuple[str, ...]) -> str:
        return f"{self._base_url}/namespaces/{self._namespace_path(namespace)}/policies"

    @staticmethod
    def _read_policy(response: requests.Response) -> PolarisPolicy:
        payload = response.json()
        policy = payload.get("policy")
        if not isinstance(policy, dict):
            raise RuntimeError("Polaris did not return a policy object")
        return PolarisPolicy.model_validate(policy)

    def list_policies(self, namespace: tuple[str, ...]) -> list[PolicyIdentifier]:
        identifiers: list[PolicyIdentifier] = []
        page_token: str | None = None
        seen_tokens: set[str] = set()
        while True:
            params = {"page-token": page_token} if page_token is not None else None
            response = self._session.get(
                self._policies_url(namespace),
                params=params,
                headers=self._headers,
                timeout=15,
            )
            response.raise_for_status()
            page = ListPoliciesResponse.model_validate(response.json())
            identifiers.extend(page.identifiers)
            page_token = page.next_page_token
            if page_token is None:
                return identifiers
            if page_token in seen_tokens:
                raise RuntimeError("Polaris returned a repeated policy page token")
            seen_tokens.add(page_token)

    def delete_policy(self, namespace: tuple[str, ...], name: str) -> None:
        response = self._session.delete(
            f"{self._policies_url(namespace)}/{quote(name, safe='')}",
            params={"detach-all": "true"},
            headers=self._headers,
            timeout=15,
        )
        if response.status_code != 404:
            response.raise_for_status()

    def reconcile_policy(self, spec: PolicyContract) -> PolicyReconcileResult:
        collection_url = self._policies_url(spec.namespace)
        policy_url = f"{collection_url}/{quote(spec.name, safe='')}"
        desired_content = policy_content_json(spec)
        response = self._session.get(policy_url, headers=self._headers, timeout=15)
        action: Literal["created", "updated", "unchanged"] = "unchanged"
        current: PolarisPolicy | None = None
        if response.status_code == 404:
            response = self._session.post(
                collection_url,
                json={
                    "name": spec.name,
                    "type": spec.policy_type,
                    "description": spec.description,
                    "content": desired_content,
                },
                headers=self._headers,
                timeout=15,
            )
            if response.status_code in (200, 201):
                action = "created"
            elif response.status_code == 409:
                response = self._session.get(policy_url, headers=self._headers, timeout=15)
                response.raise_for_status()
                current = self._read_policy(response)
            else:
                response.raise_for_status()
        else:
            response.raise_for_status()
            current = self._read_policy(response)

        if current is not None:
            if current.policy_type != spec.policy_type:
                raise RuntimeError(
                    f"Polaris policy {spec.name!r} has type {current.policy_type!r}; "
                    f"expected {spec.policy_type!r}. Policy types are immutable."
                )
            for attempt in range(2):
                if (
                    current.description == spec.description
                    and current.canonical_content() == desired_content
                ):
                    break
                response = self._session.put(
                    policy_url,
                    json={
                        "description": spec.description,
                        "content": desired_content,
                        "current-policy-version": current.version,
                    },
                    headers=self._headers,
                    timeout=15,
                )
                if response.status_code in (200, 204):
                    action = "updated"
                    break
                if response.status_code != 409 or attempt == 1:
                    response.raise_for_status()
                response = self._session.get(policy_url, headers=self._headers, timeout=15)
                response.raise_for_status()
                current = self._read_policy(response)

        ensured = 0
        pending = 0
        for target in spec.targets:
            response = self._session.put(
                f"{policy_url}/mappings",
                json={"target": {"type": target.type, "path": list(target.path)}},
                headers=self._headers,
                timeout=15,
            )
            if response.status_code == 404 and target.type == "table-like":
                pending += 1
                continue
            response.raise_for_status()
            ensured += 1
        return PolicyReconcileResult(
            policy=spec.name,
            action=action,
            ensured_mappings=ensured,
            pending_mappings=pending,
        )

    def applicable_policies(self, table: TableIdentifier) -> list[PolarisPolicy]:
        policies: list[PolarisPolicy] = []
        page_token: str | None = None
        seen_tokens: set[str] = set()
        while True:
            params = {
                "namespace": "\x1f".join(table.namespace),
                "target-name": table.name,
            }
            if page_token is not None:
                params["page-token"] = page_token
            response = self._session.get(
                f"{self._base_url}/applicable-policies",
                params=params,
                headers=self._headers,
                timeout=15,
            )
            response.raise_for_status()
            page = ApplicablePoliciesResponse.model_validate(response.json())
            policies.extend(page.applicable_policies)
            page_token = page.next_page_token
            if page_token is None:
                return policies
            if page_token in seen_tokens:
                raise RuntimeError("Polaris returned a repeated policy page token")
            seen_tokens.add(page_token)
