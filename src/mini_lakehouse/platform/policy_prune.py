"""Review or explicitly apply deletion of stale repository-managed Polaris policies."""

import argparse
import hashlib
import json
import logging
from collections.abc import Sequence

from mini_lakehouse.config import get_settings
from mini_lakehouse.contracts import load_contracts
from mini_lakehouse.logging import configure_logging
from mini_lakehouse.platform.polaris import (
    PolarisPolicyClient,
    create_retry_session,
    request_oauth_token,
)
from mini_lakehouse.platform.policy_reconciliation import (
    PolicyPruneItem,
    apply_policy_prune_plan,
    build_policy_prune_plan,
)
from mini_lakehouse.platform.runtime import validate_runtime_contract

logger = logging.getLogger(__name__)


def plan_payload(plan: tuple[PolicyPruneItem, ...]) -> dict[str, object]:
    policies = [{"namespace": list(item.namespace), "name": item.name} for item in plan]
    encoded = json.dumps(
        policies,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return {
        "plan_sha256": hashlib.sha256(encoded).hexdigest(),
        "policies": policies,
    }


def main(arguments: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Plan stale managed-policy removal or apply one exact reviewed plan."
    )
    parser.add_argument(
        "--apply-plan-sha256",
        help="Delete only when the current plan matches this reviewed SHA-256.",
    )
    parsed = parser.parse_args(arguments)

    settings = get_settings()
    configure_logging(settings.log_level)
    contracts = load_contracts(settings.contracts_dir)
    validate_runtime_contract(settings, contracts)
    with create_retry_session() as session:
        token = request_oauth_token(session, settings)
        client = PolarisPolicyClient(session, settings, token)
        plan = build_policy_prune_plan(client, contracts)
        payload = plan_payload(plan)
        print(json.dumps(payload, indent=2))
        expected_sha256 = parsed.apply_plan_sha256
        if expected_sha256 is not None:
            if expected_sha256 != payload["plan_sha256"]:
                raise RuntimeError(
                    "Polaris policy prune plan changed after review; generate and review a new plan"
                )
            if plan:
                apply_policy_prune_plan(client, plan)
                logger.info("Pruned %d stale managed Polaris policies", len(plan))
            else:
                logger.info("The reviewed Polaris policy prune plan is empty")


if __name__ == "__main__":
    main()
