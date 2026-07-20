import plotly.express as px
import streamlit as st

from mini_lakehouse.presentation.data_loader import load_contributor_trend, load_overview

st.title("Engineering activity")
st.caption("Public dbt marts owned by Engineering Analytics")

try:
    overview = load_overview().iloc[0]
    trend = load_contributor_trend()
except Exception as error:
    st.warning(f"Analytics marts are not ready: {error}")
    st.stop()

columns = st.columns(4)
columns[0].metric("Events", f"{int(overview['event_count'] or 0):,}")
columns[1].metric("Push events", f"{int(overview['push_event_count'] or 0):,}")
columns[2].metric("Pushed commits", f"{int(overview['pushed_commit_count'] or 0):,}")
columns[3].metric("PR events", f"{int(overview['pull_request_event_count'] or 0):,}")

figure = px.line(trend, x="activity_date", y="event_count", markers=True)
figure.update_layout(xaxis_title="UTC date", yaxis_title="Events")
st.plotly_chart(figure, width="stretch")
