# Giai đoạn 2: Quy trình Nạp dữ liệu (Ingestion Pipeline)

Tài liệu này hướng dẫn cách thức hoạt động của pipeline nạp dữ liệu (Ingestion) từ GitHub Archive vào tầng lưu trữ thô (Landing Layer) của Lakehouse.

---

## 1. Cơ chế hoạt động của Ingestion

Pipeline được viết dưới dạng các **Prefect Tasks** và **Flows**:
1. **Download Task**: Tải file nén `.json.gz` chứa danh sách các sự kiện GitHub của một giờ cụ thể (ví dụ: `https://data.gharchive.org/2026-07-18-10.json.gz`).
2. **Landing Ingestion Task**:
   * Đọc file nén line-by-line và phẳng hóa các trường cơ bản (như `id`, `type`, `actor_id`, `repo_name`...).
   * Serialized nested JSON payload thành dạng chuỗi (String) để tránh lỗi cấu trúc schema khi nạp dữ liệu thô.
   * Sử dụng thư viện **PyArrow** để chuyển đổi danh sách dữ liệu sang định dạng columnar table.
   * Dùng **PyIceberg** kết nối tới REST Catalog và thực hiện thao tác **overwrite** phân vùng dữ liệu theo giờ (`source_hour`).

> [!TIP]
> **Tính Idempotency (Chạy lại không trùng dữ liệu)**:
> Thay vì append dữ liệu trực tiếp, task sử dụng API `table.overwrite()` của PyIceberg kèm bộ lọc phân vùng `source_hour == 'YYYY-MM-DD-H'`. Nhờ đó, nếu chạy lại pipeline cho cùng một giờ, dữ liệu cũ sẽ bị đè hoàn toàn, đảm bảo không có dữ liệu trùng lặp.

---

## 2. Giải thích Cấu trúc Tệp Metadata của Apache Iceberg

Sau khi dữ liệu được nạp thành công, trong thư mục `warehouse/landing_api_github/events_raw/metadata/`, bạn sẽ thấy các tệp tin có định dạng như:
* `00000-425b344e-e701-446b-9356-ab35395dc43c.metadata.json`
* `00001-a9dcb7d5-1ec7-478e-a271-07ed5c0af94f.metadata.json`
* Các file `.avro` (Manifest và Snapshot metadata)

### Quy luật đặt tên tệp của Iceberg:
1. **Số thứ tự tiền tố (Sequential Prefix)**: `00000-`, `00001-`, `00002-...` đại diện cho version metadata hiện tại của bảng. Số này tự động tăng lên 1 đơn vị sau mỗi giao dịch (write transaction) thành công.
2. **Chuỗi UUID**: Phần mã hash phía sau giúp đảm bảo tên file là duy nhất toàn cục, ngăn chặn xung đột ghi đè dữ liệu khi có nhiều writers ghi đồng thời.
3. **Mục đích**: Nhờ cấu trúc này, công cụ đọc (như DuckDB) chỉ cần quét qua danh sách file trong thư mục `metadata/` và chọn file JSON có số thứ tự lớn nhất để làm điểm bắt đầu truy vấn. Điều này giúp hỗ trợ tính năng **Time Travel** (đọc dữ liệu tại một snapshot cụ thể trong quá khứ).
