import copy
import json
from unittest.mock import call, create_autospec

import requests
from pyiceberg.catalog import Catalog
from pyiceberg.exceptions import NamespaceAlreadyExistsError

from mini_lakehouse.config.settings import Settings
from mini_lakehouse.contracts import load_contracts
from mini_lakehouse.contracts.maintenance import policy_content_json
from mini_lakehouse.platform.desired_state import DesiredCatalog, compile_desired_state
from mini_lakehouse.platform.polaris import (
    PolarisManagementClient,
    PolarisPolicyClient,
    PolicyIdentifier,
    PolicyReconcileResult,
)
from mini_lakehouse.platform.policy_prune import (
    PolicyPruneItem,
    apply_policy_prune_plan,
    build_policy_prune_plan,
    plan_payload,
)
from mini_lakehouse.platform.reconcile import (
    ensure_catalog,
    ensure_catalog_role_grants,
    ensure_namespaces,
    reconcile_policies,
)


def _response(status_code: int, payload: object | None = None) -> requests.Response:
    response = requests.Response()
    response.status_code = status_code
    response.url = "http://polaris.test"
    if payload is not None:
        response._content = json.dumps(payload).encode()
        response.headers["Content-Type"] = "application/json"
    return response


def _desired_catalog(settings: Settings) -> DesiredCatalog:
    return compile_desired_state(
        settings,
        load_contracts(),
    ).catalog


def test_catalog_reconcile_is_a_noop_when_current_state_matches() -> None:
    settings = Settings()
    desired = _desired_catalog(settings)
    payload = desired.management_payload()
    session = create_autospec(requests.Session, instance=True)
    session.get.return_value = _response(200, {"catalog": payload})

    ensure_catalog(PolarisManagementClient(session, settings, "token"), desired)

    session.post.assert_not_called()


def test_catalog_contract_includes_default_location_in_allowed_locations() -> None:
    desired = _desired_catalog(Settings())
    payload = desired.management_payload()

    assert (
        payload["properties"]["default-base-location"]
        in payload["storageConfigInfo"]["allowedLocations"]
    )


def test_catalog_reconcile_updates_mutable_drift_with_entity_version() -> None:
    settings = Settings()
    desired = _desired_catalog(settings)
    payload = desired.management_payload()
    current = copy.deepcopy(payload)
    current["entityVersion"] = 7
    current["storageConfigInfo"]["allowedLocations"] = ["s3://wrong"]
    session = create_autospec(requests.Session, instance=True)
    session.get.return_value = _response(200, {"catalog": current})
    session.put.return_value = _response(204)

    ensure_catalog(PolarisManagementClient(session, settings, "token"), desired)

    session.put.assert_called_once()
    assert session.put.call_args.kwargs["json"] == {
        "currentEntityVersion": 7,
        "properties": payload["properties"],
        "storageConfigInfo": payload["storageConfigInfo"],
    }


def test_catalog_reconcile_removes_stale_properties_from_previous_contracts() -> None:
    settings = Settings()
    desired = _desired_catalog(settings)
    payload = desired.management_payload()
    current = copy.deepcopy(payload)
    current["entityVersion"] = 7
    current["properties"]["polaris.config.drop-with-purge.enabled"] = "true"
    session = create_autospec(requests.Session, instance=True)
    session.get.return_value = _response(200, {"catalog": current})
    session.put.return_value = _response(204)

    ensure_catalog(PolarisManagementClient(session, settings, "token"), desired)

    assert session.put.call_args.kwargs["json"]["properties"] == payload["properties"]


def test_catalog_reconcile_rejects_immutable_drift() -> None:
    settings = Settings()
    desired = _desired_catalog(settings)
    current = copy.deepcopy(desired.management_payload())
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
    desired = _desired_catalog(settings)
    payload = desired.management_payload()
    stale = copy.deepcopy(payload)
    stale["entityVersion"] = 3
    stale["properties"]["owner"] = "stale-owner"
    current = copy.deepcopy(payload)
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
    state = compile_desired_state(settings, contracts)
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

    granted = ensure_catalog_role_grants(PolarisManagementClient(session, settings, "token"), state)

    assert granted == 1
    session.put.assert_called_once()
    assert session.put.call_args.kwargs["json"] == {
        "type": "catalog",
        "privilege": "CATALOG_MANAGE_CONTENT",
    }


def test_catalog_role_reconcile_is_noop_when_privilege_exists() -> None:
    settings = Settings()
    contracts = load_contracts()
    state = compile_desired_state(settings, contracts)
    session = create_autospec(requests.Session, instance=True)
    session.get.return_value = _response(
        200,
        {
            "grants": [
                {"type": "catalog", "privilege": "CATALOG_MANAGE_CONTENT"},
            ]
        },
    )

    granted = ensure_catalog_role_grants(PolarisManagementClient(session, settings, "token"), state)

    assert granted == 0
    session.put.assert_not_called()


def test_namespace_reconcile_only_writes_changed_properties() -> None:
    settings = Settings()
    contracts = load_contracts()
    state = compile_desired_state(settings, contracts)
    desired = {namespace.path: namespace.iceberg_properties() for namespace in state.namespaces}
    catalog = create_autospec(Catalog, instance=True)
    catalog.create_namespace.side_effect = NamespaceAlreadyExistsError

    def current_properties(namespace: tuple[str, ...]) -> dict[str, str]:
        current = dict(desired[namespace])
        if namespace == ("analytics", "engineering"):
            current["owner"] = "stale-owner"
        return current

    catalog.load_namespace_properties.side_effect = current_properties

    ensure_namespaces(catalog, state)

    catalog.update_namespace_properties.assert_called_once_with(
        ("analytics", "engineering"),
        removals=set(),
        updates={"owner": "engineering-analytics"},
    )


def test_namespace_reconcile_removes_properties_deleted_from_the_contract() -> None:
    state = compile_desired_state(Settings(), load_contracts())
    catalog = create_autospec(Catalog, instance=True)
    catalog.create_namespace.side_effect = NamespaceAlreadyExistsError

    def current_properties(namespace: tuple[str, ...]) -> dict[str, str]:
        return {
            **next(
                desired.iceberg_properties()
                for desired in state.namespaces
                if desired.path == namespace
            ),
            "stale_property": "remove-me",
        }

    catalog.load_namespace_properties.side_effect = current_properties

    ensure_namespaces(catalog, state)

    assert catalog.update_namespace_properties.call_count == len(state.namespaces)
    assert all(
        call_item.kwargs["removals"] == {"stale_property"}
        for call_item in catalog.update_namespace_properties.call_args_list
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
    assert result.ensured_mappings == len(spec.targets)
    session.post.assert_not_called()
    assert session.put.call_count == len(spec.targets)


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
    assert session.put.call_count == len(spec.targets)


def test_policy_prune_plan_removes_only_reserved_stale_policies() -> None:
    contracts = load_contracts()
    desired = contracts.policies[0]
    client = create_autospec(PolarisPolicyClient, instance=True)
    client.list_policies.return_value = [
        PolicyIdentifier(namespace=desired.namespace, name=desired.name),
        PolicyIdentifier(namespace=("analytics",), name="mlh-stale-policy"),
        PolicyIdentifier(namespace=("curated",), name="compact-data-files"),
        PolicyIdentifier(namespace=("analytics",), name="compact-data-files"),
        PolicyIdentifier(namespace=("curated",), name="team-owned-policy"),
    ]

    plan = build_policy_prune_plan(client, contracts)

    assert [(item.namespace, item.name) for item in plan] == [
        (("analytics",), "mlh-stale-policy"),
    ]
    apply_policy_prune_plan(client, plan)
    assert client.delete_policy.call_args_list == [
        call(("analytics",), "mlh-stale-policy"),
    ]


def test_policy_prune_plan_has_a_deterministic_review_identity() -> None:
    contracts = load_contracts()
    client = create_autospec(PolarisPolicyClient, instance=True)
    client.list_policies.return_value = [
        PolicyIdentifier(namespace=("analytics",), name="mlh-stale-policy")
    ]

    plan = build_policy_prune_plan(client, contracts)
    payload = plan_payload(plan)
    changed = plan_payload((PolicyPruneItem(("analytics",), "mlh-other-policy"),))

    assert payload["policies"] == [{"namespace": ["analytics"], "name": "mlh-stale-policy"}]
    assert len(str(payload["plan_sha256"])) == 64
    assert payload["plan_sha256"] != changed["plan_sha256"]


def test_policy_reconcile_summary_tracks_pending_table_mappings() -> None:
    contracts = load_contracts()
    client = create_autospec(PolarisPolicyClient, instance=True)
    client.reconcile_policy.side_effect = [
        PolicyReconcileResult(
            policy=policy.name,
            action="unchanged",
            ensured_mappings=1,
            pending_mappings=1 if index == 0 else 0,
        )
        for index, policy in enumerate(contracts.policies)
    ]

    results = reconcile_policies(client, contracts)

    assert sum(result.ensured_mappings for result in results) == len(contracts.policies)
    assert sum(result.pending_mappings for result in results) == 1
    assert client.reconcile_policy.call_count == len(contracts.policies)
