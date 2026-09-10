{{ config(materialized='view') }}

with certified_days as (
    select
        trade_date,
        configuration_version,
        configuration_sha256,
        selected_stream_session_id,
        evidence_sha256,
        evaluated_at
    from {{ ref('stg_trading__market_day_certifications') }}
    where status = 'passed'
),

eligible_snapshots as (
    select
        feature_version,
        configuration_version,
        configuration_sha256,
        symbol,
        trade_date,
        decision_at,
        market_session,
        stream_session_id,
        last_receive_sequence,
        trade_age_seconds,
        quote_age_seconds,
        mid_price,
        microprice,
        microprice_deviation_bps,
        spread,
        spread_bps,
        bid_depth,
        ask_depth,
        level_one_imbalance,
        depth_imbalance,
        available_at,
        processed_at,
        manifest_sha256,
        snapshot_sha256
    from {{ ref('stg_trading__feature_snapshots') }}
    where is_eligible
),

feature_windows as (
    select
        feature_version,
        configuration_sha256,
        symbol,
        trade_date,
        decision_at,
        window_seconds,
        trade_count,
        quote_change_count,
        trade_volume,
        signed_trade_volume,
        trade_volume_per_second,
        trade_volume_imbalance,
        level_one_order_flow_imbalance,
        price_return_bps,
        realized_volatility_bps,
        vwap,
        last_price_to_vwap_bps,
        snapshot_sha256
    from {{ ref('stg_trading__feature_windows') }}
),

eligible_outcomes as (
    select
        outcome_version,
        outcome_configuration_sha256,
        feature_version,
        feature_configuration_sha256,
        feature_snapshot_sha256,
        stream_session_id,
        symbol,
        trade_date,
        decision_at,
        action,
        horizon_seconds,
        order_quantity,
        entry_at,
        horizon_at,
        entry_quote_received_at,
        entry_receive_sequence,
        entry_vwap,
        horizon_quote_received_at,
        horizon_receive_sequence,
        horizon_vwap,
        gross_return_bps,
        processed_at,
        outcome_sha256
    from {{ ref('stg_trading__outcome_labels') }}
    where is_eligible and gross_return_bps is not null
),

final as (
    select
        snapshots.trade_date,
        snapshots.symbol,
        snapshots.decision_at,
        snapshots.market_session,
        outcomes.action,
        outcomes.horizon_seconds,
        windows.window_seconds,
        outcomes.order_quantity,
        snapshots.feature_version,
        snapshots.configuration_version,
        snapshots.configuration_sha256,
        outcomes.outcome_version,
        outcomes.outcome_configuration_sha256,
        snapshots.stream_session_id,
        certifications.evidence_sha256,
        snapshots.manifest_sha256,
        snapshots.snapshot_sha256 as feature_snapshot_sha256,
        outcomes.outcome_sha256,
        snapshots.last_receive_sequence,
        outcomes.entry_receive_sequence,
        outcomes.horizon_receive_sequence,
        snapshots.available_at as feature_available_at,
        outcomes.entry_at,
        outcomes.horizon_at,
        outcomes.entry_quote_received_at,
        outcomes.horizon_quote_received_at,
        snapshots.trade_age_seconds,
        snapshots.quote_age_seconds,
        snapshots.mid_price,
        snapshots.microprice,
        snapshots.microprice_deviation_bps,
        snapshots.spread,
        snapshots.spread_bps,
        snapshots.bid_depth,
        snapshots.ask_depth,
        snapshots.level_one_imbalance,
        snapshots.depth_imbalance,
        windows.trade_count,
        windows.quote_change_count,
        windows.trade_volume,
        windows.signed_trade_volume,
        windows.trade_volume_per_second,
        windows.trade_volume_imbalance,
        windows.level_one_order_flow_imbalance,
        windows.price_return_bps,
        windows.realized_volatility_bps,
        windows.vwap,
        windows.last_price_to_vwap_bps,
        outcomes.entry_vwap,
        outcomes.horizon_vwap,
        outcomes.gross_return_bps,
        certifications.evaluated_at as certification_evaluated_at,
        snapshots.processed_at as feature_processed_at,
        outcomes.processed_at as outcome_processed_at
    from eligible_snapshots as snapshots
    inner join certified_days as certifications
        on snapshots.trade_date = certifications.trade_date
        and snapshots.configuration_sha256 = certifications.configuration_sha256
        and snapshots.stream_session_id = certifications.selected_stream_session_id
    inner join feature_windows as windows
        on snapshots.feature_version = windows.feature_version
        and snapshots.configuration_sha256 = windows.configuration_sha256
        and snapshots.symbol = windows.symbol
        and snapshots.trade_date = windows.trade_date
        and snapshots.decision_at = windows.decision_at
        and snapshots.snapshot_sha256 = windows.snapshot_sha256
    inner join eligible_outcomes as outcomes
        on snapshots.feature_version = outcomes.feature_version
        and snapshots.configuration_sha256 = outcomes.feature_configuration_sha256
        and snapshots.snapshot_sha256 = outcomes.feature_snapshot_sha256
        and snapshots.stream_session_id = outcomes.stream_session_id
        and snapshots.symbol = outcomes.symbol
        and snapshots.trade_date = outcomes.trade_date
        and snapshots.decision_at = outcomes.decision_at
)

select
    trade_date,
    symbol,
    decision_at,
    market_session,
    action,
    horizon_seconds,
    window_seconds,
    order_quantity,
    feature_version,
    configuration_version,
    configuration_sha256,
    outcome_version,
    outcome_configuration_sha256,
    stream_session_id,
    evidence_sha256,
    manifest_sha256,
    feature_snapshot_sha256,
    outcome_sha256,
    last_receive_sequence,
    entry_receive_sequence,
    horizon_receive_sequence,
    feature_available_at,
    entry_at,
    horizon_at,
    entry_quote_received_at,
    horizon_quote_received_at,
    trade_age_seconds,
    quote_age_seconds,
    mid_price,
    microprice,
    microprice_deviation_bps,
    spread,
    spread_bps,
    bid_depth,
    ask_depth,
    level_one_imbalance,
    depth_imbalance,
    trade_count,
    quote_change_count,
    trade_volume,
    signed_trade_volume,
    trade_volume_per_second,
    trade_volume_imbalance,
    level_one_order_flow_imbalance,
    price_return_bps,
    realized_volatility_bps,
    vwap,
    last_price_to_vwap_bps,
    entry_vwap,
    horizon_vwap,
    gross_return_bps,
    certification_evaluated_at,
    feature_processed_at,
    outcome_processed_at
from final
