# Giai đoạn 3: Biến đổi dữ liệu bằng dbt & ClickHouse

Tài liệu này giải thích quy trình biến đổi dữ liệu (Transformation) bằng dbt và ClickHouse, và cách ClickHouse đọc dữ liệu trực tiếp từ Landing layer.

---

## 1. Vai trò của ClickHouse Server trong Lakehouse

Thay vì sử dụng DuckDB làm local compute file, dự án sử dụng **ClickHouse Server** làm công cụ tính toán và lưu trữ chính cho các tầng biến đổi (Curated và Analytics Layers).
* **Hiệu suất vượt trội**: ClickHouse là một hệ quản trị cơ sở dữ liệu cột (Column-oriented DBMS) chuyên cho phân tích (OLAP), hoạt động độc lập và cực kỳ nhanh.
* **Không cần đồng bộ (No Copy-Sync)**: dbt biến đổi và lưu trực tiếp dữ liệu kết quả thành các bảng vật lý ClickHouse (`MergeTree` engine) thuộc các database `default_curated_engineering` và `default_analytics_engineering`. Không còn cần task đồng bộ trung gian hay lo lắng về lỗi đổi tên (rename table) của Iceberg!

---

## 2. Cách dbt-clickhouse đọc dữ liệu từ Iceberg Landing Layer

Vì ClickHouse chạy trong Docker container độc lập, làm thế nào để nó truy vấn dữ liệu từ tệp Parquet của Iceberg ở Landing Layer?

> [!NOTE]
> **ClickHouse File Table Function với Explicit Schema**
> 1. Thư mục `warehouse` chứa các file Parquet của Iceberg trên Host được mount trực tiếp vào thư mục an toàn `/var/lib/clickhouse/user_files/warehouse` của ClickHouse container.
> 2. Model staging `stg_github_events.sql` sử dụng hàm `file()` của ClickHouse để đọc đệ quy các tệp Parquet và khai báo rõ ràng kiểu dữ liệu của schema:
>    ```sql
>    WITH source_data AS (
>        SELECT * FROM file(
>            'warehouse/landing_api_github/events_raw/data/**/*.parquet', 
>            'Parquet', 
>            'id Nullable(String), type Nullable(String), actor_id Nullable(Int64), ...'
>        )
>    )
>    ```
> 3. Cách tiếp cận này giúp dbt xây dựng các view staging cực kỳ nhanh, ổn định và tự động đồng bộ mỗi khi Landing layer được append thêm tệp mới.

---

## 3. Cấu trúc các Layer biến đổi (dbt Models)

Quy trình xử lý chia làm 3 tầng:

1. **Staging layer (`models/staging/`)**:
   * `stg_github_events.sql`: Đọc từ file Parquet thô của Landing layer, ép kiểu thời gian bằng hàm `parseDateTime64BestEffort()` của ClickHouse.
2. **Curated layer (`models/curated/`)**:
   * `github_events.sql`: Lọc trùng lặp sự kiện bằng `ROW_NUMBER() OVER (PARTITION BY id ORDER BY created_at DESC)`. Trích xuất các thuộc tính hành động trong payload JSON sử dụng hàm tối ưu của ClickHouse `JSONExtractString` và `JSONExtractInt`.
   * `repositories.sql` & `actors.sql`: Bảng chiều quản lý thông tin duy nhất của repo và user.
3. **Analytics layer (`models/analytics/`)**:
   * `repository_activity_daily.sql`: Bảng tổng hợp hoạt động hàng ngày của repository (số lượt commit, pull request, contributor hoạt động...).
   * `contributor_activity_daily.sql`: Tổng hợp số lượng đóng góp hàng ngày của contributor.
   * Cả hai bảng đều được vật chất hóa thành các bảng ClickHouse MergeTree tối ưu hóa cho BI app.
