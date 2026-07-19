import streamlit as st
import pandas as pd
import plotly.express as px
from bi_app.data_loader import load_analytics_data

# Header
st.subheader("🔥 Top 15 Repositories hoạt động sôi nổi nhất")

repo_daily = pd.DataFrame()
data_loaded = False

try:
    repo_daily, _ = load_analytics_data()
    data_loaded = True
except Exception as e:
    data_loaded = False
    st.warning(f"Chưa có dữ liệu phân tích hoặc pipeline chưa hoàn thành chạy thử. Chi tiết: {e}")

if data_loaded:
    # Lấy 15 repo hoạt động nhiều nhất
    top_repos = repo_daily.sort_values(by="total_events", ascending=False).head(15)  # type: ignore
    
    # Biểu đồ thanh ngang với màu sắc gradient từ Plasma
    fig = px.bar(
        top_repos,
        x="total_events",
        y="repo_name",
        orientation='h',
        color="active_contributors",
        color_continuous_scale="Plasma",
        labels={
            "total_events": "Tổng số lượng sự kiện", 
            "repo_name": "Tên Repository", 
            "active_contributors": "Nhà đóng góp (Contributors)"
        }
    )
    
    fig.update_layout(
        yaxis={'categoryorder':'total ascending'},
        paper_bgcolor='rgba(0,0,0,0)',
        plot_bgcolor='rgba(0,0,0,0)',
        xaxis=dict(
            showgrid=True, 
            gridcolor='rgba(255,255,255,0.05)',
            linecolor='rgba(255,255,255,0.1)'
        ),
        coloraxis_colorbar=dict(title="Contributors")
    )
    st.plotly_chart(fig, width="stretch")
    
    st.write(" ")
    st.markdown("### 📊 Chi tiết hoạt động tích hợp của các Repository")
    
    # Hiển thị bảng số liệu đã được định dạng và căn lề đẹp mắt
    formatted_df = repo_daily[[
        "repo_name", "total_events", "push_count", 
        "pull_request_count", "issue_count", 
        "issue_comment_count", "active_contributors"
    ]].sort_values(by="total_events", ascending=False)  # type: ignore
    
    # Thay đổi tiêu đề các cột trong hiển thị
    formatted_df.columns = [
        "Tên Repository", "Tổng Events", "Pushes", 
        "Pull Requests", "Issues", "Comments", "Contributors"
    ]
    
    st.dataframe(
        formatted_df,
        width="stretch",
        hide_index=True
    )
