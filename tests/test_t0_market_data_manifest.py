import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from emr_jobs.market_data import manifest


def _json(value: object) -> bytes:
    return json.dumps(value, separators=(",", ":"), sort_keys=True).encode()


def _capture(monkeypatch: pytest.MonkeyPatch, *, request_body: bytes | None = None):
    captured_at = datetime(2026, 8, 27, tzinfo=UTC).isoformat()
    object_key = (
        "api/ssi_fastconnect_rest/raw/trade_date=2026-08-26/run=run/"
        "requests/request/page-000001-record.json.gz"
    )
    request_key = (
        "api/ssi_fastconnect_rest/raw/trade_date=2026-08-26/run=run/requests/request/manifest.json"
    )
    request = {
        "schema_version": 1,
        "request_id": "request",
        "endpoint": "get_securities_info",
        "parameters": {"symbol": "VIC"},
        "request_parameters_sha256": "parameters-sha256",
        "requested_at": captured_at,
        "completed_at": captured_at,
        "page_count": 1,
        "record_count": 1,
        "capture_status": "success",
        "api_version": "v3",
        "sdk_version": "3.2.0",
        "pages": [
            {
                "page": 1,
                "record_count": 1,
                "object_key": object_key,
                "object_sha256": "object-sha256",
                "requested_at": captured_at,
                "received_at": captured_at,
                "published_at": captured_at,
            }
        ],
        "published_at": captured_at,
    }
    request["request_parameters_sha256"] = hashlib.sha256(_json(request["parameters"])).hexdigest()
    expected_request_body = _json(request)
    run = {
        "schema_version": 1,
        "trade_date": "2026-08-26",
        "symbols": ["VIC", "VHM"],
        "indices": ["VNINDEX", "VN30"],
        "api_version": "v3",
        "sdk_version": "3.2.0",
        "requests": [
            {
                "request_id": "request",
                "manifest_key": request_key,
                "manifest_sha256": hashlib.sha256(expected_request_body).hexdigest(),
            }
        ],
    }
    run_uri = (
        "s3://landing/root/api/ssi_fastconnect_rest/raw/trade_date=2026-08-26/run=run/manifest.json"
    )
    request_uri = f"s3://landing/root/{request_key}"
    bodies = {
        run_uri: _json(run),
        request_uri: request_body or expected_request_body,
    }
    monkeypatch.setattr(manifest, "read_bytes", bodies.__getitem__)

    def head_object(bucket: str, key: str) -> dict[str, object] | None:
        if bucket == "landing" and key == f"root/{object_key}":
            return {"Metadata": {"sha256": "object-sha256"}}
        return None

    monkeypatch.setattr(manifest, "head_object", head_object)
    return run_uri


def test_capture_manifest_resolves_only_verified_source_objects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capture = manifest.load_capture(_capture(monkeypatch), "2026-08-26")

    assert capture.symbols == ("VIC", "VHM")
    assert capture.indices == ("VNINDEX", "VN30")
    assert capture.requests[0].endpoint == "get_securities_info"
    assert capture.objects[0].uri.endswith("page-000001-record.json.gz")


def test_capture_manifest_rejects_request_checksum_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_uri = _capture(monkeypatch, request_body=b"{}")

    with pytest.raises(RuntimeError, match="request manifest checksum mismatch"):
        manifest.load_capture(run_uri, "2026-08-26")


def test_bounded_scope_must_match_every_requested_symbol_and_index(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capture = manifest.load_capture(_capture(monkeypatch), "2026-08-26")
    publication = capture.requests[0]
    endpoints = (
        *("get_securities_info",) * 2,
        *("get_securities_summary_historical",) * 2,
        *("get_ohlc_1day_historical",) * 2,
        *("get_ohlc_1minute_historical",) * 2,
        "get_master_data_historical",
        *("get_index_summary_historical",) * 2,
    )
    bounded = replace(
        capture,
        requests=tuple(replace(publication, endpoint=endpoint) for endpoint in endpoints),
    )

    manifest.require_bounded_scope(bounded)
    with pytest.raises(RuntimeError, match="required bounded request set"):
        manifest.require_bounded_scope(replace(bounded, requests=bounded.requests[:-1]))
