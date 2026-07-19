import streamlit as st
import pandas as pd
import plotly.express as px

def render_repository_activity(repo_daily: pd.DataFrame):
    """
    Render giao diện phân tích chi tiết hoạt động của các Repository.
    """
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
        labels={
            "total_events": "Tổng số lượng sự kiện", 
            "repo_name": "Tên Repository", 
            "active_contributors": "Contributors hoạt động"
        },
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
        repo_daily[[
            "repo_name", "total_events", "push_count", 
            "pull_request_count", "issue_count", 
            "issue_comment_count", "active_contributors"
        ]].sort_values(by="total_events", ascending=False),  # type: ignore
        use_container_width=True
    )
