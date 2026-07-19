import gzip
import json
import os
from datetime import datetime
import pyarrow as pa
from pyiceberg.catalog import load_catalog
from pyiceberg.exceptions import NoSuchTableError
from pyiceberg.schema import Schema
from pyiceberg.types import (
    BooleanType,
    LongType,
    StringType,
    NestedField
)
from pyiceberg.partitioning import PartitionSpec, PartitionField
from pyiceberg.transforms import IdentityTransform
from src.utils.logging import get_logger

logger = get_logger("lakehouse.ingestion.landing")

# Định nghĩa Iceberg Schema (REST Catalog)
iceberg_schema = Schema(
    NestedField(field_id=1, name="id", field_type=StringType(), required=True),
    NestedField(field_id=2, name="type", field_type=StringType(), required=False),
    NestedField(field_id=3, name="actor_id", field_type=LongType(), required=False),
    NestedField(field_id=4, name="actor_login", field_type=StringType(), required=False),
    NestedField(field_id=5, name="repo_id", field_type=LongType(), required=False),
    NestedField(field_id=6, name="repo_name", field_type=StringType(), required=False),
    NestedField(field_id=7, name="payload", field_type=StringType(), required=False),
    NestedField(field_id=8, name="public", field_type=BooleanType(), required=False),
    NestedField(field_id=9, name="created_at", field_type=StringType(), required=False),
    NestedField(field_id=10, name="source_file", field_type=StringType(), required=False),
    NestedField(field_id=11, name="source_hour", field_type=StringType(), required=False),
    NestedField(field_id=12, name="ingested_at", field_type=StringType(), required=False),
)

# Định nghĩa PyArrow Schema tương ứng để mapping dữ liệu
arrow_schema = pa.schema([
    pa.field("id", pa.string(), nullable=False),
    pa.field("type", pa.string(), nullable=True),
    pa.field("actor_id", pa.int64(), nullable=True),
    pa.field("actor_login", pa.string(), nullable=True),
    pa.field("repo_id", pa.int64(), nullable=True),
    pa.field("repo_name", pa.string(), nullable=True),
    pa.field("payload", pa.string(), nullable=True),
    pa.field("public", pa.bool_(), nullable=True),
    pa.field("created_at", pa.string(), nullable=True),
    pa.field("source_file", pa.string(), nullable=True),
    pa.field("source_hour", pa.string(), nullable=True),
    pa.field("ingested_at", pa.string(), nullable=True),
])

def append_json_archive_to_landing(local_file_path: str, source_hour_str: str) -> str:
    """
    Đọc dữ liệu từ file raw .json.gz, chuyển sang PyArrow Table 
    và append/overwrite vào bảng Iceberg 'landing_api_github.events_raw'.
    """
    logger.info("Bắt đầu xử lý file raw: %s", local_file_path)
    source_file_name = os.path.basename(local_file_path)
    ingested_at_str = datetime.utcnow().isoformat()
    
    records = []
    error_count = 0
    total_lines = 0
    
    with gzip.open(local_file_path, 'rt', encoding='utf-8') as f:
        for line in f:
            if not line.strip():
                continue
            total_lines += 1
            try:
                event = json.loads(line)
                actor = event.get("actor", {})
                repo = event.get("repo", {})
                payload_dict = event.get("payload", {})
                payload_str = json.dumps(payload_dict) if payload_dict else "{}"
                
                records.append({
                    "id": str(event.get("id")),
                    "type": event.get("type"),
                    "actor_id": int(actor.get("id")) if actor and actor.get("id") is not None else None,
                    "actor_login": actor.get("login") if actor else None,
                    "repo_id": int(repo.get("id")) if repo and repo.get("id") is not None else None,
                    "repo_name": repo.get("name") if repo else None,
                    "payload": payload_str,
                    "public": bool(event.get("public")),
                    "created_at": event.get("created_at"),
                    "source_file": source_file_name,
                    "source_hour": source_hour_str,
                    "ingested_at": ingested_at_str,
                })
            except Exception as e:
                error_count += 1
                logger.warning("Lỗi phân tích dòng JSON số %d: %s", total_lines, e)
                continue

    if error_count > 0:
        error_ratio = error_count / total_lines if total_lines > 0 else 0
        logger.warning("Đã xảy ra %d dòng lỗi trên tổng số %d dòng (Tỷ lệ: %.2f%%)", error_count, total_lines, error_ratio * 100)
        # Nếu tỷ lệ lỗi quá cao (> 5%), dừng tiến trình để kiểm tra cấu trúc API
        if error_ratio > 0.05:
            raise ValueError(f"Tỷ lệ dòng hỏng vượt quá giới hạn an toàn (>5%): {error_ratio*100:.2f}%")

    if not records:
        logger.warning("Không có record nào được phân tích thành công từ file %s.", local_file_path)
        return "No records ingested"

    logger.info("Đã phân tích thành công %d/%d events.", len(records), total_lines)
    
    arrow_table = pa.Table.from_pylist(records, schema=arrow_schema)
    
    catalog = load_catalog("prod")
    table_identifier = "landing_api_github.events_raw"
    
    try:
        table = catalog.load_table(table_identifier)
        logger.info("Bảng %s đã tồn tại.", table_identifier)
    except NoSuchTableError:
        logger.info("Bảng %s chưa tồn tại. Tiến hành khởi tạo...", table_identifier)
        partition_spec = PartitionSpec(
            PartitionField(
                source_id=11,
                field_id=1000,
                transform=IdentityTransform(),
                name="source_hour"
            )
        )
        table = catalog.create_table(
            identifier=table_identifier,
            schema=iceberg_schema,
            partition_spec=partition_spec
        )
        logger.info("Khởi tạo bảng %s thành công.", table_identifier)
        
    logger.info("Ghi đè (overwrite) dữ liệu vào partition source_hour='%s'...", source_hour_str)
    table.overwrite(
        df=arrow_table,
        overwrite_filter=f"source_hour == '{source_hour_str}'"
    )
    logger.info("Ghi dữ liệu vào Iceberg hoàn tất!")
    
    return f"Ingested {len(records)} records successfully into {table_identifier}"
