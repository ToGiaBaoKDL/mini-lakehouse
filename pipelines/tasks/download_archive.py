from prefect import task
from src.ingestion.download import download_github_archive

@task(retries=3, retry_delay_seconds=10)
def download_archive_file(year: int, month: int, day: int, hour: int, target_dir: str) -> str:
    """
    Prefect task bọc logic nghiệp vụ download từ src/
    """
    return download_github_archive(
        year=year,
        month=month,
        day=day,
        hour=hour,
        target_dir=target_dir
    )
