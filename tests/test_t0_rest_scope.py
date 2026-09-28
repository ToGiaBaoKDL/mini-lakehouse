import pytest
from emr_jobs.market_data.rest_scope import validate_scopes

SYMBOLS = {"VIC", "VHM"}
INDICES = {"VNINDEX", "VN30", "VNREAL"}
MEMBERSHIP_INDICES = {"VN30", "VNREAL"}


def _complete_scopes() -> dict[str, set[str]]:
    return {
        "get_securities_info": set(SYMBOLS),
        "get_securities_summary_historical": set(SYMBOLS),
        "get_ohlc_1day_historical": set(SYMBOLS),
        "get_ohlc_1minute_historical": set(SYMBOLS),
        "get_master_data_historical": set(SYMBOLS),
        "get_indexes": set(INDICES),
        "get_index_summary_historical": set(INDICES),
        "get_ohlc_1minute": set(INDICES),
        "get_securities_info_by_index": set(MEMBERSHIP_INDICES),
    }


def test_rest_scope_accepts_complete_stock_and_index_evidence() -> None:
    validate_scopes(
        _complete_scopes(),
        expected_symbols=SYMBOLS,
        expected_indices=INDICES,
        expected_membership_indices=MEMBERSHIP_INDICES,
        captured_index_bars=INDICES,
        captured_memberships=MEMBERSHIP_INDICES,
    )


def test_rest_scope_reports_missing_stock_minute_bars_as_stock_failure() -> None:
    scopes = _complete_scopes()
    scopes["get_ohlc_1minute_historical"].remove("VHM")

    with pytest.raises(
        RuntimeError,
        match="SSI completed-day stock OHLC 1-minute scope mismatch: missing=VHM",
    ):
        validate_scopes(
            scopes,
            expected_symbols=SYMBOLS,
            expected_indices=INDICES,
            expected_membership_indices=MEMBERSHIP_INDICES,
            captured_index_bars=INDICES,
            captured_memberships=MEMBERSHIP_INDICES,
        )


def test_rest_scope_reports_missing_provider_index_membership() -> None:
    scopes = _complete_scopes()
    scopes["get_indexes"].remove("VNREAL")

    with pytest.raises(
        RuntimeError,
        match="SSI index catalog scope mismatch: missing=VNREAL",
    ):
        validate_scopes(
            scopes,
            expected_symbols=SYMBOLS,
            expected_indices=INDICES,
            expected_membership_indices=MEMBERSHIP_INDICES,
            captured_index_bars=INDICES,
            captured_memberships=MEMBERSHIP_INDICES,
        )


def test_rest_scope_accepts_explicitly_unavailable_index_bars() -> None:
    scopes = _complete_scopes()
    scopes["get_ohlc_1minute"] = set()

    validate_scopes(
        scopes,
        expected_symbols=SYMBOLS,
        expected_indices=INDICES,
        expected_membership_indices=MEMBERSHIP_INDICES,
        captured_index_bars=frozenset(),
        captured_memberships=MEMBERSHIP_INDICES,
    )


def test_rest_scope_requires_each_configured_membership_universe() -> None:
    scopes = _complete_scopes()
    scopes["get_securities_info_by_index"].remove("VNREAL")

    with pytest.raises(
        RuntimeError,
        match="SSI point-in-time index membership scope mismatch: missing=VNREAL",
    ):
        validate_scopes(
            scopes,
            expected_symbols=SYMBOLS,
            expected_indices=INDICES,
            expected_membership_indices=MEMBERSHIP_INDICES,
            captured_index_bars=INDICES,
            captured_memberships=MEMBERSHIP_INDICES,
        )


def test_rest_scope_accepts_explicitly_unavailable_membership() -> None:
    scopes = _complete_scopes()
    scopes["get_securities_info_by_index"] = set()

    validate_scopes(
        scopes,
        expected_symbols=SYMBOLS,
        expected_indices=INDICES,
        expected_membership_indices=MEMBERSHIP_INDICES,
        captured_index_bars=INDICES,
        captured_memberships=frozenset(),
    )
