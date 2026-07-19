import os
from datetime import datetime, timedelta, timezone

from prefect import flow, get_run_logger
from pipelines.tasks.download_archive import download_archive_file
from pipelines.tasks.append_landing_events import append_to_landing_table
from pipelines.tasks.run_dbt_models import run_dbt_build
from src.utils.config import settings

@flow(name="ingest-github-archive-hour")
def ingest_github_archive_hour(year: int, month: int, day: int, hour: int):
    """
    Prefect flow chạy theo giờ:
    1. Tải file raw GitHub Archive (.json.gz).
    2. Phân tích PyArrow & nạp đè dữ liệu vào partition của Landing Iceberg table.
    3. Thực thi dbt build để biến đổi dữ liệu trực tiếp trong ClickHouse.
    """
    logger = get_run_logger()
    
    # Sử dụng cấu hình nạp từ Pydantic Settings
    base_warehouse = settings.warehouse_path
    raw_archive_dir = os.path.join(base_warehouse, "raw-files", "api", "github")
    
    dbt_project_dir = "./dbt_project"
    dbt_profiles_dir = settings.dbt_profiles_dir
    
    source_hour_str = f"{year}-{month:02d}-{day:02d}-{hour}"
    
    logger.info("Bắt đầu chạy pipeline cho source_hour=%s", source_hour_str)
    
    # 1. Tải file raw
    local_file = download_archive_file(
        year=year,
        month=month,
        day=day,
        hour=hour,
        target_dir=raw_archive_dir
    )
    
    # 2. Nạp dữ liệu vào Landing table
    ingest_result = append_to_landing_table(
        local_file_path=local_file,
        source_hour_str=source_hour_str
    )
    logger.info("Kết quả Landing Ingestion: %s", ingest_result)
    
    # 3. Chạy dbt build biến đổi trên ClickHouse
    dbt_result = run_dbt_build(
        project_dir=dbt_project_dir,
        profiles_dir=dbt_profiles_dir
    )
    logger.info("Kết quả dbt transformation: %s", dbt_result)
    
    logger.info("Toàn bộ pipeline ClickHouse đã hoàn thành xuất sắc!")
    return "Pipeline execution successful"

if __name__ == "__main__":
    # Lùi lại 1 ngày để đảm bảo dữ liệu archive có sẵn
    yesterday = datetime.now(timezone.utc) - timedelta(days=1)
    
    print(f"Bắt đầu chạy thử nghiệm toàn bộ Pipeline cho thời điểm: {yesterday.strftime('%Y-%m-%d %H:00:00')}")
    ingest_github_archive_hour(
        year=yesterday.year,
        month=yesterday.month,
        day=yesterday.day,
        hour=yesterday.hour
    )
