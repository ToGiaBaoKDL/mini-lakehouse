{{ config(materialized='view') }}

with certified_days as (
    select
        trade_date,
        configuration_sha256,
        evidence_sha256,
        evaluated_at
    from {{ ref('stg_trading__market_day_certifications') }}
    where status = 'passed'
),

decisions as (
    select
        decision_version,
        decision_configuration_sha256,
        strategy,
        strategy_version,
        strategy_configuration_sha256,
        outcome_version,
        outcome_configuration_sha256,
        feature_version,
        feature_configuration_sha256,
        feature_snapshot_sha256,
        strategy_score_sha256,
        symbol,
        trade_date,
        decision_at,
        market_session,
        horizon_seconds,
        signed_score,
        minimum_strength,
        action,
        decision_reasons,
        processed_at,
        decision_sha256
    from {{ ref('stg_trading__shadow_decisions') }}
),

outcomes as (
    select
        outcome_version,
        outcome_configuration_sha256,
        feature_snapshot_sha256,
        action,
        horizon_seconds,
        order_quantity,
        entry_at,
        horizon_at,
        entry_vwap,
        horizon_vwap,
        gross_return_bps,
        is_eligible,
        outcome_reasons_json,
        processed_at,
        outcome_sha256
    from {{ ref('stg_trading__outcome_labels') }}
),

final as (
    select
        decisions.trade_date,
        decisions.symbol,
        decisions.decision_at,
        decisions.market_session,
        decisions.strategy,
        decisions.action,
        decisions.horizon_seconds,
        decisions.signed_score,
        decisions.minimum_strength,
        decisions.action != 'ABSTAIN' as is_actionable,
        nullif(array_join(decisions.decision_reasons, ', '), '') as decision_reason_set,
        decisions.decision_version,
        decisions.decision_configuration_sha256,
        decisions.strategy_version,
        decisions.strategy_configuration_sha256,
        decisions.outcome_version,
        decisions.outcome_configuration_sha256,
        decisions.feature_version,
        decisions.feature_configuration_sha256,
        decisions.feature_snapshot_sha256,
        decisions.strategy_score_sha256,
        decisions.decision_sha256,
        outcomes.outcome_sha256 is not null as has_outcome_label,
        coalesce(outcomes.is_eligible, false) as has_eligible_outcome,
        coalesce(outcomes.is_eligible and outcomes.gross_return_bps > 0, false)
            as has_positive_gross_markout,
        case when outcomes.is_eligible then outcomes.gross_return_bps end
            as eligible_gross_return_bps,
        outcomes.order_quantity,
        outcomes.entry_at,
        outcomes.horizon_at,
        outcomes.entry_vwap,
        outcomes.horizon_vwap,
        outcomes.outcome_reasons_json,
        outcomes.outcome_sha256,
        certifications.evidence_sha256,
        certifications.evaluated_at as certification_evaluated_at,
        decisions.processed_at as decision_processed_at,
        outcomes.processed_at as outcome_processed_at
    from decisions
    inner join certified_days as certifications
        on decisions.trade_date = certifications.trade_date
        and decisions.feature_configuration_sha256 = certifications.configuration_sha256
    left join outcomes
        on decisions.outcome_version = outcomes.outcome_version
        and decisions.outcome_configuration_sha256 = outcomes.outcome_configuration_sha256
        and decisions.feature_snapshot_sha256 = outcomes.feature_snapshot_sha256
        and decisions.action = outcomes.action
        and decisions.horizon_seconds = outcomes.horizon_seconds
)

select
    trade_date,
    symbol,
    decision_at,
    market_session,
    strategy,
    action,
    horizon_seconds,
    signed_score,
    minimum_strength,
    is_actionable,
    decision_reason_set,
    decision_version,
    decision_configuration_sha256,
    strategy_version,
    strategy_configuration_sha256,
    outcome_version,
    outcome_configuration_sha256,
    feature_version,
    feature_configuration_sha256,
    feature_snapshot_sha256,
    strategy_score_sha256,
    decision_sha256,
    has_outcome_label,
    has_eligible_outcome,
    has_positive_gross_markout,
    eligible_gross_return_bps,
    order_quantity,
    entry_at,
    horizon_at,
    entry_vwap,
    horizon_vwap,
    outcome_reasons_json,
    outcome_sha256,
    evidence_sha256,
    certification_evaluated_at,
    decision_processed_at,
    outcome_processed_at
from final
