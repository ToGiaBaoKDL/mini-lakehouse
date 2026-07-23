"""Publish validated OCR artifacts and canonical elements with a durable commit marker."""

from pathlib import Path

from mini_lakehouse.config.settings import Settings
from mini_lakehouse.contracts.processors import ProcessorContract
from mini_lakehouse.curated_products.arxiv.ocr_repository import ArxivOcrRepository
from mini_lakehouse.platform.trino import SqlExecutor
from mini_lakehouse.processing.ocr.archive import (
    artifact_paths,
    document_manifest_payload,
    file_sha256,
    load_elements,
)
from mini_lakehouse.processing.ocr.identity import canonical_json_bytes
from mini_lakehouse.processing.ocr.paths import artifact_root_uri, runner_document_path
from mini_lakehouse.processing.ocr.protocol import OcrDocumentResult, OcrJob
from mini_lakehouse.storage.object_store import ObjectStore


class ArxivOcrPublisher:
    """Own the curated publication boundary for one successful OCR document."""

    def __init__(
        self,
        settings: Settings,
        processor: ProcessorContract,
        repository: ArxivOcrRepository,
        object_store: ObjectStore,
    ) -> None:
        self._settings = settings
        self._processor = processor
        self._repository = repository
        self._object_store = object_store

    def publish(
        self,
        executor: SqlExecutor,
        job: OcrJob,
        result: OcrDocumentResult,
        extraction_directory: Path,
        temporary_directory: Path,
    ) -> None:
        processing_key = result.processing_id
        manifest_hash = result.manifest_sha256
        if processing_key is None or manifest_hash is None:
            raise RuntimeError("Successful OCR result lacks a processing identity")
        artifact_uri = artifact_root_uri(
            self._settings,
            self._processor,
            arxiv_id=result.arxiv_id,
            processing_id=processing_key,
        )
        existing = self._repository.processing_manifest(executor, processing_key)
        if existing is not None:
            if existing != (manifest_hash, artifact_uri):
                raise RuntimeError(
                    f"Processing identity collision for {processing_key}: {existing!r}"
                )
            self._repository.mark_document_imported(
                executor,
                batch_id=job.batch_id,
                result=result,
                artifact_uri=artifact_uri,
            )
            return

        document_directory = extraction_directory.joinpath(
            *runner_document_path(result.arxiv_id, result.request_id).parts
        )
        elements = load_elements(
            document_directory / "elements.jsonl.gz",
            max_uncompressed_bytes=job.limits.max_output_bytes,
        )
        for source, relative_path in artifact_paths(extraction_directory, result):
            self._publish_immutable(
                source,
                f"{artifact_uri}/{relative_path}",
            )

        manifest_file = temporary_directory / f"{result.request_id}-manifest.json"
        manifest_file.write_bytes(canonical_json_bytes(document_manifest_payload(result)))
        if file_sha256(manifest_file) != manifest_hash:
            raise RuntimeError("Canonical OCR document manifest hash drifted during publication")
        self._publish_immutable(
            manifest_file,
            f"{artifact_uri}/manifest.json",
        )

        # The manifest is the artifact commit marker. Iceberg state follows it and
        # remains retry-safe because elements are replaced by processing identity.
        self._repository.replace_elements(
            executor,
            processing_id=processing_key,
            arxiv_id=result.arxiv_id,
            elements=elements,
        )
        self._repository.mark_document_imported(
            executor,
            batch_id=job.batch_id,
            result=result,
            artifact_uri=artifact_uri,
        )

    def _publish_immutable(
        self,
        source: Path,
        destination_uri: str,
    ) -> None:
        if self._object_store.upload_if_absent(source, destination_uri):
            return
        expected_hash = file_sha256(source)
        if self._object_store.sha256(destination_uri) != expected_hash:
            raise RuntimeError(f"Immutable OCR artifact collision at {destination_uri}")
