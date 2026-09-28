"""Capability-aware request-scope validation for one SSI REST capture."""

from collections.abc import Set

from t0_trading.capture.rest_contract import REST_CAPABILITIES


def _require_exact_scope(label: str, actual: Set[str], expected: Set[str]) -> None:
    missing = sorted(expected - actual)
    unexpected = sorted(actual - expected)
    if not missing and not unexpected:
        return
    details: list[str] = []
    if missing:
        details.append(f"missing={','.join(missing)}")
    if unexpected:
        details.append(f"unexpected={','.join(unexpected)}")
    raise RuntimeError(f"SSI {label} scope mismatch: {'; '.join(details)}")


def validate_scopes(
    scopes: dict[str, set[str]],
    *,
    expected_symbols: Set[str],
    expected_indices: Set[str],
    expected_membership_indices: Set[str] = frozenset(),
    captured_index_bars: Set[str] = frozenset(),
    captured_memberships: Set[str] = frozenset(),
) -> None:
    """Reject missing or out-of-scope evidence with an endpoint-specific reason."""
    for capability in REST_CAPABILITIES:
        expected = {
            "symbols": expected_symbols,
            "indices": expected_indices,
            "membership_indices": expected_membership_indices,
        }[capability.scope]
        if capability.endpoint == "get_ohlc_1minute":
            expected = set(captured_index_bars)
        elif capability.endpoint == "get_securities_info_by_index":
            expected = set(captured_memberships)
        actual = scopes.get(capability.endpoint, set())
        _require_exact_scope(capability.label, actual, expected)

    supported_universe = expected_symbols | expected_indices | expected_membership_indices
    unexpected = {
        identity
        for capability in REST_CAPABILITIES
        for identity in scopes.get(capability.endpoint, set())
        if identity not in supported_universe
    }
    if unexpected:
        raise RuntimeError(
            "SSI REST scope contains identities outside the capture universe: "
            + ",".join(sorted(unexpected))
        )
