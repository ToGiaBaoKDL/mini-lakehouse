"""Capability-aware request-scope validation for one SSI REST capture."""

from t0_trading.capture.rest_contract import REST_CAPABILITIES


def _require_exact_scope(label: str, actual: set[str], expected: set[str]) -> None:
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
    expected_symbols: set[str],
    expected_indices: set[str],
    allow_legacy_without_index_catalog: bool = False,
) -> None:
    """Reject missing or out-of-scope evidence with an endpoint-specific reason."""
    for capability in REST_CAPABILITIES:
        if (
            allow_legacy_without_index_catalog
            and capability.endpoint == "get_indexes"
            and capability.endpoint not in scopes
        ):
            continue
        expected = expected_symbols if capability.scope == "symbols" else expected_indices
        actual = scopes.get(capability.endpoint, set())
        if capability.endpoint == "get_ohlc_1minute_historical":
            # Older immutable captures contain empty/experimental index probes.
            # They are tolerated for replay but are not a declared capability.
            actual = actual - expected_indices
        _require_exact_scope(capability.label, actual, expected)

    supported_universe = expected_symbols | expected_indices
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
