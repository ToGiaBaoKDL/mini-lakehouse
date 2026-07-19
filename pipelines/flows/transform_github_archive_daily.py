from prefect import flow, get_run_logger
from pipelines.tasks.run_dbt_models import run_dbt_build
from src.utils.config import settings

@flow(name="transform-github-archive-daily")
def transform_github_archive_daily():
    """
    Prefect flow chạy hàng ngày:
    Thực thi dbt build (gồm chạy model và test) để biến đổi dữ liệu trong ClickHouse.
    """
    logger = get_run_logger()
    logger.info("Bắt đầu chạy dbt build hàng ngày...")
    
    dbt_project_dir = "./dbt_project"
    dbt_profiles_dir = settings.dbt_profiles_dir
    
    dbt_result = run_dbt_build(
        project_dir=dbt_project_dir,
        profiles_dir=dbt_profiles_dir
    )
    logger.info("Kết quả dbt transformation hàng ngày: %s", dbt_result)
    logger.info("Pipeline biến đổi dữ liệu hàng ngày đã hoàn thành xuất sắc!")
    return "Transformation successful"

if __name__ == "__main__":
    print("Bắt đầu chạy thử nghiệm biến đổi dữ liệu hàng ngày...")
    transform_github_archive_daily()
