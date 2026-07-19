import streamlit as st
import clickhouse_connect
import pandas as pd
import plotly.express as px
import os
from pyiceberg.catalog import load_catalog

# Cấu hình Page
st.set_page_config(
    page_title="Mini Lakehouse Dashboard (ClickHouse)",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom CSS để giao diện trông sang xịn mịn (Premium Dark Theme/Modern Styling)
st.markdown("""
<style>
    /* Gradient Background cho Header */
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

# Khởi tạo ClickHouse client
@st.cache_resource
def get_clickhouse_client():
    host = os.getenv("CLICKHOUSE_HOST", "localhost")
    port = int(os.getenv("CLICKHOUSE_PORT", "8123"))
    user = os.getenv("CLICKHOUSE_USER", "dev_user")
    password = os.getenv("CLICKHOUSE_PASSWORD", "dev_password")
    database = os.getenv("CLICKHOUSE_DB", "github_lakehouse")
    
    # Kết nối ClickHouse HTTP Client
    client = clickhouse_connect.get_client(
        host=host,
        port=port,
        username=user,
        password=password,
        database=database
    )
    return client

# Khởi tạo PyIceberg Catalog
@st.cache_resource
def get_iceberg_catalog():
    # PyIceberg tự động đọc cấu hình Catalog từ các biến môi trường
    return load_catalog("prod")

# Khởi tạo kết nối
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

# Sidebar
st.sidebar.image("https://clickhouse.com/images/logo.svg", width=120)
st.sidebar.markdown("### ⚡ ClickHouse Lakehouse Portal")
st.sidebar.markdown("Dự án Học tập Xây dựng Lakehouse cục bộ sử dụng **Apache Iceberg**, **Prefect**, **dbt-clickhouse** và **ClickHouse**.")

menu = st.sidebar.radio(
    "Danh mục Dashboard",
    ["📊 Tổng quan & KPI", "📈 Hoạt động Repositories", "🕵️ Inspector Siêu dữ liệu (Iceberg)"]
)

# Header chính
st.markdown('<div class="main-title">Mini ClickHouse Lakehouse Dashboard</div>', unsafe_allow_html=True)
st.markdown('<div class="sub-title">Hệ thống phân tích sự kiện GitHub Archive chạy trên nền tảng ClickHouse & Apache Iceberg</div>', unsafe_allow_html=True)

if not db_connected or client is None:
    st.error(f"❌ Không thể kết nối tới ClickHouse Server. Vui lòng kiểm tra Docker container!\nChi tiết lỗi: {connection_error}")
else:
    # Tải dữ liệu từ ClickHouse
    @st.cache_data(ttl=30)
    def load_overview_data():
        assert client is not None
        events_df = client.query_df("SELECT * FROM default_curated_engineering.github_events")
        repos_df = client.query_df("SELECT * FROM default_curated_engineering.repositories")
        actors_df = client.query_df("SELECT * FROM default_curated_engineering.actors")
        return events_df, repos_df, actors_df

    @st.cache_data(ttl=30)
    def load_analytics_data():
        assert client is not None
        repo_daily_df = client.query_df("SELECT * FROM default_analytics_engineering.repository_activity_daily")
        contrib_daily_df = client.query_df("SELECT * FROM default_analytics_engineering.contributor_activity_daily")
        return repo_daily_df, contrib_daily_df

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
            st.subheader("📌 Tổng quan Hồ chứa dữ liệu (Lakehouse KPIs)")
            
            # Khối Metric hàng đầu
            col1, col2, col3, col4 = st.columns(4)
            with col1:
                st.metric("Tổng sự kiện (Curated layer)", f"{len(events):,}")
            with col2:
                st.metric("Số Repositories hoạt động", f"{len(repos):,}")
            with col3:
                st.metric("Số nhà phát triển (Actors)", f"{len(actors):,}")
            with col4:
                st.metric("Thời gian Ingest gần nhất", pd.to_datetime(events["ingested_at"]).max().strftime('%Y-%m-%d %H:%M') if len(events) > 0 else "N/A")
                
            st.write("---")
            
            # Phân tích loại sự kiện
            col_chart1, col_chart2 = st.columns(2)
            
            with col_chart1:
                st.subheader("💡 Tỷ lệ loại sự kiện (Event Types)")
                event_counts = events["type"].value_counts().reset_index()  # type: ignore
                event_counts.columns = ["Event Type", "Count"]
                fig1 = px.pie(
                    event_counts, 
                    names="Event Type", 
                    values="Count", 
                    hole=0.4,
                    color_discrete_sequence=px.colors.sequential.RdBu
                )
                fig1.update_layout(
                    paper_bgcolor='rgba(0,0,0,0)',
                    plot_bgcolor='rgba(0,0,0,0)',
                    font_color='#fff'
                )
                st.plotly_chart(fig1, use_container_width=True)
                
            with col_chart2:
                st.subheader("📈 Số lượng đóng góp theo ngày (Contributors)")
                daily_contrib = contrib_daily.groupby("activity_date")["total_events"].sum().reset_index()  # type: ignore
                fig2 = px.line(
                    daily_contrib, 
                    x="activity_date", 
                    y="total_events",
                    labels={"activity_date": "Ngày", "total_events": "Số lượng đóng góp (events)"},
                    line_shape="spline",
                    color_discrete_sequence=["#FF8a00"]
                )
                fig2.update_layout(
                    paper_bgcolor='rgba(0,0,0,0)',
                    plot_bgcolor='rgba(0,0,0,0)',
                    font_color='#fff'
                )
                st.plotly_chart(fig2, use_container_width=True)

        elif menu == "📈 Hoạt động Repositories":
            st.subheader("🔥 Top 15 Repositories hoạt động sôi nổi nhất")
            
            # Bảng xếp hạng hoạt động
            top_repos = repo_daily.sort_values(by="total_events", ascending=False).head(15)  # type: ignore
            
            fig = px.bar(
                top_repos,
                x="total_events",
                y="repo_name",
                orientation='h',
                color="active_contributors",
                color_continuous_scale="Viridis",
                labels={"total_events": "Tổng số lượng sự kiện", "repo_name": "Tên Repository", "active_contributors": "Contributors hoạt động"},
                title="Bảng xếp hạng theo số lượng sự kiện và số nhà đóng góp"
            )
            fig.update_layout(
                yaxis={'categoryorder':'total ascending'},
                paper_bgcolor='rgba(0,0,0,0)',
                plot_bgcolor='rgba(0,0,0,0)',
                font_color='#fff'
            )
            st.plotly_chart(fig, use_container_width=True)
            
            st.subheader("Chi tiết hoạt động tích hợp của các Repository")
            st.dataframe(
                repo_daily[["repo_name", "total_events", "push_count", "pull_request_count", "issue_count", "issue_comment_count", "active_contributors"]]
                .sort_values(by="total_events", ascending=False),  # type: ignore
                use_container_width=True
            )

        elif menu == "🕵️ Inspector Siêu dữ liệu (Iceberg)":
            st.subheader("🕵️ Trình kiểm tra Siêu dữ liệu Apache Iceberg qua REST Catalog")
            st.write("Dữ liệu gốc nạp vào Landing layer được quản lý bởi **Apache Iceberg REST Catalog**. Bạn có thể xem lịch sử các Snapshots và tệp siêu dữ liệu `.metadata.json` trực tiếp từ REST Catalog:")
            
            if catalog is None:
                st.warning("Không thể tải Catalog của Iceberg.")
            else:
                try:
                    table_identifier = "landing_api_github.events_raw"
                    st.info(f"Đang đọc bảng Iceberg: **{table_identifier}**")
                    
                    # Tải thông tin bảng qua PyIceberg
                    table = catalog.load_table(table_identifier)
                    
                    # 1. Hiển thị Lịch sử Snapshots
                    st.markdown("### 1. Lịch sử Snapshot (Snapshots History)")
                    snapshots = table.metadata.snapshots
                    
                    if snapshots:
                        history_records = []
                        for snap in snapshots:
                            history_records.append({
                                "Snapshot ID": str(snap.snapshot_id),
                                "Parent ID": str(snap.parent_snapshot_id) if snap.parent_snapshot_id else "None",
                                "Thời gian commit": pd.to_datetime(snap.timestamp_ms, unit="ms"),
                                "Manifest List Location": snap.manifest_list
                            })
                        snapshots_df = pd.DataFrame(history_records)
                        st.dataframe(
                            snapshots_df.sort_values(by="Thời gian commit", ascending=False),
                            use_container_width=True
                        )
                    else:
                        st.info("Bảng Iceberg Landing chưa có snapshot nào.")
                        
                    # 2. Hiển thị tệp tin Metadata Log
                    st.markdown("### 2. Các tệp Siêu dữ liệu lịch sử (Metadata Log Files)")
                    metadata_log = table.metadata.metadata_log
                    if metadata_log:
                        meta_records = []
                        for entry in metadata_log:
                            meta_records.append({
                                "File Metadata JSON": entry.metadata_file_location,  # type: ignore
                                "Thời gian tạo": pd.to_datetime(entry.timestamp_ms, unit="ms")
                            })
                        meta_df = pd.DataFrame(meta_records)
                        st.dataframe(
                            meta_df.sort_values(by="Thời gian tạo", ascending=False),
                            use_container_width=True
                        )
                    else:
                        st.info("Bảng Iceberg chưa ghi nhận tệp log metadata lịch sử nào.")
                        
                except Exception as e:
                    st.error(f"Lỗi khi đọc metadata Iceberg thông qua REST Catalog: {e}")
