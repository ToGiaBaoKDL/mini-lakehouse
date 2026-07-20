import os
import requests
import tempfile
from src.utils.logging import get_logger
from src.utils.s3 import get_s3_fs
from src.utils.config import settings

logger = get_logger("lakehouse.ingestion.download")

def download_github_archive(year: int, month: int, day: int, hour: int, target_dir: str = "") -> str:
    """
    Tải file sự kiện của GitHub Archive cho một giờ cụ thể và lưu nguyên bản vào SeaweedFS S3.
    file_name = f"{year}-{month:02d}-{day:02d}-{hour}.json.gz"
    url = f"https://data.gharchive.org/{file_name}"
    
    # Đường dẫn đích trên S3 (raw files được đưa vào raw-files prefix của landing bucket)
    s3_path = f"s3://{settings.s3_landing_bucket}/raw-files/api/github/year={year}/month={month:02d}/day={day:02d}/hour={hour:02d}/{file_name}"
    
    fs = get_s3_fs()
    
    # Kiểm tra xem file đã tồn tại trên S3 chưa
    if fs.exists(s3_path):
        logger.info("File %s đã tồn tại trên S3 tại %s. Bỏ qua bước download.", file_name, s3_path)
        return s3_path

    logger.info("Đang tải %s từ GH Archive...", url)
    response = requests.get(url, stream=True, timeout=30)

    if response.status_code == 404:
        raise ValueError(f"Dữ liệu cho thời điểm {year}-{month:02d}-{day:02d} H{hour} không tồn tại trên GH Archive (404).")

    response.raise_for_status()

    # Ghi tạm ra file cục bộ rồi tải lên S3
    with tempfile.NamedTemporaryFile(delete=False) as tmp_file:
        local_tmp_path = tmp_file.name
        try:
            for chunk in response.iter_content(chunk_size=8192):
                tmp_file.write(chunk)
            tmp_file.close() # Phải đóng file trước khi upload để tránh lock file trên Windows/Linux
            
            logger.info("Đang tải tệp lên SeaweedFS S3: %s...", s3_path)
            fs.put_file(local_tmp_path, s3_path)
            logger.info("Đã tải thành công file %s lên S3!", file_name)
        finally:
            if os.path.exists(local_tmp_path):
                os.remove(local_tmp_path)
                
    return s3_path
