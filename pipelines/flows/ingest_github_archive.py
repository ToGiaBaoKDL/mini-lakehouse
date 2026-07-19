import os
from datetime import datetime, timedelta

from prefect import flow
from pipelines.tasks.download_archive import download_archive_file
from pipelines.tasks.append_landing_events import append_to_landing_table
from pipelines.tasks.run_dbt_models import run_dbt_build

@flow(name="ingest-github-archive-hour")
def ingest_github_archive_hour(year: int, month: int, day: int, hour: int):
    """
    Prefect flow chạy theo giờ:
    1. Tải file raw GitHub Archive (.json.gz).
    2. Phân tích PyArrow & nạp đè dữ liệu vào partition của Landing Iceberg table.
    3. Thực thi dbt build để biến đổi dữ liệu trực tiếp trong ClickHouse.
    """
    # 1. Định nghĩa các đường dẫn
    base_warehouse = os.getenv("PYICEBERG_CATALOG__PROD__WAREHOUSE", "/home/hcm-mki-l6009/projects/mini-lakehouse/warehouse")
    raw_archive_dir = os.path.join(base_warehouse, "raw-files", "api", "github")
    
    # Cấu hình dbt
    dbt_project_dir = "./dbt_project"
    dbt_profiles_dir = "./dbt_project"
    
    # 2. Tạo chuỗi source_hour định dạng 'YYYY-MM-DD-H'
    source_hour_str = f"{year}-{month:02d}-{day:02d}-{hour}"
    
    # 3. Tải file raw (.json.gz)
    local_file = download_archive_file(
        year=year,
        month=month,
        day=day,
        hour=hour,
        target_dir=raw_archive_dir
    )
    
    # 4. Ingest file raw vào Landing Iceberg Table (idempotent overwrite)
    ingest_result = append_to_landing_table(
        local_file_path=local_file,
        source_hour_str=source_hour_str
    )
    print(ingest_result)
    
    # 5. Chạy dbt build biến đổi dữ liệu trực tiếp trên ClickHouse
    dbt_result = run_dbt_build(
        project_dir=dbt_project_dir,
        profiles_dir=dbt_profiles_dir
    )
    print(dbt_result)
    
    print("Toàn bộ pipeline ClickHouse đã hoàn thành xuất sắc!")
    return "Pipeline execution successful"

if __name__ == "__main__":
    # Chọn thời điểm chạy thử (lùi 1 ngày) để đảm bảo dữ liệu archive có sẵn trên GH Archive
    yesterday = datetime.utcnow() - timedelta(days=1)
    
    print(f"Bắt đầu chạy thử nghiệm toàn bộ Pipeline cho thời điểm: {yesterday.strftime('%Y-%m-%d %H:00:00')}")
    ingest_github_archive_hour(
        year=yesterday.year,
        month=yesterday.month,
        day=yesterday.day,
        hour=yesterday.hour
    )
