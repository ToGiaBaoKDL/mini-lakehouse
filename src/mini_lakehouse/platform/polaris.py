from urllib.parse import quote

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from mini_lakehouse.config.settings import Settings
from mini_lakehouse.contracts import TableIdentifier
from mini_lakehouse.platform.policies import (
    ApplicablePoliciesResponse,
    PolarisPolicy,
    PolicySpec,
)


def create_retry_session() -> requests.Session:
    retry = Retry(
        total=12,
        backoff_factor=1,
        status_forcelist=(429, 500, 502, 503, 504),
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
        f"{settings.polaris.uri.rstrip('/')}/v1/oauth/tokens",
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

    def ensure_policy(self, spec: PolicySpec) -> None:
        collection_url = self._policies_url(spec.namespace)
        policy_url = f"{collection_url}/{quote(spec.name, safe='')}"
        response = self._session.get(policy_url, headers=self._headers, timeout=15)
        if response.status_code == 404:
            response = self._session.post(
                collection_url,
                json={
                    "name": spec.name,
                    "type": spec.policy_type,
                    "description": spec.description,
                    "content": spec.content_json,
                },
                headers=self._headers,
                timeout=15,
            )
            response.raise_for_status()
        else:
            response.raise_for_status()
            current = self._read_policy(response)
            if current.policy_type != spec.policy_type:
                raise RuntimeError(
                    f"Polaris policy {spec.name!r} has type {current.policy_type!r}; "
                    f"expected {spec.policy_type!r}. Policy types are immutable."
                )
            if (
                current.description != spec.description
                or current.canonical_content() != spec.content_json
            ):
                response = self._session.put(
                    policy_url,
                    json={
                        "description": spec.description,
                        "content": spec.content_json,
                        "current-policy-version": current.version,
                    },
                    headers=self._headers,
                    timeout=15,
                )
                response.raise_for_status()

        for target_namespace in spec.target_namespaces:
            response = self._session.put(
                f"{policy_url}/mappings",
                json={
                    "target": {
                        "type": "namespace",
                        "path": list(target_namespace),
                    }
                },
                headers=self._headers,
                timeout=15,
            )
            response.raise_for_status()

    def applicable_policies(self, table: TableIdentifier) -> list[PolarisPolicy]:
        response = self._session.get(
            f"{self._base_url}/applicable-policies",
            params={
                "namespace": "\x1f".join(table.namespace),
                "target-name": table.name,
            },
            headers=self._headers,
            timeout=15,
        )
        response.raise_for_status()
        return ApplicablePoliciesResponse.model_validate(response.json()).applicable_policies
