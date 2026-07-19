import os
import requests
from src.utils.logging import get_logger

logger = get_logger("lakehouse.ingestion.download")

def download_github_archive(year: int, month: int, day: int, hour: int, target_dir: str) -> str:
    """
    Tải file sự kiện của GitHub Archive cho một giờ cụ thể.
    Đường dẫn lưu trữ mô phỏng cấu trúc GCS.
    """
    file_name = f"{year}-{month:02d}-{day:02d}-{hour}.json.gz"
    url = f"https://data.gharchive.org/{file_name}"
    
    partition_path = os.path.join(
        target_dir, 
        f"year={year}", 
        f"month={month:02d}", 
        f"day={day:02d}", 
        f"hour={hour:02d}"
    )
    os.makedirs(partition_path, exist_ok=True)
    local_file_path = os.path.join(partition_path, file_name)
    
    # Bỏ qua nếu đã tải xuống (idempotency)
    if os.path.exists(local_file_path):
        logger.info("File %s đã tồn tại tại %s. Bỏ qua bước download.", file_name, local_file_path)
        return local_file_path

    logger.info("Đang tải %s về %s...", url, local_file_path)
    response = requests.get(url, stream=True, timeout=30)
    
    if response.status_code == 404:
        raise ValueError(f"Dữ liệu cho thời điểm {year}-{month:02d}-{day:02d} H{hour} không tồn tại trên GH Archive (404).")
    
    response.raise_for_status()
    
    with open(local_file_path, 'wb') as f:
        for chunk in response.iter_content(chunk_size=8192):
            f.write(chunk)
            
    logger.info("Đã tải thành công file %s!", file_name)
    return local_file_path
