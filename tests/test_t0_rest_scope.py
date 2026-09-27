import pytest
from emr_jobs.market_data.rest_scope import validate_scopes

SYMBOLS = {"VIC", "VHM"}
INDICES = {"VNINDEX", "VN30", "VNREAL"}


def _complete_scopes() -> dict[str, set[str]]:
    return {
        "get_securities_info": set(SYMBOLS),
        "get_securities_summary_historical": set(SYMBOLS),
        "get_ohlc_1day_historical": set(SYMBOLS),
        "get_ohlc_1minute_historical": set(SYMBOLS),
        "get_master_data_historical": set(SYMBOLS),
        "get_indexes": set(INDICES),
        "get_index_summary_historical": set(INDICES),
    }


def test_rest_scope_accepts_complete_stock_and_index_evidence() -> None:
    validate_scopes(
        _complete_scopes(),
        expected_symbols=SYMBOLS,
        expected_indices=INDICES,
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
        )


def test_rest_scope_accepts_missing_catalog_only_for_legacy_replay() -> None:
    scopes = _complete_scopes()
    del scopes["get_indexes"]

    validate_scopes(
        scopes,
        expected_symbols=SYMBOLS,
        expected_indices=INDICES,
        allow_legacy_without_index_catalog=True,
    )
