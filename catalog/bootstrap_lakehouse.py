import sys
import time
import logging
import requests
import boto3
from botocore.client import Config
from botocore.exceptions import EndpointConnectionError

# Cấu hình logging chuyên nghiệp thay thế các lệnh print thô sơ
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("bootstrap")

def wait_for_http(url, timeout=45):
    """Đợi dịch vụ HTTP online bằng cơ chế polling thay vì hardcoded sleep."""
    logger.info(f"Đang chờ dịch vụ HTTP tại {url} sẵn sàng...")
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            # Gửi request kiểm tra kết nối (Polaris token API trả về 405 GET là bình thường, miễn là port phản hồi)
            requests.options(url, timeout=2)
            logger.info(f"Dịch vụ HTTP tại {url} đã trực tuyến.")
            return True
        except requests.exceptions.RequestException:
            time.sleep(1.5)
    logger.error(f"Hết hạn chờ (Timeout) dịch vụ HTTP tại {url}")
    return False

def wait_for_s3(s3_client, timeout=15):
    """Kiểm tra kết nối thực tế tới MinIO API trước khi thực hiện tác vụ."""
    logger.info("Đang kiểm tra kết nối API S3 của MinIO...")
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            s3_client.list_buckets()
            logger.info("Kết nối API S3 thành công.")
            return True
        except (EndpointConnectionError, Exception) as e:
            logger.debug(f"Kết nối S3 thất bại: {e}. Thử lại...")
            time.sleep(1.5)
    logger.error("Hết hạn chờ API S3.")
    return False

def main():
    s3_endpoint = "http://aistor:9000"
    polaris_uri = "http://catalog:8181"
    
    # 1. Đợi các service khởi động hoàn tất
    if not wait_for_http(f"{s3_endpoint}/minio/health/live", timeout=45):
        sys.exit(1)
    if not wait_for_http(f"{polaris_uri}/api/catalog/v1/oauth/tokens", timeout=45):
        sys.exit(1)
        
    # 2. Khởi tạo S3 Client
    s3 = boto3.client(
        's3',
        endpoint_url=s3_endpoint,
        aws_access_key_id='any_key',
        aws_secret_access_key='any_secret',
        config=Config(signature_version='s3v4')
    )
    
    if not wait_for_s3(s3, timeout=15):
        sys.exit(1)
        
    # 3. Tạo các buckets trên MinIO AIStor S3
    buckets = ['landing', 'curated', 'analytics']
    for bucket in buckets:
        try:
            s3.create_bucket(Bucket=bucket)
            logger.info(f"Bucket '{bucket}' được khởi tạo thành công.")
        except s3.exceptions.BucketAlreadyExists:
            logger.info(f"Bucket '{bucket}' đã tồn tại từ trước.")
        except s3.exceptions.BucketAlreadyOwnedByYou:
            logger.info(f"Bucket '{bucket}' đã được sở hữu bởi tài khoản hiện tại.")
        except Exception as e:
            logger.error(f"Lỗi khi khởi tạo bucket '{bucket}': {e}")
            sys.exit(1)
            
    # 4. Lấy OAuth2 Token của Polaris
    logger.info("Đang lấy OAuth2 token từ Polaris...")
    try:
        r = requests.post(
            f"{polaris_uri}/api/catalog/v1/oauth/tokens",
            data={"grant_type": "client_credentials", "scope": "PRINCIPAL_ROLE:ALL"},
            auth=("root", "secretpassword"),
            timeout=10
        )
        r.raise_for_status()
        token = r.json()["access_token"]
        logger.info("Đã lấy token thành công.")
    except Exception as e:
        logger.error(f"Lấy token Polaris thất bại: {e}")
        sys.exit(1)
        
    # 5. Đăng ký Catalog 'prod' trên Polaris
    logger.info("Đang đăng ký Catalog 'prod' trên Polaris Catalog...")
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}"
    }
    payload = {
        "name": "prod",
        "type": "INTERNAL",
        "allowCustomLocations": True,
        "properties": {
            "default-base-location": "s3://landing/warehouse",
            "s3.endpoint": s3_endpoint,
            "s3.path-style-access": "true",
            "s3.access-key-id": "any_key",
            "s3.secret-access-key": "any_secret",
            "s3.region": "us-east-1",
            "external-credentials": "true",
            "external.credentials": "true",
            "external_credentials": "true"
        },
        "storageConfigInfo": {
            "storageType": "S3",
            "stsUnavailable": True,
            "kmsUnavailable": True,
            "allowedLocations": [
                "s3://landing/warehouse",
                "s3://landing",
                "s3://curated",
                "s3://analytics"
            ]
        }
    }
    
    try:
        r2 = requests.post(
            f"{polaris_uri}/api/management/v1/catalogs",
            json=payload,
            headers=headers,
            timeout=10
        )
        if r2.status_code == 201:
            logger.info("Catalog 'prod' được đăng ký thành công.")
        elif r2.status_code == 409 or "already exists" in r2.text.lower():
            logger.info("Catalog 'prod' đã tồn tại sẵn.")
        else:
            logger.error(f"Phản hồi từ Polaris khi tạo Catalog: {r2.status_code} - {r2.text}")
            sys.exit(1)
    except Exception as e:
        logger.error(f"Gặp lỗi khi gọi API tạo Catalog của Polaris: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
