import os
import requests

def download_github_archive(year: int, month: int, day: int, hour: int, target_dir: str) -> str:
    """
    Tải file sự kiện của GitHub Archive cho một giờ cụ thể.
    Đường dẫn lưu trữ mô phỏng cấu trúc partition của GCS.
    """
    file_name = f"{year}-{month:02d}-{day:02d}-{hour}.json.gz"
    url = f"https://data.gharchive.org/{file_name}"
    
    # Định nghĩa thư mục lưu trữ raw files (archive) tương tự cấu hình partition của GCS
    partition_path = os.path.join(
        target_dir, 
        f"year={year}", 
        f"month={month:02d}", 
        f"day={day:02d}", 
        f"hour={hour:02d}"
    )
    os.makedirs(partition_path, exist_ok=True)
    local_file_path = os.path.join(partition_path, file_name)
    
    # Bỏ qua nếu đã tải xuống từ trước (idempotent)
    if os.path.exists(local_file_path):
        print(f"File {file_name} đã tồn tại tại {local_file_path}. Bỏ qua bước download.")
        return local_file_path

    print(f"Đang tải {url} về {local_file_path}...")
    response = requests.get(url, stream=True, timeout=30)
    
    # Nếu giờ đó chưa có dữ liệu (có thể vì quá tương lai hoặc API chậm)
    if response.status_code == 404:
        raise ValueError(f"Dữ liệu cho thời điểm {year}-{month:02d}-{day:02d} H{hour} không tồn tại trên GH Archive (404).")
    
    response.raise_for_status()
    
    with open(local_file_path, 'wb') as f:
        for chunk in response.iter_content(chunk_size=8192):
            f.write(chunk)
            
    print(f"Đã tải thành công file {file_name}!")
    return local_file_path
