from prefect import serve
from pipelines.flows.ingest_github_archive import ingest_github_archive_hour
from pipelines.flows.transform_github_archive_daily import transform_github_archive_daily

def deploy_flows():
    """
    Đăng ký (deploy) và khởi chạy listener phục vụ cho cả 2 Flows:
    - Flow Ingestion chạy mỗi tiếng một lần (phút thứ 5 hàng giờ).
    - Flow Transformation chạy mỗi ngày một lần (lúc 1:00 AM hàng ngày).
    """
    print("🤖 Đang chuẩn bị đăng ký deployments cho Prefect Server...")
    
    # 1. Deployment cho luồng nạp dữ liệu theo giờ
    ingest_deployment = ingest_github_archive_hour.to_deployment(  # type: ignore
        name="hourly-ingestion",
        cron="5 * * * *",  # Chạy vào phút thứ 5 của mỗi giờ
        description="Flow tải dữ liệu GitHub Archive và nạp đè vào Iceberg Landing Table theo giờ."
    )
    
    # 2. Deployment cho luồng biến đổi dữ liệu hàng ngày
    transform_deployment = transform_github_archive_daily.to_deployment(  # type: ignore
        name="daily-transformation",
        cron="0 1 * * *",  # Chạy lúc 1:00 AM mỗi ngày
        description="Flow thực thi dbt build để biến đổi dữ liệu ClickHouse và chạy các bài data test chất lượng."
    )
    
    print("🚀 Khởi chạy worker phục vụ các lịch trình trên (Nhấn Ctrl+C để dừng)...")
    serve(ingest_deployment, transform_deployment)  # type: ignore

if __name__ == "__main__":
    deploy_flows()
