"""Small, validated contract shared by the local CLI, Modal, and artifact readers."""

from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from document_ocr.identity import canonical_json_sha256, job_id, processing_id

OCR_PROTOCOL_VERSION = "3.0.0"
OCR_RESULT_FILE = "result.json"
OCR_ARCHIVE_FILE = "artifacts.tar.zst"
OCR_RESULT_FILES = (OCR_RESULT_FILE, OCR_ARCHIVE_FILE)
DOCUMENT_MANIFEST_PATH = PurePosixPath("manifest.json")
PAGE_MARKDOWN_BUNDLE_PATH = PurePosixPath("pages.json.gz")
type Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
DocumentState = Literal["succeeded", "reused"]


class ProtocolModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class OcrError(RuntimeError):
    """One user-facing error type for the complete OCR flow."""

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(f"{code}: {message}" if code else message)


class OcrReuseReference(ProtocolModel):
    pdf_sha256: Sha256
    pdf_size_bytes: int = Field(ge=1)
    page_count: int = Field(ge=1)
    processing_id: Sha256
    manifest_sha256: Sha256

    def matches(self, *, pdf_sha256: str, pdf_size_bytes: int, page_count: int) -> bool:
        return (
            self.pdf_sha256 == pdf_sha256
            and self.pdf_size_bytes == pdf_size_bytes
            and self.page_count == page_count
        )


class OcrLimits(ProtocolModel):
    max_pdf_bytes: int = Field(ge=1024 * 1024)
    max_pages_per_document: int = Field(ge=1, le=2000)
    max_output_bytes: int = Field(ge=1024 * 1024)


class OcrJob(ProtocolModel):
    """One deterministic document request sent from the local CLI to Modal."""

    schema_version: Literal["3.0.0"] = OCR_PROTOCOL_VERSION
    job_id: Sha256
    config_hash: Sha256
    limits: OcrLimits
    document_id: str = Field(min_length=1, max_length=255)
    source_record_sha256: Sha256
    pdf_url: str = Field(pattern=r"^https://")
    reuse: OcrReuseReference | None = None

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        expected_job_id = job_id(
            document_id=self.document_id,
            source_record_sha256=self.source_record_sha256,
            configuration_hash=self.config_hash,
        )
        if self.job_id != expected_job_id:
            raise ValueError(f"OCR job identity does not match its inputs for {self.document_id}")
        if self.reuse is not None:
            expected_processing_id = processing_id(
                document_id=self.document_id,
                pdf_sha256=self.reuse.pdf_sha256,
                configuration_hash=self.config_hash,
            )
            if self.reuse.processing_id != expected_processing_id:
                raise ValueError(
                    f"OCR reuse identity does not match job configuration for {self.document_id}"
                )
        return self


class ArtifactFile(ProtocolModel):
    relative_path: str
    sha256: Sha256
    size_bytes: int = Field(ge=0)
    media_type: str = Field(min_length=1)

    @field_validator("relative_path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        path = PurePosixPath(value)
        if path.is_absolute() or not value or value != path.as_posix() or ".." in path.parts:
            raise ValueError("Artifact paths must be normalized relative POSIX paths")
        return value


class OcrElement(ProtocolModel):
    element_id: Sha256
    page_number: int = Field(ge=1)
    reading_order: int = Field(ge=0)
    element_type: str = Field(min_length=1, max_length=100)
    bbox_json: str | None = None
    text_content: str
    markdown_content: str | None = None
    parent_element_id: Sha256 | None = None
    raw_attributes_json: str | None = None


class OcrPageMarkdown(ProtocolModel):
    page_number: int = Field(ge=1)
    markdown: str


class OcrPageMarkdownBundle(ProtocolModel):
    schema_version: Literal["3.0.0"] = OCR_PROTOCOL_VERSION
    pages: tuple[OcrPageMarkdown, ...] = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def consecutive_pages(self) -> Self:
        page_numbers = tuple(page.page_number for page in self.pages)
        expected = tuple(range(1, len(self.pages) + 1))
        if page_numbers != expected:
            raise ValueError("OCR page Markdown numbers must be consecutive and ordered")
        return self

    def markdown(self, page_number: int) -> str:
        if page_number < 1 or page_number > len(self.pages):
            raise ValueError(f"Page {page_number} is outside this document")
        return self.pages[page_number - 1].markdown


def validate_document_artifact_paths(paths: list[str], *, page_count: int) -> None:
    if len(paths) != len(set(paths)):
        raise ValueError("OCR result artifact paths must be unique")
    required = {PAGE_MARKDOWN_BUNDLE_PATH.as_posix(), "elements.jsonl.gz"}
    if not required.issubset(paths):
        raise ValueError("Successful OCR results require pages.json.gz and elements.jsonl.gz")

    expected_visualizations = {
        f"layout_vis/page-{page_number:04d}" for page_number in range(1, page_count + 1)
    }
    actual_visualizations: list[str] = []
    for value in paths:
        path = PurePosixPath(value)
        if value in required:
            continue
        if (
            len(path.parts) == 2
            and path.parts[0] == "layout_vis"
            and path.suffix.lower() in {".jpg", ".jpeg", ".png"}
        ):
            actual_visualizations.append(path.with_suffix("").as_posix())
            continue
        if (
            len(path.parts) == 2
            and path.parts[0] == "imgs"
            and path.suffix.lower() in {".jpg", ".jpeg", ".png"}
        ):
            continue
        raise ValueError(f"Unsupported OCR document artifact path: {value}")
    if (
        len(actual_visualizations) != page_count
        or set(actual_visualizations) != expected_visualizations
    ):
        raise ValueError("Successful OCR results require exactly one visualization per page")


class OcrDocumentManifest(ProtocolModel):
    schema_version: Literal["3.0.0"] = OCR_PROTOCOL_VERSION
    document_id: str = Field(min_length=1, max_length=255)
    files: tuple[ArtifactFile, ...]
    page_count: int = Field(ge=1)
    pdf_sha256: Sha256
    pdf_size_bytes: int = Field(ge=1)
    processing_id: Sha256

    @model_validator(mode="after")
    def validate_artifact_set(self) -> Self:
        validate_document_artifact_paths(
            [file.relative_path for file in self.files],
            page_count=self.page_count,
        )
        return self

    def file(self, relative_path: str) -> ArtifactFile:
        try:
            return next(file for file in self.files if file.relative_path == relative_path)
        except StopIteration as error:
            raise FileNotFoundError(f"OCR artifact is not declared: {relative_path}") from error


class OcrOutput(ProtocolModel):
    schema_version: Literal["3.0.0"] = OCR_PROTOCOL_VERSION
    job_id: Sha256
    created_at: datetime
    archive_sha256: Sha256
    archive_size_bytes: int = Field(ge=1)
    document_id: str = Field(min_length=1, max_length=255)
    state: DocumentState
    pdf_sha256: Sha256
    pdf_size_bytes: int = Field(ge=1)
    page_count: int = Field(ge=1)
    processing_id: Sha256
    manifest_sha256: Sha256
    manifest: OcrDocumentManifest | None = None

    @field_validator("created_at")
    @classmethod
    def require_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("Run timestamps must include a timezone")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_document(self) -> Self:
        if self.state != "succeeded":
            if self.manifest is not None:
                raise ValueError("Only a newly succeeded run can publish a document manifest")
            return self
        if self.manifest is None:
            raise ValueError("A succeeded run requires a document manifest")
        if (
            self.manifest.document_id != self.document_id
            or self.manifest.page_count != self.page_count
            or self.manifest.pdf_sha256 != self.pdf_sha256
            or self.manifest.pdf_size_bytes != self.pdf_size_bytes
            or self.manifest.processing_id != self.processing_id
        ):
            raise ValueError("OCR document manifest lineage does not match its result")
        if canonical_json_sha256(self.manifest.model_dump(mode="json")) != self.manifest_sha256:
            raise ValueError("OCR document manifest checksum does not match its result")
        return self
