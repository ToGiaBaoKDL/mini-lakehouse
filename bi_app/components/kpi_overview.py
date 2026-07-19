import streamlit as st
import pandas as pd
import plotly.express as px

def render_kpi_overview(events: pd.DataFrame, repos: pd.DataFrame, actors: pd.DataFrame, contrib_daily: pd.DataFrame):
    """
    Render giao diện Dashboard Tổng quan KPIs và Biểu đồ phân tích.
    """
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
        last_ingest = pd.to_datetime(events["ingested_at"]).max().strftime('%Y-%m-%d %H:%M') if len(events) > 0 else "N/A"
        st.metric("Thời gian Ingest gần nhất", last_ingest)
        
    st.write("---")
    
    # Biểu đồ cột song song
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
