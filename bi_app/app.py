import os
import sys

# Tự động thêm thư mục gốc của dự án vào sys.path để tránh lỗi ModuleNotFoundError
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import streamlit as st
import pandas as pd
from bi_app.data_loader import get_clickhouse_client, get_iceberg_catalog, load_overview_data, load_analytics_data
from bi_app.components.kpi_overview import render_kpi_overview
from bi_app.components.repository_activity import render_repository_activity
from bi_app.components.metadata_inspector import render_metadata_inspector

# Cấu hình Page chính
st.set_page_config(
    page_title="Mini Lakehouse Dashboard (ClickHouse)",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom CSS để giao diện trông sang xịn mịn (Ultra-Premium Dark Theme)
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700;800&family=JetBrains+Mono:wght@400;500&display=swap');
    
    /* Thiết lập font chữ chính cho app */
    html, body, [class*="css"], .stApp {
        font-family: 'Outfit', sans-serif !important;
        background-color: #0b0d12 !important;
        color: #e2e8f0 !important;
    }
    
    /* Cải tiến Sidebar */
    [data-testid="stSidebar"] {
        background-color: #0f131a !important;
        border-right: 1px solid rgba(255, 255, 255, 0.04) !important;
    }
    [data-testid="stSidebar"] * {
        font-family: 'Outfit', sans-serif !important;
    }
    
    /* Custom style cho Header Title */
    .main-title {
        background: linear-gradient(135deg, #FF8a00, #E52e71, #9b5de5);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        font-weight: 800;
        font-size: 2.8rem;
        margin-bottom: 0.2rem;
        letter-spacing: -0.05rem;
    }
    .sub-title {
        color: #8892b0;
        font-size: 1.1rem;
        font-weight: 400;
        margin-bottom: 2rem;
    }

    /* Thiết kế Glassmorphism Cards cho KPIs */
    .kpi-card-row {
        display: flex;
        gap: 20px;
        flex-wrap: wrap;
        margin-bottom: 25px;
        width: 100%;
    }
    .kpi-card {
        flex: 1;
        min-width: 220px;
        background: rgba(255, 255, 255, 0.02);
        border: 1px solid rgba(255, 255, 255, 0.05);
        border-radius: 16px;
        padding: 24px;
        box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.3);
        backdrop-filter: blur(12px);
        -webkit-backdrop-filter: blur(12px);
        transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
    }
    .kpi-card:hover {
        transform: translateY(-5px);
        border-color: rgba(229, 46, 113, 0.3);
        box-shadow: 0 12px 40px 0 rgba(229, 46, 113, 0.12);
        background: rgba(255, 255, 255, 0.04);
    }
    .kpi-title {
        font-size: 0.85rem;
        font-weight: 600;
        color: #8892b0;
        letter-spacing: 0.08rem;
        text-transform: uppercase;
        margin-bottom: 8px;
    }
    .kpi-value {
        font-size: 2.4rem;
        font-weight: 800;
        line-height: 1.1;
        margin-bottom: 6px;
    }
    .kpi-footer {
        font-size: 0.8rem;
        font-weight: 500;
    }
    
    /* Code và cấu trúc đơn giản */
    code, pre {
        font-family: 'JetBrains Mono', monospace !important;
        background-color: #161b22 !important;
        border-radius: 8px !important;
    }
    
    /* Alert styles */
    .stAlert {
        border-radius: 12px !important;
        background-color: rgba(255, 255, 255, 0.02) !important;
        border: 1px solid rgba(255, 255, 255, 0.05) !important;
    }
</style>
""", unsafe_allow_html=True)

# Khởi tạo các kết nối dữ liệu
db_connected = False
connection_error = ""
client = None
catalog = None

try:
    client = get_clickhouse_client()
    catalog = get_iceberg_catalog()
    db_connected = True
except Exception as e:
    db_connected = False
    connection_error = str(e)

# Sidebar Layout
st.sidebar.image("https://clickhouse.com/images/logo.svg", width=120)
st.sidebar.markdown("### ⚡ ClickHouse Lakehouse Portal")
st.sidebar.markdown("Dự án Học tập Xây dựng Lakehouse cục bộ sử dụng **Apache Iceberg**, **Prefect**, **dbt-clickhouse** và **ClickHouse**.")

menu = st.sidebar.radio(
    "Danh mục Dashboard",
    ["📊 Tổng quan & KPI", "📈 Hoạt động Repositories", "🕵️ Inspector Siêu dữ liệu (Iceberg)"]
)

# Render main header
st.markdown('<div class="main-title">Mini ClickHouse Lakehouse Dashboard</div>', unsafe_allow_html=True)
st.markdown('<div class="sub-title">Hệ thống phân tích sự kiện GitHub Archive chạy trên nền tảng ClickHouse & Apache Iceberg</div>', unsafe_allow_html=True)

if not db_connected or client is None:
    st.error(f"❌ Không thể kết nối tới ClickHouse Server. Vui lòng kiểm tra Docker container!\nChi tiết lỗi: {connection_error}")
else:
    # Trực quan hóa dựa trên menu lựa chọn
    events = pd.DataFrame()
    repos = pd.DataFrame()
    actors = pd.DataFrame()
    repo_daily = pd.DataFrame()
    contrib_daily = pd.DataFrame()
    data_loaded = False

    with st.spinner("Đang tải dữ liệu từ ClickHouse..."):
        try:
            events, repos, actors = load_overview_data()
            repo_daily, contrib_daily = load_analytics_data()
            data_loaded = True
        except Exception as e:
            data_loaded = False
            st.warning(f"Chưa có dữ liệu phân tích hoặc pipeline chưa hoàn thành chạy thử. Chi tiết: {e}")

    if data_loaded:
        if menu == "📊 Tổng quan & KPI":
            render_kpi_overview(events, repos, actors, contrib_daily)
        elif menu == "📈 Hoạt động Repositories":
            render_repository_activity(repo_daily)
        elif menu == "🕵️ Inspector Siêu dữ liệu (Iceberg)":
            render_metadata_inspector(catalog)
