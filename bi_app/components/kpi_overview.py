import streamlit as st
import pandas as pd
import plotly.express as px

def render_kpi_overview(events: pd.DataFrame, repos: pd.DataFrame, actors: pd.DataFrame, contrib_daily: pd.DataFrame):
    """
    Render giao diện Dashboard Tổng quan KPIs dạng Glassmorphism và Đồ thị Plotly cao cấp.
    """
    st.subheader("📌 Tổng quan Hồ chứa dữ liệu (Lakehouse KPIs)")
    
    # Tính toán thời gian ingest gần nhất
    last_ingest = pd.to_datetime(events["ingested_at"]).max().strftime('%Y-%m-%d %H:%M') if len(events) > 0 else "N/A"
    
    # Render các KPI Cards bằng HTML/CSS tùy chỉnh
    st.markdown(f"""
    <div class="kpi-card-row">
        <div class="kpi-card">
            <div class="kpi-title">Tổng sự kiện (Curated)</div>
            <div class="kpi-value" style="background: linear-gradient(135deg, #00f2fe, #4facfe); -webkit-background-clip: text; -webkit-text-fill-color: transparent;">
                {len(events):,}
            </div>
            <div class="kpi-footer" style="color: #4facfe;">⚡ clickhouse-connect</div>
        </div>
        <div class="kpi-card">
            <div class="kpi-title">Repositories hoạt động</div>
            <div class="kpi-value" style="background: linear-gradient(135deg, #FF8a00, #E52e71); -webkit-background-clip: text; -webkit-text-fill-color: transparent;">
                {len(repos):,}
            </div>
            <div class="kpi-footer" style="color: #E52e71;">📂 dimension table</div>
        </div>
        <div class="kpi-card">
            <div class="kpi-title">Nhà phát triển (Actors)</div>
            <div class="kpi-value" style="background: linear-gradient(135deg, #9b5de5, #f15bb5); -webkit-background-clip: text; -webkit-text-fill-color: transparent;">
                {len(actors):,}
            </div>
            <div class="kpi-footer" style="color: #f15bb5;">👥 active developers</div>
        </div>
        <div class="kpi-card">
            <div class="kpi-title">Thời gian Ingest gần nhất</div>
            <div class="kpi-value" style="font-size: 1.8rem; padding-top: 10px; padding-bottom: 10px; background: linear-gradient(135deg, #00bbf9, #00f2fe); -webkit-background-clip: text; -webkit-text-fill-color: transparent;">
                {last_ingest}
            </div>
            <div class="kpi-footer" style="color: #00bbf9;">⏰ hourly ingestion</div>
        </div>
    </div>
    """, unsafe_allow_html=True)
    
    st.write(" ")
    st.write(" ")
    
    # Render các Biểu đồ chất lượng cao
    col_chart1, col_chart2 = st.columns(2)
    
    with col_chart1:
        st.markdown("### 💡 Tỷ lệ loại sự kiện (Event Types)")
        event_counts = events["type"].value_counts().reset_index()  # type: ignore
        event_counts.columns = ["Event Type", "Count"]
        
        # Pie chart tinh chỉnh màu sắc hài hòa
        fig1 = px.pie(
            event_counts, 
            names="Event Type", 
            values="Count", 
            hole=0.45,
            color_discrete_sequence=px.colors.qualitative.Pastel
        )
        fig1.update_layout(
            paper_bgcolor='rgba(0,0,0,0)',
            plot_bgcolor='rgba(0,0,0,0)',
            font=dict(family="Outfit, sans-serif", size=13, color="#e2e8f0"),
            legend=dict(orientation="h", yanchor="bottom", y=-0.2, xanchor="center", x=0.5)
        )
        st.plotly_chart(fig1, width="stretch")
        
    with col_chart2:
        st.markdown("### 📈 Hoạt động đóng góp theo ngày (Events)")
        daily_contrib = contrib_daily.groupby("activity_date")["total_events"].sum().reset_index()  # type: ignore
        
        # Line chart mượt mà sử dụng gradient màu cam ấm
        fig2 = px.line(
            daily_contrib, 
            x="activity_date", 
            y="total_events",
            labels={"activity_date": "Ngày", "total_events": "Số lượng events"},
            line_shape="spline",
            color_discrete_sequence=["#FF8a00"]
        )
        fig2.update_layout(
            paper_bgcolor='rgba(0,0,0,0)',
            plot_bgcolor='rgba(0,0,0,0)',
            font=dict(family="Outfit, sans-serif", size=13, color="#e2e8f0"),
            xaxis=dict(
                showgrid=True, 
                gridcolor='rgba(255,255,255,0.05)',
                linecolor='rgba(255,255,255,0.1)'
            ),
            yaxis=dict(
                showgrid=True, 
                gridcolor='rgba(255,255,255,0.05)',
                linecolor='rgba(255,255,255,0.1)'
            )
        )
        st.plotly_chart(fig2, width="stretch")
