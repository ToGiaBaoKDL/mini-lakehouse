# Giai đoạn 5: Nhật ký lỗi & Bài học Kinh nghiệm (Troubleshooting & Lessons Learned)

Tài liệu này ghi lại các lỗi kỹ thuật phát sinh trong quá trình xây dựng hệ thống Local Data Lakehouse và các giải pháp đã được áp dụng để vượt qua.

---

## 1. Lỗi đổi tên bảng Iceberg trong dbt (Rename Transaction Limit)
* **Thông báo lỗi**: `Catalog Error: This table (actors__dbt_tmp) was modified already, can't be renamed!`
* **Nguyên nhân**: `dbt-duckdb` mặc định tạo bảng tạm dạng `__dbt_tmp` rồi đổi tên (`ALTER TABLE ... RENAME TO`). Tuy nhiên, trong Apache Iceberg, giao dịch ghi bảng tạm đã được commit vào REST Catalog, dẫn tới việc khóa metadata và không cho phép đổi tên bảng trong cùng một phiên làm việc.
* **Giải pháp**:
  - Cấu hình dbt ghi các bảng Staging, Curated và Analytics trực tiếp vào cơ sở dữ liệu DuckDB local (`github_lakehouse.duckdb`).
  - Viết task Prefect đồng bộ riêng (`sync_local_tables_to_iceberg`) để sao chép dữ liệu từ DuckDB local sang Iceberg REST Catalog sau khi dbt chạy xong.

---

## 2. Lỗi `CREATE OR REPLACE TABLE` trên Iceberg
* **Thông báo lỗi**: `NotImplementedException: Not implemented Error: CREATE OR REPLACE not supported in DuckDB-Iceberg. Please use separate Drop and Create Statements`
* **Nguyên nhân**: Tiện ích mở rộng Iceberg của DuckDB chưa hỗ trợ từ khóa `CREATE OR REPLACE` cho bảng Iceberg.
* **Giải pháp**: Tách biệt luồng đồng bộ thành 2 câu lệnh chạy tuần tự:
  1. `DROP TABLE IF EXISTS prod.schema.table;`
  2. `CREATE TABLE prod.schema.table AS SELECT * FROM ...;`

---

## 3. Lỗi DuckDB không tự tạo thư mục ghi metadata và dữ liệu
* **Thông báo lỗi**: `Cannot open file ".../metadata/...-m0.avro": No such file or directory`
* **Nguyên nhân**: DuckDB C++ writer khi ghi bảng Iceberg lên REST Catalog cục bộ không tự động tạo đệ quy (recursive) thư mục nested `metadata/` và `data/` trên đĩa cứng Host.
* **Giải pháp**: Sử dụng thư viện `os` của Python trong task đồng bộ để tự động tạo trước cấu trúc thư mục trước khi chạy câu lệnh DuckDB:
  ```python
  table_dir = os.path.join(base_warehouse, schema, table)
  os.makedirs(os.path.join(table_dir, "metadata"), exist_ok=True)
  os.makedirs(os.path.join(table_dir, "data"), exist_ok=True)
  ```

---

## 4. Lỗi khóa cơ sở dữ liệu SQLite của REST Catalog (`SQLITE_BUSY`)
* **Thông báo lỗi**: `Caused by: org.sqlite.SQLiteException: [SQLITE_BUSY] The database file is locked (database is locked)`
* **Nguyên nhân**: Mặc định, dbt chạy song song đa luồng (threads). Khi nhiều luồng cùng gọi REST Catalog để ghi dữ liệu, Catalog cố gắng ghi đồng thời vào SQLite backend làm xuất hiện xung đột khóa ghi (write lock).
* **Giải pháp**: Cấu hình thêm chế độ ghi nhật ký trước **WAL (Write-Ahead Logging)** và thời gian chờ bận **busy_timeout** vào JDBC connection string trong `docker-compose.yml`:
  ```yaml
  CATALOG_URI: jdbc:sqlite:/home/iceberg/data/iceberg_catalog.db?journal_mode=WAL&busy_timeout=10000
  ```

---

## 5. Lỗi phân quyền ghi tệp của Docker REST Catalog container lên Host
* **Thông báo lỗi**: `Cannot open file "...avro": No such file or directory` (Hoặc Permission Denied).
* **Nguyên nhân**: Container REST Catalog mặc định chạy dưới một UID người dùng ảo bên trong Docker, không có quyền ghi dữ liệu vào các thư mục mới do Host tạo ra (quyền mặc định `755`).
* **Giải pháp**: Khai báo rõ ràng thuộc tính `user: "1000:1000"` (UID/GID khớp với tài khoản người dùng Host) cho container catalog trong `docker-compose.yml`.

---

## 6. Lỗi ClickHouse không hỗ trợ local path cho Iceberg table function
* **Thông báo lỗi**: `DB::Exception: Host is empty in S3 URI. (BAD_ARGUMENTS)`
* **Nguyên nhân**: Hàm `iceberg()` của ClickHouse thực chất được cài đặt như một bí danh của `icebergS3()`, bắt buộc đường dẫn truyền vào phải sử dụng scheme `s3://` hoặc `http://` và không hỗ trợ đường dẫn cục bộ `file:///`.
* **Giải pháp**: Đọc trực tiếp các tệp Parquet bên trong thư mục data của bảng bằng hàm `file()` của ClickHouse:
  ```sql
  SELECT * FROM file('warehouse/landing_api_github/events_raw/data/**/*.parquet', 'Parquet')
  ```

---

## 7. Lỗi ClickHouse truy cập thư mục ngoài bị chặn (Database Access Denied)
* **Thông báo lỗi**: `DB::Exception: File ... is not inside /var/lib/clickhouse/user_files. (DATABASE_ACCESS_DENIED)`
* **Nguyên nhân**: Để bảo mật, ClickHouse mặc định chỉ cho phép hàm `file()` đọc các tệp nằm bên trong thư mục `user_files`.
* **Giải pháp**: Thay đổi cấu hình mount volume của ClickHouse trong `docker-compose.yml`, mount thư mục `warehouse` của Host vào bên trong thư mục `user_files` của container:
  ```yaml
  - /home/hcm-mki-l6009/projects/mini-lakehouse/warehouse:/var/lib/clickhouse/user_files/warehouse
  ```

---

## 8. Lỗi thiếu định nghĩa cột khi phân tích view của ClickHouse (Unknown Identifier)
* **Thông báo lỗi**: `DB::Exception: Unknown expression identifier 'id' in scope ...` hoặc `Identifier 'source_data.payload' cannot be resolved`
* **Nguyên nhân**: Khi tạo View từ hàm `file()` động, ClickHouse thỉnh thoảng không lưu vết hoặc định kiểu động chính xác các cột, hoặc trùng lặp tên cột với các hàm hệ thống (như hàm `id()`).
* **Giải pháp**: Khai báo tĩnh rõ ràng cấu trúc kiểu dữ liệu (explicit schema structure) trực tiếp bên trong tham số thứ 3 của hàm `file()`:
  ```sql
  SELECT * FROM file(
      'warehouse/landing_api_github/events_raw/data/**/*.parquet', 
      'Parquet', 
      'id Nullable(String), type Nullable(String), actor_id Nullable(Int64), ...'
  )
  ```
