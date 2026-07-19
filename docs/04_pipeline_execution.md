# Giai đoạn 4: Vận hành Pipeline & Truy vấn kết quả (ClickHouse)

Tài liệu này hướng dẫn cách thực thi toàn bộ pipeline tự động và cách kết nối truy vấn dữ liệu từ các bảng ClickHouse kết quả.

---

## 1. Hướng dẫn chạy thử nghiệm Pipeline End-to-End

Pipeline kết hợp tuần tự các bước: tải dữ liệu, ingest vào landing Iceberg, và chạy dbt build biến đổi dữ liệu trực tiếp trong ClickHouse.

### Câu lệnh chạy:
Chạy lệnh sau tại thư mục gốc của dự án (cần đảm bảo Docker container đang hoạt động):

```bash
PYTHONPATH=. uv run --env-file .env python pipelines/flows/ingest_github_archive.py
```

*Lưu ý*: Lệnh sử dụng `--env-file .env` để `uv` tự động nạp các biến cấu hình từ file `.env` vào shell trước khi chạy, giúp code Python hoàn toàn độc lập và sạch sẽ.

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

