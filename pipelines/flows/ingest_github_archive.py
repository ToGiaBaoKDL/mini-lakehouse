import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from prefect import flow, get_run_logger
from pipelines.tasks.download_archive import download_archive_file
from pipelines.tasks.append_landing_events import append_to_landing_table
from src.utils.config import settings

@flow(name="ingest-github-archive-hour")
def ingest_github_archive_hour(
    year: Optional[int] = None,
    month: Optional[int] = None,
    day: Optional[int] = None,
    hour: Optional[int] = None
):
    """
    Prefect flow chạy theo giờ:
    1. Tự động tính toán target_time bằng thời điểm hiện tại trừ 1 giờ nếu không truyền tham số (chạy theo lịch).
    2. Tải file raw GitHub Archive (.json.gz).
    3. Phân tích PyArrow & nạp đè dữ liệu vào partition của Landing Iceberg table.
    """
    logger = get_run_logger()
    
    # Tự động tính toán thời gian chạy nếu không truyền tham số (chạy theo lịch của scheduler)
    if year is None or month is None or day is None or hour is None:
        target_time = datetime.now(timezone.utc) - timedelta(hours=1)
        year = target_time.year
        month = target_time.month
        day = target_time.day
        hour = target_time.hour
        logger.info("Chạy tự động theo lịch. Tự động tính toán target_time: %s H%d", target_time.strftime('%Y-%m-%d'), hour)
    
    source_hour_str = f"{year}-{month:02d}-{day:02d}-{hour}"
    
    logger.info("Bắt đầu chạy Ingestion cho source_hour=%s", source_hour_str)
    
    # 1. Tải file raw (lưu thẳng vào SeaweedFS S3, trả về đường dẫn s3://)
    s3_file_path = download_archive_file(
        year=year,
        month=month,
        day=day,
        hour=hour,
        target_dir=""
    )
    
    # 2. Nạp dữ liệu vào Landing table từ S3
    ingest_result = append_to_landing_table(
        file_path=s3_file_path,
        source_hour_str=source_hour_str
    )
    logger.info("Kết quả Landing Ingestion: %s", ingest_result)
    
    logger.info("Pipeline Ingestion theo giờ đã hoàn thành xuất sắc!")
    return "Ingestion successful"

if __name__ == "__main__":
    from src.utils.logging import get_logger
    logger = get_logger("lakehouse.pipelines.ingest")
    
    logger.info("Kích hoạt chạy Ingestion thủ công...")
    ingest_github_archive_hour()
