from prefect import task
from src.ingestion.landing import append_json_archive_to_landing

@task
def append_to_landing_table(file_path: str, source_hour_str: str) -> str:
    """
    Prefect task bọc logic nghiệp vụ nạp dữ liệu landing từ src/
    """
    return append_json_archive_to_landing(
        file_path=file_path,
        source_hour_str=source_hour_str
    )
