from datetime import UTC, datetime

import pandas as pd
import streamlit as st

from mini_lakehouse.contracts import GITHUB_EVENTS_RAW
from mini_lakehouse.presentation.data_loader import get_iceberg_catalog

st.title("Iceberg metadata")
st.caption("Operational view of the immutable GitHub Archive landing table")

try:
    table = get_iceberg_catalog().load_table(GITHUB_EVENTS_RAW.iceberg)
except Exception as error:
    st.warning(f"Landing table is not ready: {error}")
    st.stop()

snapshots = [
    {
        "snapshot_id": snapshot.snapshot_id,
        "parent_snapshot_id": snapshot.parent_snapshot_id,
        "committed_at": datetime.fromtimestamp(snapshot.timestamp_ms / 1000, tz=UTC),
        "operation": snapshot.summary.operation if snapshot.summary else None,
    }
    for snapshot in table.metadata.snapshots
]
st.dataframe(
    pd.DataFrame(snapshots).sort_values("committed_at", ascending=False),
    width="stretch",
    hide_index=True,
)
