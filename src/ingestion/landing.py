import gzip
import json
import os
from datetime import datetime, timezone
import pyarrow as pa
from pyiceberg.catalog import load_catalog
from pyiceberg.exceptions import NoSuchTableError, NamespaceAlreadyExistsError
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
from src.utils.config import settings

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

def append_json_archive_to_landing(file_path: str, source_hour_str: str) -> str:
    """
    Đọc dữ liệu từ file raw .json.gz (trên S3 hoặc Local), chuyển sang PyArrow Table
    và append/overwrite vào bảng Iceberg 'landing.api.github.events_raw'.
    """
    logger.info("Bắt đầu xử lý file raw: %s", file_path)
    source_file_name = os.path.basename(file_path)
    ingested_at_str = datetime.now(timezone.utc).isoformat()

    records = []
    error_count = 0
    total_lines = 0

    # Đọc từ S3 nếu đường dẫn bắt đầu bằng s3://, ngược lại đọc local
    if file_path.startswith("s3://"):
        from src.utils.s3 import get_s3_fs
        fs = get_s3_fs()
        open_func = lambda: fs.open(file_path, 'rb')
    else:
        open_func = lambda: open(file_path, 'rb')

    with open_func() as raw_file:
        with gzip.open(raw_file, 'rt', encoding='utf-8') as f:
            for line in f:
                if not line.strip():
                    continue
                total_lines += 1
                try:
                    event = json.loads(line)

                    # Trích xuất dữ liệu phẳng hóa cơ bản theo cấu trúc Schema
                    actor = event.get("actor", {})
                    repo = event.get("repo", {})

                    # Payload được giữ nguyên định dạng JSON string để parsing linh hoạt ở tầng sau
                    payload_str = json.dumps(event.get("payload", {}))

                    record = {
                        "id": str(event.get("id")),
                        "type": event.get("type"),
                        "actor_id": actor.get("id") if actor else None,
                        "actor_login": actor.get("login") if actor else None,
                        "repo_id": repo.get("id") if repo else None,
                        "repo_name": repo.get("name") if repo else None,
                        "payload": payload_str,
                        "public": event.get("public"),
                        "created_at": event.get("created_at"),
                        "source_file": source_file_name,
                        "source_hour": source_hour_str,
                        "ingested_at": ingested_at_str
                    }
                    records.append(record)
                except Exception as e:
                    error_count += 1
                    if error_count <= 5:
                        logger.warning("Bỏ qua dòng lỗi trong tệp JSON: %s. Chi tiết: %s", line[:100], e)

    # Đánh giá chất lượng dữ liệu: Đảm bảo tỷ lệ hỏng cấu trúc không quá cao
    if total_lines > 0:
        error_ratio = error_count / total_lines
        logger.info("Hoàn thành phân tích: Tổng số dòng=%d, Dòng lỗi=%d (Tỷ lệ: %.2f%%)", total_lines, error_count, error_ratio * 100)
        # Nếu tỷ lệ lỗi quá cao (> 5%), dừng tiến trình để kiểm tra cấu trúc API
        if error_ratio > 0.05:
            raise ValueError(f"Tỷ lệ dòng hỏng vượt quá giới hạn an toàn (>5%): {error_ratio*100:.2f}%")

    if not records:
        logger.warning("Không có record nào được phân tích thành công từ file %s.", file_path)
        return "No records ingested"

    logger.info("Đã phân tích thành công %d/%d events.", len(records), total_lines)

    arrow_table = pa.Table.from_pylist(records, schema=arrow_schema)

    catalog = load_catalog("prod")
    table_identifier = "landing.api.github.events_raw"

    # Đảm bảo namespace đa cấp tồn tại trong catalog (tạo từng cấp một cho Polaris)
    namespace_parts = table_identifier.split(".")[:-1]
    for i in range(1, len(namespace_parts) + 1):
        sub_namespace = tuple(namespace_parts[:i])
        try:
            # Gán vị trí S3 cho namespace để các bảng con tự động kế thừa S3 path.
            # Ví dụ: ("landing", "api") -> s3://landing/api
            bucket_name = sub_namespace[0]
            remaining_path = "/".join(sub_namespace[1:])
            ns_location = f"s3://{bucket_name}/{remaining_path}" if remaining_path else f"s3://{bucket_name}"
            catalog.create_namespace(sub_namespace, properties={"location": ns_location})
            logger.info("Đã tạo namespace: %s tại location: %s", ".".join(sub_namespace), ns_location)
        except NamespaceAlreadyExistsError:
            pass

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
        # Khởi tạo bảng Iceberg với Format V2, để catalog tự động gán đường dẫn mặc định chuẩn dưới namespace
        table = catalog.create_table(
            identifier=table_identifier,
            schema=iceberg_schema,
            partition_spec=partition_spec,
            properties={"format-version": "2"}
        )
        logger.info("Khởi tạo bảng %s (Format V2) thành công tại location: %s", table_identifier, table.location)

    logger.info("Ghi đè (dynamic partition overwrite) dữ liệu bằng PyIceberg 0.11...")
    table.dynamic_partition_overwrite(df=arrow_table)
    logger.info("Ghi dữ liệu vào Iceberg hoàn tất!")

    return f"Ingested {len(records)} records successfully into {table_identifier}"
