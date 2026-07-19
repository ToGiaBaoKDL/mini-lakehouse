import streamlit as st
import pandas as pd
from typing import Optional
from pyiceberg.catalog import Catalog

def render_metadata_inspector(catalog: Optional[Catalog]):
    """
    Render giao diện Trình kiểm tra Siêu dữ liệu (Iceberg Metadata Inspector) thông qua REST Catalog.
    """
    st.subheader("🕵️ Trình kiểm tra Siêu dữ liệu Apache Iceberg qua REST Catalog")
    st.write("Dữ liệu gốc nạp vào Landing layer được quản lý bởi **Apache Iceberg REST Catalog**. Bạn có thể xem lịch sử các Snapshots và tệp siêu dữ liệu `.metadata.json` trực tiếp từ REST Catalog:")
    
    if catalog is None:
        st.warning("Không thể tải Catalog của Iceberg.")
        return

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
