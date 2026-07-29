"""Launch the locked OCR runner from its attached Kaggle Dataset."""

import json
import os
import subprocess
import sys
from pathlib import Path

UV_VERSION = "0.11.30"


def main(
    *,
    job_json: str,
    source: Path,
    model_path: Path,
    layout_model_path: Path,
) -> None:
    working = Path("/kaggle/working")
    job_path = working / "job.json"
    job_path.write_text(job_json, encoding="utf-8")
    environment = os.environ.copy()
    environment["UV_PROJECT_ENVIRONMENT"] = "/tmp/document-ocr-venv"
    environment["UV_CACHE_DIR"] = "/tmp/document-ocr-uv-cache"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            f"uv=={UV_VERSION}",
        ],
        check=True,
        env=environment,
    )
    print(json.dumps({"event": "dependency_sync_started"}), flush=True)
    subprocess.run(
        [
            "uv",
            "sync",
            "--project",
            str(source),
            "--frozen",
            "--no-dev",
            "--no-build",
        ],
        check=True,
        env=environment,
    )
    print(json.dumps({"event": "dependency_sync_completed"}), flush=True)
    subprocess.run(
        [
            "uv",
            "run",
            "--project",
            str(source),
            "--frozen",
            "--no-sync",
            "python",
            str(source / "runtime.py"),
            "--job",
            str(job_path),
            "--output-directory",
            str(working),
            "--model-path",
            str(model_path),
            "--layout-model-path",
            str(layout_model_path),
        ],
        check=True,
        env=environment,
    )
