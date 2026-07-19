# Mini Data Lakehouse Project ⚡

Dự án này xây dựng một hệ thống **Data Lakehouse** cục bộ (Local-first) chuẩn doanh nghiệp phục vụ mục đích học tập và nghiên cứu. Hệ thống được thiết kế để dễ dàng chuyển đổi cấu hình lưu trữ từ hệ thống tệp cục bộ (Local Filesystem) lên **Google Cloud Storage (GCS)** mà không cần thay đổi mã nguồn.

---

## 🛠️ Công nghệ sử dụng
* **Prefect** (Orchestration): Điều phối toàn bộ dữ liệu từ API tải về đến lúc nạp đè và biến đổi.
* **Apache Iceberg + REST Catalog**: Sử dụng định dạng bảng Iceberg để lưu trữ lớp Landing thô, đảm bảo tính nhất quán (ACID transaction) và hỗ trợ Time Travel.
* **dbt (data build tool) & ClickHouse**: Thực hiện các truy vấn biến đổi dữ liệu siêu tốc và lưu trữ dữ liệu lớp Curated và Analytics trực tiếp trong **ClickHouse Server** (dùng `MergeTree` engine).
* **Ruff & Pyright**: Đảm bảo chất lượng mã nguồn Python sạch sẽ, nhất quán và an toàn về kiểu.
* **Streamlit**: Giao diện BI phân tích dữ liệu trực quan kết hợp tính năng kiểm tra Iceberg Metadata thông qua PyIceberg.

---

## 📂 Hướng dẫn Dự án Chi tiết (Phases)

Dự án được tài liệu hóa thành các phần ngắn gọn, súc tích trong thư mục `docs/`. Vui lòng đọc theo thứ tự để cấu hình và chạy:

1. **[00_overview.md](file:///home/hcm-mki-l6009/projects/mini-lakehouse/docs/00_overview.md)**: Sơ đồ dòng chảy dữ liệu (Architecture) và cấu trúc thư mục Lakehouse sau khi chạy thành công.
2. **[01_setup.md](file:///home/hcm-mki-l6009/projects/mini-lakehouse/docs/01_environment_setup.md)**: Hướng dẫn chuẩn bị môi trường ảo bằng `uv`, cấu hình `.env`, các lệnh kiểm tra ruff/pyright và Docker Compose chạy Iceberg Catalog & ClickHouse.
3. **[02_ingestion.md](file:///home/hcm-mki-l6009/projects/mini-lakehouse/docs/02_data_ingestion.md)**: Giải thích pipeline nạp dữ liệu thô (Landing events) và quy luật đặt tên file metadata của Iceberg.
4. **[03_transformation.md](file:///home/hcm-mki-l6009/projects/mini-lakehouse/docs/03_dbt_transformation.md)**: Cơ chế hoạt động của ClickHouse, dbt models, và cách ClickHouse đọc dữ liệu Parquet từ Landing layer thông qua file function.
5. **[04_execution.md](file:///home/hcm-mki-l6009/projects/mini-lakehouse/docs/04_pipeline_execution.md)**: Hướng dẫn vận hành chạy thử toàn bộ pipeline tự động và cách viết script Python sử dụng `clickhouse-connect` để truy vấn dữ liệu ClickHouse kết quả.
6. **[05_troubleshooting_lessons.md](file:///home/hcm-mki-l6009/projects/mini-lakehouse/docs/05_troubleshooting_lessons.md)**: Nhật ký ghi lại các lỗi kỹ thuật phát sinh (phân quyền docker, s3 empty host, schema view của clickhouse) và bài học kinh nghiệm rút ra.

---

## 🚀 Khởi động nhanh (Quick Start)

Dành cho những người đã thiết lập xong môi trường và file `.env`:

```bash
# 1. Khởi chạy Iceberg REST Catalog và ClickHouse Server
cd catalog
docker compose up -d
cd ..

# 2. Chạy Ingestion Pipeline theo giờ (Hourly Ingest)
PYTHONPATH=. uv run --env-file .env python pipelines/flows/ingest_github_archive.py

# 3. Chạy dbt Transformation Pipeline hàng ngày (Daily Transform & Test)
PYTHONPATH=. uv run --env-file .env python pipelines/flows/transform_github_archive_daily.py

# 4. Khởi chạy giao diện Streamlit BI
PYTHONPATH=. uv run --env-file .env streamlit run bi_app/app.py
```
Giao diện Streamlit BI sẽ khả dụng tại địa chỉ: `http://localhost:8501`.
