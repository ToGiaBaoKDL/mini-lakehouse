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

# Định nghĩa Iceberg Schema (được lưu trữ tại REST Catalog)
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

# Định nghĩa PyArrow Schema tương ứng để mapping dữ liệu (khớp tính nullable với Iceberg)
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
    và append vào bảng Iceberg 'landing_api_github.events_raw'.
    """
    print(f"Bắt đầu xử lý file raw: {local_file_path}")
    source_file_name = os.path.basename(local_file_path)
    ingested_at_str = datetime.utcnow().isoformat()
    
    records = []
    
    # Đọc file gzip line-by-line
    with gzip.open(local_file_path, 'rt', encoding='utf-8') as f:
        for line in f:
            if not line.strip():
                continue
            try:
                event = json.loads(line)
                
                # Trích xuất & làm phẳng cấu trúc cơ bản
                actor = event.get("actor", {})
                repo = event.get("repo", {})
                
                # Serialized JSON payload để đảm bảo tính mềm dẻo của landing
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
                # Log lỗi dòng hỏng và tiếp tục
                print(f"Lỗi phân tích dòng JSON: {e}")
                continue

    if not records:
        print("Không có record nào được phân tích thành công.")
        return "No records ingested"

    print(f"Đã phân tích thành công {len(records)} events.")
    
    # Chuyển đổi list records sang PyArrow Table
    arrow_table = pa.Table.from_pylist(records, schema=arrow_schema)
    
    # Kết nối Iceberg Catalog
    catalog = load_catalog("prod")
    table_identifier = "landing_api_github.events_raw"
    
    # Kiểm tra bảng đã tồn tại chưa, nếu chưa thì khởi tạo bảng phân vùng
    try:
        table = catalog.load_table(table_identifier)
        print(f"Bảng {table_identifier} đã tồn tại.")
    except NoSuchTableError:
        print(f"Bảng {table_identifier} chưa tồn tại. Tiến hành khởi tạo...")
        
        # Khởi tạo partition spec theo cột 'source_hour'
        partition_spec = PartitionSpec(
            PartitionField(
                source_id=11,  # field_id của source_hour
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
        print(f"Khởi tạo bảng {table_identifier} thành công.")
        
    # Ghi đè (hoặc Append) dữ liệu vào bảng để đảm bảo tính idempotent
    print(f"Ghi dữ liệu (overwrite) vào partition source_hour='{source_hour_str}'...")
    table.overwrite(
        df=arrow_table,
        overwrite_filter=f"source_hour == '{source_hour_str}'"
    )
    print("Ghi dữ liệu vào Iceberg hoàn tất!")
    
    return f"Ingested {len(records)} records successfully into {table_identifier}"
