import plotly.express as px
import streamlit as st

from mini_lakehouse.presentation.data_loader import load_repository_activity

st.title("Repository activity")

try:
    repositories = load_repository_activity()
except Exception as error:
    st.warning(f"Repository mart is not ready: {error}")
    st.stop()

figure = px.bar(
    repositories.sort_values("event_count"),
    x="event_count",
    y="repository_name",
    orientation="h",
    color="active_actor_days",
    labels={"active_actor_days": "Active actor-days"},
)
st.plotly_chart(figure, width="stretch")
st.dataframe(repositories, width="stretch", hide_index=True)
