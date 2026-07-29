"""Publish the Kaggle runner Dataset used by the OCR kernel."""

import shutil
from pathlib import Path
from tempfile import TemporaryDirectory

import kagglehub
from document_ocr.config import load_ocr_config
from document_ocr.settings import KaggleSettings

RUNNER_FILES = ("runtime.py", "pyproject.toml", "uv.lock")


def build_runner(
    destination: Path,
    *,
    runner_source: Path = Path("ocr/runners/glm_ocr"),
    bootstrap_source: Path = Path("ocr/runners/kaggle/glm_ocr/bootstrap.py"),
    package_source: Path = Path("ocr/src/document_ocr"),
) -> None:
    files = [
        ("bootstrap.py", bootstrap_source),
        *((name, runner_source / name) for name in RUNNER_FILES),
    ]
    missing = sorted(str(source) for _, source in files if not source.is_file())
    if not package_source.is_dir():
        missing.append(str(package_source))
    if missing:
        raise FileNotFoundError(f"Missing Kaggle runner source files: {', '.join(missing)}")
    destination.mkdir(parents=True, exist_ok=False)
    for relative_path, source in files:
        target = destination / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    shutil.copytree(
        package_source,
        destination / "document_ocr",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    shutil.copytree(
        runner_source / "runner",
        destination / "runner",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )


def main() -> None:
    settings = KaggleSettings()
    if not settings.configured:
        raise ValueError("Kaggle runner deployment requires KAGGLE_USERNAME and KAGGLE_API_TOKEN")
    assert settings.username is not None
    processor = load_ocr_config("arxiv_glm_ocr")
    handle = f"{settings.username}/{processor.runner.kaggle.runner_dataset_name}"
    with TemporaryDirectory(prefix="document-ocr-kaggle-runner-") as temporary_directory:
        bundle = Path(temporary_directory) / "runner"
        build_runner(bundle)
        kagglehub.dataset_upload(
            handle,
            str(bundle),
            version_notes=(
                f"GLM-OCR adapter {processor.adapter_version}; "
                f"output protocol {processor.output_schema_version}"
            ),
        )
    print(
        f"Published Kaggle runner Dataset: {handle}. "
        "Keep runner_dataset_version pinned to this release before submitting OCR."
    )


if __name__ == "__main__":
    main()
