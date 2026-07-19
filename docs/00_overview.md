# Hướng dẫn Dự án Mini Data Lakehouse (Tổng quan)

Dự án này xây dựng một **Data Lakehouse** cục bộ mô phỏng môi trường production thực tế, sử dụng dữ liệu sự kiện từ GitHub Archive làm nguồn cấp đầu vào.

---

## 1. Kiến trúc luồng dữ liệu (Data Pipeline Flow)

```text
GitHub Archive API (Compressed JSON)
       │
       ▼ [Ingestion Layer - Prefect, PyArrow & PyIceberg]
Raw Files Archive (.json.gz) & Landing Table (Apache Iceberg REST Catalog)
       │
       ▼ [Transformation Layer - dbt & ClickHouse]
dbt staging (Views via ClickHouse file function) -> dbt curated & analytics (ClickHouse MergeTree)
       │
       ▼ [Presentation Layer]
BI & Reporting Dashboard (Streamlit & clickhouse-connect / PyIceberg metadata query)
```

---

## 2. Danh mục tài liệu hướng dẫn chi tiết (Phases)

Vui lòng theo dõi tài liệu dự án theo thứ tự các bước dưới đây để thiết lập và chạy thành công Lakehouse:

1. **[01_setup.md](file:///home/hcm-mki-l6009/projects/mini-lakehouse/docs/01_environment_setup.md)**: Hướng dẫn cài đặt Python dependencies bằng `uv`, các công cụ clean code (`ruff`, `pyright`), cấu hình tệp `.env`, khởi tạo Docker container cho Iceberg REST Catalog và ClickHouse Server.
2. **[02_ingestion.md](file:///home/hcm-mki-l6009/projects/mini-lakehouse/docs/02_data_ingestion.md)**: Hướng dẫn cơ chế hoạt động của pipeline nạp dữ liệu từ GH Archive API bằng Prefect, PyArrow và PyIceberg, đồng thời giải thích cấu trúc tệp metadata độc đáo của Iceberg.
3. **[03_transformation.md](file:///home/hcm-mki-l6009/projects/mini-lakehouse/docs/03_dbt_transformation.md)**: Giải thích vai trò của ClickHouse Server làm công cụ tính toán phân tích (OLAP), cấu hình `dbt-clickhouse` profiles, và cách ClickHouse đọc dữ liệu trực tiếp từ Landing layer qua file function.
4. **[04_execution.md](file:///home/hcm-mki-l6009/projects/mini-lakehouse/docs/04_pipeline_execution.md)**: Hướng dẫn vận hành chạy thử toàn bộ pipeline tự động và cách viết script Python sử dụng `clickhouse-connect` để truy vấn các bảng phân tích phục vụ báo cáo.
5. **[05_troubleshooting_lessons.md](file:///home/hcm-mki-l6009/projects/mini-lakehouse/docs/05_troubleshooting_lessons.md)**: Nhật ký ghi lại các lỗi kỹ thuật phát sinh và bài học kinh nghiệm rút ra trong quá trình thiết lập và build dự án.

---

## 3. Các thành phần chính của Lakehouse sau khi chạy thành công

Sau khi pipeline hoàn tất, cấu trúc thư mục dữ liệu tại `warehouse/` sẽ như sau:
* `raw-files/`: Thư mục lưu trữ tệp tin gốc `.json.gz` để audit hoặc backfill.
* `landing_api_github/events_raw/`: Bảng Iceberg Landing thô chứa tất cả các events được ghi bởi PyIceberg.
* ClickHouse Server lưu dữ liệu phân tích cục bộ tại thư mục host mount `catalog/clickhouse-data/`.
