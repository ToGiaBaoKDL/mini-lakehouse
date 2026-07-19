# Giai đoạn 4: Vận hành Pipeline & Truy vấn kết quả (ClickHouse)

Tài liệu này hướng dẫn cách thực thi các pipeline tự động và cách kết nối truy vấn dữ liệu từ các bảng ClickHouse kết quả.

---

## 1. Khởi chạy Thủ công theo yêu cầu (On-Demand Runs)

Bạn có thể chạy thử trực tiếp các script Python để chạy flow ngay lập tức:

### A. Chạy Ingestion theo giờ:
```bash
PYTHONPATH=. uv run --env-file .env python pipelines/flows/ingest_github_archive.py
```

### B. Chạy dbt Transformation hàng ngày:
```bash
PYTHONPATH=. uv run --env-file .env python pipelines/flows/transform_github_archive_daily.py
```

---

## 2. Khởi chạy tự động theo Lịch trình (Scheduled Deployments)

Để tự động lập lịch chạy cho các flow (Hourly Ingestion và Daily dbt Transform) tương tự như mô hình Airflow DAGs, ta sử dụng tệp cấu hình khai báo [prefect.yaml](file:///home/hcm-mki-l6009/projects/mini-lakehouse/prefect.yaml):

### Bước A: Đăng ký toàn bộ Flow lên Prefect Server
```bash
# Đăng ký toàn bộ các deployments được khai báo trong prefect.yaml
uv run prefect deploy --all
```

### Bước B: Chạy Worker lắng nghe lịch biểu
```bash
# Chạy worker lắng nghe work-pool tương ứng để thực thi task khi đến giờ hẹn
uv run prefect worker start --pool 'local-pool'
```
*Lưu ý*: Luồng nạp dữ liệu chạy đúng **phút 0 hàng giờ** (`0 * * * *`), còn luồng biến đổi dbt chạy vào đúng **8:00 AM hàng ngày** (`0 8 * * *`).

---

## 2. Hướng dẫn Truy vấn Bảng kết quả trong ClickHouse bằng Python

Dữ liệu phân tích đã được tổng hợp dưới dạng các bảng ClickHouse MergeTree cực kỳ nhanh. Dưới đây là cách sử dụng thư viện **`clickhouse-connect`** trong Python để truy vấn:

```python
import clickhouse_connect

def query_lakehouse():
    # 1. Khởi tạo kết nối ClickHouse HTTP client
    client = clickhouse_connect.get_client(
        host='localhost', 
        port=8123, 
        username='dev_user', 
        password='dev_password', 
        database='github_lakehouse'
    )
    
    # 2. Truy vấn bảng Analytics
    print("--- 10 Repository hoạt động tích cực nhất ---")
    query = """
        SELECT activity_date, repo_name, total_events, push_count, active_contributors
        FROM default_analytics_engineering.repository_activity_daily
        ORDER BY total_events DESC
        LIMIT 10;
    """
    df = client.query_df(query)
    print(df)

if __name__ == "__main__":
    query_lakehouse()
```

