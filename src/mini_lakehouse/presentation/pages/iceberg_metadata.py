from datetime import UTC, datetime

import pandas as pd
import streamlit as st

from mini_lakehouse.config import get_settings
from mini_lakehouse.storage.iceberg import discover_tables, load_iceberg_catalog

st.title("Iceberg metadata")
st.caption("Operational snapshot history for a discovered Iceberg table")

try:
    with load_iceberg_catalog(get_settings()) as catalog:
        identifiers = {
            ".".join(identifier.iceberg): identifier
            for identifier in sorted(discover_tables(catalog), key=lambda item: item.iceberg)
        }
except Exception as error:
    st.warning(f"Iceberg catalog is not ready: {error}")
    st.stop()

if not identifiers:
    st.info("No Iceberg tables are available yet.")
    st.stop()

selected = st.selectbox("Table", tuple(identifiers))
identifier = identifiers[selected]

try:
    with load_iceberg_catalog(get_settings()) as catalog:
        table = catalog.load_table(identifier.iceberg)
        snapshots = [
            {
                "snapshot_id": snapshot.snapshot_id,
                "parent_snapshot_id": snapshot.parent_snapshot_id,
                "committed_at": datetime.fromtimestamp(snapshot.timestamp_ms / 1000, tz=UTC),
                "operation": snapshot.summary.operation if snapshot.summary else None,
            }
            for snapshot in table.metadata.snapshots
        ]
except Exception as error:
    st.warning(f"Table metadata is not readable: {error}")
    st.stop()

st.dataframe(
    pd.DataFrame(snapshots).sort_values("committed_at", ascending=False),
    width="stretch",
    hide_index=True,
)
