import subprocess
from prefect import task

@task
def run_dbt_build(project_dir: str, profiles_dir: str) -> str:
    """
    Thực thi dbt build để chạy và kiểm thử các model staging, curated và analytics.
    """
    print(f"Bắt đầu chạy dbt build (project: {project_dir}, profiles: {profiles_dir})...")
    
    cmd = [
        "uv", "run", "dbt", "build",
        "--project-dir", project_dir,
        "--profiles-dir", profiles_dir
    ]
    
    # Chạy dbt subprocess
    result = subprocess.run(cmd, capture_output=True, text=True)
    
    print("----- DBT STDOUT -----")
    print(result.stdout)
    print("----------------------")
    
    if result.returncode != 0:
        print("----- DBT STDERR -----")
        print(result.stderr)
        print("----------------------")
        raise RuntimeError(f"dbt build thất bại với exit code {result.returncode}")
        
    print("dbt build đã chạy thành công!")
    return "dbt build completed"
