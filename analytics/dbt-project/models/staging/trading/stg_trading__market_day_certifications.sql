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
    coalesce(
        cast(json_parse(selected_stream_session_ids_json) as array(varchar)),
        if(
            selected_stream_session_id is null,
            cast(array[] as array(varchar)),
            array[selected_stream_session_id]
        )
    ) as selected_stream_session_ids,
    coalesce(gap_count, 0) as gap_count,
    coalesce(gap_duration_milliseconds, 0) as gap_duration_milliseconds,
    evidence_sha256,
    evaluated_at
from {{ source('t0_trading', 'market_day_certifications') }}
