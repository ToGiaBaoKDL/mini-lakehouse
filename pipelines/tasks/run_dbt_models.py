import subprocess
from prefect import task, get_run_logger

@task
def run_dbt_build(project_dir: str, profiles_dir: str) -> str:
    logger = get_run_logger()
    logger.info("Bắt đầu chạy dbt build (project: %s, profiles: %s)...", project_dir, profiles_dir)

    cmd = [
        "uv", "run", "dbt", "build",
        "--project-dir", project_dir,
        "--profiles-dir", profiles_dir
    ]

    # Chạy dbt subprocess
    result = subprocess.run(cmd, capture_output=True, text=True)

    # Ghi nhận log output từ dbt stdout
    if result.stdout:
        logger.info("----- DBT STDOUT -----\n%s", result.stdout)

    if result.returncode != 0:
        if result.stderr:
            logger.error("----- DBT STDERR -----\n%s", result.stderr)
        raise RuntimeError(f"dbt build thất bại với exit code {result.returncode}")

    logger.info("dbt build đã chạy thành công!")
    return "dbt build completed"
