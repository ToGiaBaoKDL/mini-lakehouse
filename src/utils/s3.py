import s3fs
from src.utils.config import settings

def get_s3_fs() -> s3fs.S3FileSystem:
    """
    Khởi tạo và trả về đối tượng s3fs.S3FileSystem kết nối với SeaweedFS.
    """
    return s3fs.S3FileSystem(
        client_kwargs={'endpoint_url': settings.s3_endpoint},
        key=settings.s3_access_key,
        secret=settings.s3_secret_key,
        config_kwargs={'signature_version': 's3v4'}
    )
