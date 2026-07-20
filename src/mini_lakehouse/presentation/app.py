import streamlit as st

st.set_page_config(page_title="Mini Lakehouse", page_icon="🧊", layout="wide")

pages = [
    st.Page("pages/overview.py", title="Engineering overview", icon="📊", default=True),
    st.Page("pages/repositories.py", title="Repository activity", icon="📈"),
    st.Page("pages/iceberg_metadata.py", title="Iceberg metadata", icon="🧊"),
]

st.navigation(pages).run()
