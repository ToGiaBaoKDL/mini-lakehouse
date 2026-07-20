import streamlit as st
from trino.dbapi import connect
import pandas as pd
from pyiceberg.catalog import load_catalog

@st.cache_resource
def get_trino_connection():
    """Khởi tạo và cache kết nối Trino DB-API Client."""
    return connect(
        host="localhost",
        port=8080,
        user="admin",
        catalog="iceberg",
        schema="default"
    )

@st.cache_resource
def get_iceberg_catalog():
    """Khởi tạo và cache kết nối PyIceberg REST Catalog."""
    return load_catalog("prod")

@st.cache_data(ttl=30)
def load_overview_data():
    """Tải và cache dữ liệu lớp Curated từ Trino (kết nối qua Iceberg REST Catalog)."""
    conn = get_trino_connection()
    cur = conn.cursor()
    
    # Đọc bảng github_events
    cur.execute('SELECT * FROM iceberg."curated.engineering".github_events')
    events_rows = cur.fetchall()
    events_df = pd.DataFrame(events_rows, columns=[desc[0] for desc in cur.description])
    
    # Đọc bảng repositories
    cur.execute('SELECT * FROM iceberg."curated.engineering".repositories')
    repos_rows = cur.fetchall()
    repos_df = pd.DataFrame(repos_rows, columns=[desc[0] for desc in cur.description])
    
    # Đọc bảng actors
    cur.execute('SELECT * FROM iceberg."curated.engineering".actors')
    actors_rows = cur.fetchall()
    actors_df = pd.DataFrame(actors_rows, columns=[desc[0] for desc in cur.description])
    
    return events_df, repos_df, actors_df

@st.cache_data(ttl=30)
def load_analytics_data():
    """Tải và cache dữ liệu lớp Analytics từ Trino (kết nối qua Iceberg REST Catalog)."""
    conn = get_trino_connection()
    cur = conn.cursor()
    
    # Đọc bảng repository_activity_daily
    cur.execute('SELECT * FROM iceberg."analytics.engineering".repository_activity_daily')
    repo_rows = cur.fetchall()
    repo_daily_df = pd.DataFrame(repo_rows, columns=[desc[0] for desc in cur.description])
    
    # Đọc bảng contributor_activity_daily
    cur.execute('SELECT * FROM iceberg."analytics.engineering".contributor_activity_daily')
    contrib_rows = cur.fetchall()
    contrib_daily_df = pd.DataFrame(contrib_rows, columns=[desc[0] for desc in cur.description])
    
    return repo_daily_df, contrib_daily_df
