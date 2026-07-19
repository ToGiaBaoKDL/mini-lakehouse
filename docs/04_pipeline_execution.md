# Giai đoạn 4: Vận hành Pipeline & Truy vấn kết quả (ClickHouse)

Tài liệu này hướng dẫn cách thực thi các pipeline tự động và cách kết nối truy vấn dữ liệu từ các bảng ClickHouse kết quả.

---

## 1. Hướng dẫn chạy các Pipeline điều phối (Prefect Flows)

Để tối ưu hóa tài nguyên và đảm bảo tính chuẩn xác cho production, luồng dữ liệu được tách biệt thành hai flow độc lập:

### A. Pipeline nạp dữ liệu theo giờ (Hourly Ingestion)
Flow này chỉ thực hiện tải tệp raw `.json.gz` từ GitHub Archive và nạp đè dữ liệu thô vào phân vùng Landing Iceberg tương ứng.

```bash
PYTHONPATH=. uv run --env-file .env python pipelines/flows/ingest_github_archive.py
```

### B. Pipeline biến đổi dữ liệu hàng ngày (Daily dbt Transformation)
Flow này chạy một lần mỗi ngày để thực thi `dbt build` (gồm cả biến đổi dữ liệu thành các bảng ClickHouse MergeTree và chạy toàn bộ 16 bài kiểm định chất lượng dữ liệu).

```bash
PYTHONPATH=. uv run --env-file .env python pipelines/flows/transform_github_archive_daily.py
```

*Lưu ý*: Cả hai lệnh đều sử dụng `--env-file .env` để `uv` tự động nạp các biến cấu hình từ file `.env` vào shell trước khi chạy, giúp code Python hoàn toàn độc lập và sạch sẽ.

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

