# Giai đoạn 1: Thiết lập Môi trường & Hạ tầng Cục bộ (Local ClickHouse & Iceberg)

Tài liệu này hướng dẫn cách chuẩn bị môi trường chạy cục bộ sử dụng Python `uv` và Docker để chạy Iceberg REST Catalog cùng với ClickHouse Server lưu trực tiếp trên ổ đĩa máy tính (Local Filesystem).

---

## 1. Cài đặt Python Dependencies, Ruff & Pyright bằng `uv`

Dự án sử dụng file `pyproject.toml` để quản lý các gói thư viện. Chạy lệnh sau tại thư mục gốc để tự động tạo môi trường ảo `.venv` và cài đặt toàn bộ dependencies bao gồm cả công cụ clean code (`ruff`, `pyright`), adapter (`dbt-clickhouse`, `clickhouse-connect`), và framework (`prefect`, `pyiceberg`, `streamlit`):

```bash
uv sync
```

### Chạy các công cụ kiểm tra Code:
* **Kiểm tra cú pháp & Format (Ruff)**:
  ```bash
  uv run ruff check .
  ```
* **Kiểm tra kiểu tĩnh (Pyright)**:
  ```bash
  ./.venv/bin/pyright .
  ```

---

## 2. Khởi tạo Hạ tầng REST Catalog & ClickHouse Server qua Docker Compose

Chúng ta sử dụng Docker Compose để quản lý:
1. **Iceberg REST Catalog**: Dùng SQLite database backend lưu metadata. Các tệp tin dữ liệu thực tế (Parquet) được ghi vào Host thông qua volume mount.
2. **ClickHouse Server**: Dùng để biến đổi dbt models và phân tích OLAP tốc độ cao. Thư mục `warehouse` được mount trực tiếp vào `user_files/warehouse` để ClickHouse đọc dữ liệu Parquet của Iceberg cục bộ.

### Các lệnh khởi chạy:

```bash
# Di chuyển vào thư mục chứa docker-compose.yml
cd catalog

# Tạo trước thư mục lưu dữ liệu để Docker không tự khởi tạo với quyền root
mkdir -p catalog-data clickhouse-data

# Thay đổi quyền sở hữu sang tài khoản local (UID 1000:1000) để container có quyền ghi
chown -R 1000:1000 clickhouse-data catalog-data

# Khởi chạy các container ở chế độ daemon
docker compose up -d
```

### Kiểm tra trạng thái Catalog & ClickHouse:
* REST Catalog sẽ lắng nghe ở cổng `http://localhost:8181`.
* ClickHouse Server sẽ lắng nghe ở cổng HTTP `http://localhost:8123` và cổng TCP `9000`.
* Kiểm tra danh sách container đang chạy:
  ```bash
  docker ps
  ```

---

## 3. Cấu hình biến môi trường `.env`

Tạo tệp `.env` tại thư mục gốc của dự án với nội dung cấu hình sau:

```ini
# Cấu hình PyIceberg kết nối tới Catalog 'prod' cục bộ
PYICEBERG_CATALOG__PROD__TYPE=rest
PYICEBERG_CATALOG__PROD__URI=http://localhost:8181
PYICEBERG_CATALOG__PROD__WAREHOUSE=/home/hcm-mki-l6009/projects/mini-lakehouse/warehouse

# Cấu hình kết nối ClickHouse
CLICKHOUSE_HOST=localhost
CLICKHOUSE_PORT=8123
CLICKHOUSE_DB=github_lakehouse
CLICKHOUSE_USER=dev_user
CLICKHOUSE_PASSWORD=dev_password

# Cấu hình dbt
DBT_PROFILES_DIR=./dbt_project
LAKEHOUSE_STAGE=local
```
