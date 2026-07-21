# Implementation plans

Thư mục này lưu các kế hoạch thay đổi có phạm vi lớn hoặc ảnh hưởng nhiều thành phần của lakehouse.

## Naming convention

```text
YYYY-MM-DD_<change-type>_<scope>.md
```

Trong đó:

- `YYYY-MM-DD`: ngày tạo plan.
- `change-type`: một trong `feature`, `fix`, `migration`, `refactor`, `research`.
- `scope`: mô tả ngắn bằng `snake_case`, ưu tiên tên capability thay vì tên ticket.

Ví dụ:

```text
2026-07-21_refactor_declarative_contracts_and_source_scalability.md
```

## Required sections

Mỗi implementation plan phải có tối thiểu:

- Trạng thái, phạm vi, mục tiêu và non-goals.
- Quyết định kiến trúc và các invariant không được phá vỡ.
- Checklist triển khai theo phase, có exit criteria.
- Test matrix và Definition of Done.
- Kế hoạch migration, rollback và quản lý rủi ro.
- Danh sách tài liệu/code bị ảnh hưởng.

## Status lifecycle

```text
draft -> approved -> in_progress -> completed
                    \-> blocked
                    \-> superseded
```

Khi triển khai, chỉ đánh dấu `[x]` sau khi task đã được verify bằng acceptance criteria tương ứng. Nếu quyết định thay đổi, cập nhật phần decision log trước khi sửa checklist.
