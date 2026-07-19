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

# Custom CSS để giao diện trông sang xịn mịn (Premium Dark Theme/Modern Styling)
st.markdown("""
<style>
    /* Gradient Background cho Header Title */
    .main-title {
        font-family: 'Inter', sans-serif;
        background: linear-gradient(135deg, #FF8a00, #E52e71);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        font-weight: 800;
        font-size: 2.8rem;
        margin-bottom: 0.5rem;
    }
    .sub-title {
        color: #88888b;
        font-size: 1.1rem;
        margin-bottom: 2rem;
    }
    /* Style cho các khối KPI metric */
    div[data-testid="stMetricValue"] {
        font-size: 2.2rem;
        font-weight: 700;
        background: linear-gradient(135deg, #00f2fe, #4facfe);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
    }
    div[data-testid="stMetricLabel"] {
        font-weight: 600;
        color: #d1d1d6;
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
