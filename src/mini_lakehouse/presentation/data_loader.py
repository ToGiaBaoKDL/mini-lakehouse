from collections.abc import Sequence
from typing import Any

import pandas as pd
import streamlit as st
import trino

from mini_lakehouse.config import get_settings
from mini_lakehouse.contracts import PlatformContracts, load_contracts


def create_trino_connection() -> trino.dbapi.Connection:
    """Create a request-scoped DB-API connection.

    Trino DB-API connections and cursors are stateful. Sharing one cached connection across
    Streamlit sessions can interleave cursors and leak a failed transaction into later reruns.
    """
    settings = get_settings().trino
    return trino.dbapi.connect(
        host=settings.host,
        port=settings.port,
        user=settings.user,
        http_scheme=settings.http_scheme,
    )


@st.cache_resource
def get_contract_registry() -> PlatformContracts:
    settings = get_settings()
    return load_contracts(settings.contracts_dir)


def query_frame(query: str, parameters: Sequence[Any] | None = None) -> pd.DataFrame:
    connection = create_trino_connection()
    try:
        cursor = connection.cursor()
        try:
            cursor.execute(query, params=parameters)
            rows = cursor.fetchall()
            columns = [description[0] for description in cursor.description or []]
        finally:
            cursor.close()
    finally:
        connection.close()
    return pd.DataFrame(rows, columns=pd.Index(columns))


def _domain_relation(domain: str, table: str) -> str:
    return (
        get_contract_registry()
        .domain(domain)
        .table_identifier(table)
        .trino(get_settings().trino.catalog)
    )


@st.cache_data(ttl=60)
def load_overview() -> pd.DataFrame:
    relation = _domain_relation("engineering", "repository_activity_daily")
    return query_frame(
        f"""
        select
            sum(event_count) as event_count,
            sum(push_event_count) as push_event_count,
            sum(pushed_commit_count) as pushed_commit_count,
            sum(pull_request_event_count) as pull_request_event_count,
            sum(issue_event_count) as issue_event_count,
            max(activity_date) as latest_activity_date
        from {relation}
        """
    )


@st.cache_data(ttl=60)
def load_repository_activity(limit: int = 25) -> pd.DataFrame:
    relation = _domain_relation("engineering", "repository_activity_daily")
    return query_frame(
        f"""
        select
            repository_id,
            max_by(repository_name, activity_date) as repository_name,
            sum(event_count) as event_count,
            sum(push_event_count) as push_event_count,
            sum(pushed_commit_count) as pushed_commit_count,
            sum(pull_request_event_count) as pull_request_event_count,
            sum(active_actor_count) as active_actor_days
        from {relation}
        group by repository_id
        order by event_count desc
        limit ?
        """,
        [limit],
    )


@st.cache_data(ttl=60)
def load_daily_event_trend() -> pd.DataFrame:
    relation = _domain_relation("engineering", "contributor_activity_daily")
    return query_frame(
        f"""
        select activity_date, sum(event_count) as event_count
        from {relation}
        group by activity_date
        order by activity_date
        """
    )
