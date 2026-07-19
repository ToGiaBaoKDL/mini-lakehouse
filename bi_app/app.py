import os
import sys

# Tự động thêm thư mục gốc của dự án vào sys.path để tránh lỗi ModuleNotFoundError
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import streamlit as st

# Cấu hình Page chính
st.set_page_config(
    page_title="Mini Lakehouse Dashboard (ClickHouse)",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom CSS để giao diện trông sang xịn mịn (Sử dụng font chữ mặc định của hệ thống)
st.markdown("""
<style>
    /* CSS Card Layouts */
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
    
    /* Code block styling */
    code, pre {
        background-color: #161b22 !important;
        border-radius: 8px !important;
    }
    
    .stAlert {
        border-radius: 12px !important;
        background-color: rgba(255, 255, 255, 0.02) !important;
        border: 1px solid rgba(255, 255, 255, 0.05) !important;
    }
</style>
""", unsafe_allow_html=True)

# Khởi động Navigation Multipage sử dụng API chính thức của Streamlit
pg = st.navigation([
    st.Page("pages/kpi_overview.py", title="Tổng quan & KPI", icon="📊", default=True),
    st.Page("pages/repository_activity.py", title="Hoạt động Repositories", icon="📈"),
    st.Page("pages/metadata_inspector.py", title="Inspector Siêu dữ liệu (Iceberg)", icon="🕵️")
])

pg.run()
