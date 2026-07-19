import streamlit as st
import pandas as pd
from typing import Optional
from pyiceberg.catalog import Catalog
from bi_app.data_loader import get_iceberg_catalog

# Header
st.subheader("🕵️ Trình kiểm tra Siêu dữ liệu Apache Iceberg qua REST Catalog")
st.write("Dữ liệu thô nạp vào Landing layer được quản lý và theo dõi phiên bản bởi **Apache Iceberg**. Bạn có thể xem lịch sử các Snapshots dạng timeline trực tiếp từ REST Catalog:")

catalog: Optional[Catalog] = None
try:
    catalog = get_iceberg_catalog()
except Exception as e:
    st.warning(f"Không thể kết nối Iceberg Catalog: {e}")

if catalog is not None:
    try:
        table_identifier = "landing_api_github.events_raw"
        st.info(f"Đang đọc bảng Iceberg: **{table_identifier}**")
        
        # Tải thông tin bảng qua PyIceberg
        table = catalog.load_table(table_identifier)
        
        # 1. Hiển thị Lịch sử Snapshots dưới dạng Timeline cực kỳ Premium
        st.markdown("### 1. Lịch sử Snapshot (Snapshots History)")
        snapshots = table.metadata.snapshots
        
        if snapshots:
            # Sắp xếp từ snapshot mới nhất đến cũ nhất
            sorted_snapshots = sorted(snapshots, key=lambda x: x.timestamp_ms, reverse=True)
            
            # Thêm CSS cho Timeline (Không sử dụng custom font Outfit)
            st.markdown("""
            <style>
                .timeline {
                    position: relative;
                    padding: 20px 0;
                    margin-left: 10px;
                }
                .timeline-item {
                    position: relative;
                    padding-left: 30px;
                    border-left: 2px solid rgba(255, 255, 255, 0.08);
                    padding-bottom: 25px;
                }
                .timeline-item:last-child {
                    border-left: 2px solid transparent;
                    padding-bottom: 0;
                }
                .timeline-marker {
                    position: absolute;
                    left: -7px;
                    top: 6px;
                    width: 12px;
                    height: 12px;
                    border-radius: 50%;
                    background: linear-gradient(135deg, #FF8a00, #E52e71);
                    box-shadow: 0 0 8px rgba(255, 138, 0, 0.6);
                    transition: all 0.3s ease;
                }
                .timeline-item:hover .timeline-marker {
                    transform: scale(1.3);
                    box-shadow: 0 0 15px rgba(229, 46, 113, 0.9);
                }
                .timeline-content {
                    background: rgba(255, 255, 255, 0.01);
                    border: 1px solid rgba(255, 255, 255, 0.04);
                    border-radius: 12px;
                    padding: 16px;
                    transition: all 0.2s ease;
                }
                .timeline-content:hover {
                    background: rgba(255, 255, 255, 0.02);
                    border-color: rgba(255, 255, 255, 0.08);
                }
                .timeline-time {
                    font-size: 0.8rem;
                    color: #8892b0;
                    font-weight: 500;
                    margin-bottom: 4px;
                }
                .timeline-title {
                    font-size: 0.95rem;
                    font-weight: 700;
                    color: #e2e8f0;
                    margin-bottom: 6px;
                }
                .timeline-detail {
                    font-size: 0.8rem;
                    color: #8892b0;
                    word-break: break-all;
                }
            </style>
            """, unsafe_allow_html=True)
            
            # Khởi tạo chuỗi HTML dạng một dòng liên tục không xuống dòng (tránh markdown parser tự ý escape html)
            timeline_html = '<div class="timeline">'
            for snap in sorted_snapshots:
                commit_time = pd.to_datetime(snap.timestamp_ms, unit="ms").strftime('%Y-%m-%d %H:%M:%S UTC')
                parent_id = str(snap.parent_snapshot_id) if snap.parent_snapshot_id else "None (Khởi tạo bảng)"
                item_html = (
                    f"<div class='timeline-item'>"
                    f"<div class='timeline-marker'></div>"
                    f"<div class='timeline-content'>"
                    f"<div class='timeline-time'>⏱️ Commit Time: {commit_time}</div>"
                    f"<div class='timeline-title'>Snapshot ID: {snap.snapshot_id}</div>"
                    f"<div class='timeline-detail'>"
                    f"<strong>Parent ID:</strong> {parent_id}<br/>"
                    f"<strong>Manifest List:</strong> <code style='font-size: 0.75rem; color: #4facfe;'>{snap.manifest_list}</code>"
                    f"</div>"
                    f"</div>"
                    f"</div>"
                )
                timeline_html += item_html
            timeline_html += '</div>'
            st.markdown(timeline_html, unsafe_allow_html=True)
        else:
            st.info("Bảng Iceberg Landing chưa có snapshot nào.")
            
        st.write(" ")
        st.write(" ")
        
        # 2. Hiển thị tệp tin Metadata Log
        st.markdown("### 2. Các tệp Siêu dữ liệu cấu trúc lịch sử (Metadata Log Files)")
        metadata_log = table.metadata.metadata_log
        if metadata_log:
            meta_records = []
            for entry in metadata_log:
                meta_records.append({
                    "File Metadata JSON": entry.metadata_file,
                    "Thời gian tạo": pd.to_datetime(entry.timestamp_ms, unit="ms")
                })
            meta_df = pd.DataFrame(meta_records)
            st.dataframe(
                meta_df.sort_values(by="Thời gian tạo", ascending=False),
                width="stretch",
                hide_index=True
            )
        else:
            st.info("Bảng Iceberg chưa ghi nhận tệp log metadata lịch sử nào.")
            
    except Exception as e:
        st.error(f"Lỗi khi đọc metadata Iceberg thông qua REST Catalog: {e}")
