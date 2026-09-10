select
    trade_date,
    configuration_version,
    configuration_sha256,
    status,
    failure_reason,
    manifest_count,
    full_window_session_count,
    eligible_session_count,
    selected_stream_session_id,
    evidence_sha256,
    evaluated_at
from {{ source('t0_trading', 'market_day_certifications') }}
