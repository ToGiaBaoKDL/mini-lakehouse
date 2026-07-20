import os
from dotenv import load_dotenv

load_dotenv()

from pyiceberg.catalog import load_catalog
from pyiceberg.exceptions import NamespaceAlreadyExistsError

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
    target_ns = ("landing", "api", "github")
    print(f"Đang đảm bảo namespace '{'.'.join(target_ns)}' tồn tại...")
    # Tạo từng cấp một
    for i in range(1, len(target_ns) + 1):
        sub_ns = target_ns[:i]
        try:
            catalog.create_namespace(sub_ns)
            print(f"Đã tạo namespace: {'.'.join(sub_ns)}")
        except NamespaceAlreadyExistsError:
            pass
    print("Đã xác thực/tạo namespace thành công!")
    print("Namespaces hiện tại ở root level:", catalog.list_namespaces())

if __name__ == "__main__":
    test_connection()
