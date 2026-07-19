import os

from pyiceberg.catalog import load_catalog

def test_connection():
    print("Env URI:", os.environ.get("PYICEBERG_CATALOG__PROD__URI"))
    print("Env TYPE:", os.environ.get("PYICEBERG_CATALOG__PROD__TYPE"))
    print("Connecting to Iceberg REST Catalog...")
    # Khởi tạo catalog 'prod' cấu hình trong .env hoặc environment
    catalog = load_catalog("prod")
    print("Catalog loaded successfully!")
    
    # Liệt kê danh sách namespace hiện tại
    namespaces = catalog.list_namespaces()
    print("Namespaces hiện tại:", namespaces)
    
    # Tạo namespace thử nghiệm nếu chưa tồn tại
    target_ns = "landing_api_github"
    if (target_ns,) not in namespaces:
        print(f"Đang tạo namespace '{target_ns}'...")
        catalog.create_namespace(target_ns)
        print("Đã tạo namespace thành công!")
        print("Namespaces sau khi cập nhật:", catalog.list_namespaces())
    else:
        print(f"Namespace '{target_ns}' đã tồn tại từ trước.")

if __name__ == "__main__":
    test_connection()
