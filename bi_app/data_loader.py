import streamlit as st
import clickhouse_connect
from pyiceberg.catalog import load_catalog
from src.utils.config import settings

@st.cache_resource
def get_clickhouse_client():
    """Khởi tạo và cache kết nối ClickHouse HTTP Client từ Pydantic Settings."""
    return clickhouse_connect.get_client(
        host=settings.clickhouse_host,
        port=settings.clickhouse_port,
        username=settings.clickhouse_user,
        password=settings.clickhouse_password,
        database=settings.clickhouse_db
    )

@st.cache_resource
def get_iceberg_catalog():
    """Khởi tạo và cache kết nối PyIceberg REST Catalog."""
    return load_catalog("prod")

@st.cache_data(ttl=30)
def load_overview_data():
    """Tải và cache dữ liệu lớp Curated từ ClickHouse."""
    client = get_clickhouse_client()
    events_df = client.query_df("SELECT * FROM default_curated_engineering.github_events")
    repos_df = client.query_df("SELECT * FROM default_curated_engineering.repositories")
    actors_df = client.query_df("SELECT * FROM default_curated_engineering.actors")
    return events_df, repos_df, actors_df

@st.cache_data(ttl=30)
def load_analytics_data():
    """Tải và cache dữ liệu lớp Analytics từ ClickHouse."""
    client = get_clickhouse_client()
    repo_daily_df = client.query_df("SELECT * FROM default_analytics_engineering.repository_activity_daily")
    contrib_daily_df = client.query_df("SELECT * FROM default_analytics_engineering.contributor_activity_daily")
    return repo_daily_df, contrib_daily_df
