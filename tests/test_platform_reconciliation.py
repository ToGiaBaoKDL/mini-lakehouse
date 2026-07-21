import copy
import json
from unittest.mock import create_autospec

import requests
from pyiceberg.catalog import Catalog
from pyiceberg.exceptions import NamespaceAlreadyExistsError

from mini_lakehouse.config.settings import Settings
from mini_lakehouse.contracts import load_contracts
from mini_lakehouse.contracts.policies import policy_content_json
from mini_lakehouse.platform.access import ensure_catalog_role_grants
from mini_lakehouse.platform.catalog import catalog_contract, ensure_catalog
from mini_lakehouse.platform.namespaces import ensure_namespaces, namespace_contract
from mini_lakehouse.platform.polaris import PolarisManagementClient, PolarisPolicyClient


def _response(status_code: int, payload: object | None = None) -> requests.Response:
    response = requests.Response()
    response.status_code = status_code
    response.url = "http://polaris.test"
    if payload is not None:
        response._content = json.dumps(payload).encode()
        response.headers["Content-Type"] = "application/json"
    return response


def test_catalog_reconcile_is_a_noop_when_current_state_matches() -> None:
    settings = Settings()
    desired = catalog_contract(settings)
    session = create_autospec(requests.Session, instance=True)
    session.get.return_value = _response(200, {"catalog": desired})

    ensure_catalog(PolarisManagementClient(session, settings, "token"), desired)

    session.post.assert_not_called()


def test_catalog_contract_includes_default_location_in_allowed_locations() -> None:
    desired = catalog_contract(Settings())

    assert (
        desired["properties"]["default-base-location"]
        in desired["storageConfigInfo"]["allowedLocations"]
    )


def test_catalog_reconcile_updates_mutable_drift_with_entity_version() -> None:
    settings = Settings()
    desired = catalog_contract(settings)
    current = copy.deepcopy(desired)
    current["entityVersion"] = 7
    current["storageConfigInfo"]["allowedLocations"] = ["s3://wrong"]
    session = create_autospec(requests.Session, instance=True)
    session.get.return_value = _response(200, {"catalog": current})
    session.put.return_value = _response(204)

    ensure_catalog(PolarisManagementClient(session, settings, "token"), desired)

    session.put.assert_called_once()
    assert session.put.call_args.kwargs["json"] == {
        "currentEntityVersion": 7,
        "properties": desired["properties"],
        "storageConfigInfo": desired["storageConfigInfo"],
    }


def test_catalog_reconcile_rejects_immutable_drift() -> None:
    settings = Settings()
    desired = catalog_contract(settings)
    current = copy.deepcopy(desired)
    current["type"] = "EXTERNAL"
    current["entityVersion"] = 2
    session = create_autospec(requests.Session, instance=True)
    session.get.return_value = _response(200, {"catalog": current})

    try:
        ensure_catalog(PolarisManagementClient(session, settings, "token"), desired)
    except RuntimeError as error:
        assert "immutable contract drift at: type" in str(error)
    else:
        raise AssertionError("Expected immutable catalog drift to fail reconciliation")

    session.put.assert_not_called()


def test_catalog_reconcile_retries_once_after_concurrent_update() -> None:
    settings = Settings()
    desired = catalog_contract(settings)
    stale = copy.deepcopy(desired)
    stale["entityVersion"] = 3
    stale["properties"]["owner"] = "stale-owner"
    current = copy.deepcopy(desired)
    current["entityVersion"] = 4
    session = create_autospec(requests.Session, instance=True)
    session.get.side_effect = [
        _response(200, {"catalog": stale}),
        _response(200, {"catalog": current}),
    ]
    session.put.return_value = _response(409)

    ensure_catalog(PolarisManagementClient(session, settings, "token"), desired)

    session.put.assert_called_once()
    assert session.get.call_count == 2


def test_catalog_role_reconcile_only_grants_missing_privileges() -> None:
    settings = Settings()
    contracts = load_contracts()
    session = create_autospec(requests.Session, instance=True)
    session.get.return_value = _response(
        200,
        {
            "grants": [
                {"type": "catalog", "privilege": "CATALOG_MANAGE_METADATA"},
            ]
        },
    )
    session.put.return_value = _response(204)

    granted = ensure_catalog_role_grants(
        PolarisManagementClient(session, settings, "token"), contracts
    )

    assert granted == 1
    session.put.assert_called_once()
    assert session.put.call_args.kwargs["json"] == {
        "type": "catalog",
        "privilege": "CATALOG_MANAGE_CONTENT",
    }


def test_catalog_role_reconcile_is_noop_when_privilege_exists() -> None:
    settings = Settings()
    contracts = load_contracts()
    session = create_autospec(requests.Session, instance=True)
    session.get.return_value = _response(
        200,
        {
            "grants": [
                {"type": "catalog", "privilege": "CATALOG_MANAGE_CONTENT"},
            ]
        },
    )

    granted = ensure_catalog_role_grants(
        PolarisManagementClient(session, settings, "token"), contracts
    )

    assert granted == 0
    session.put.assert_not_called()


def test_namespace_reconcile_only_writes_changed_properties() -> None:
    settings = Settings()
    contracts = load_contracts()
    desired = namespace_contract(settings, contracts)
    catalog = create_autospec(Catalog, instance=True)
    catalog.create_namespace.side_effect = NamespaceAlreadyExistsError

    def current_properties(namespace: tuple[str, ...]) -> dict[str, str]:
        current = dict(desired[namespace])
        if namespace == ("analytics", "engineering"):
            current["owner"] = "stale-owner"
        return current

    catalog.load_namespace_properties.side_effect = current_properties

    ensure_namespaces(catalog, settings, contracts)

    catalog.update_namespace_properties.assert_called_once_with(
        ("analytics", "engineering"),
        updates={"owner": "engineering-analytics"},
    )


def test_policy_reconcile_does_not_update_unchanged_content() -> None:
    settings = Settings()
    spec = load_contracts().policies[0]
    session = create_autospec(requests.Session, instance=True)
    session.get.return_value = _response(
        200,
        {
            "policy": {
                "name": spec.name,
                "type": spec.policy_type,
                "description": spec.description,
                "content": policy_content_json(spec),
                "version": 1,
            }
        },
    )
    session.put.return_value = _response(204)
    client = PolarisPolicyClient(session, settings, "token")

    result = client.reconcile_policy(spec)

    assert result.action == "unchanged"
    assert result.ensured_mappings == 3
    session.post.assert_not_called()
    assert session.put.call_count == 3


def test_policy_reconcile_updates_drift_with_the_current_server_version() -> None:
    settings = Settings()
    spec = load_contracts().policies[0]
    session = create_autospec(requests.Session, instance=True)
    session.get.return_value = _response(
        200,
        {
            "policy": {
                "name": spec.name,
                "type": spec.policy_type,
                "description": "stale description",
                "content": policy_content_json(spec),
                "version": 7,
            }
        },
    )
    session.put.return_value = _response(204)

    result = PolarisPolicyClient(session, settings, "token").reconcile_policy(spec)

    assert result.action == "updated"
    update_payload = session.put.call_args_list[0].kwargs["json"]
    assert update_payload["current-policy-version"] == 7
    assert update_payload["description"] == spec.description


def test_policy_reconcile_handles_a_concurrent_create_idempotently() -> None:
    settings = Settings()
    spec = load_contracts().policies[0]
    session = create_autospec(requests.Session, instance=True)
    session.get.side_effect = [
        _response(404),
        _response(
            200,
            {
                "policy": {
                    "name": spec.name,
                    "policy-type": spec.policy_type,
                    "description": spec.description,
                    "content": policy_content_json(spec),
                    "version": 0,
                    "inheritable": True,
                }
            },
        ),
    ]
    session.post.return_value = _response(409)
    session.put.return_value = _response(204)

    result = PolarisPolicyClient(session, settings, "token").reconcile_policy(spec)

    assert result.action == "unchanged"
    assert session.put.call_count == 3
