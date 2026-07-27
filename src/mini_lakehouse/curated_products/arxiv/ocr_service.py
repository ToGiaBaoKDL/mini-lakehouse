"""Reconcile one remote OCR run and submit the next bounded ArXiv batch."""

from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path
from tempfile import TemporaryDirectory

from mini_lakehouse.config.settings import Settings
from mini_lakehouse.contracts import PlatformContracts, load_contracts
from mini_lakehouse.curated_products.arxiv.models import (
    ActiveOcrBatch,
    OcrBatchDocument,
    OcrCandidate,
    OcrCycleResult,
    OcrRunState,
)
from mini_lakehouse.curated_products.arxiv.ocr_publisher import ArxivOcrPublisher
from mini_lakehouse.curated_products.arxiv.ocr_repository import ArxivOcrRepository
from mini_lakehouse.curated_products.table_manager import CuratedTableManager
from mini_lakehouse.platform.trino import SqlExecutor, TrinoExecutor
from mini_lakehouse.processing.ocr.archive import (
    InvalidOcrOutputError,
    OcrBatchMismatchError,
    validate_runner_output,
)
from mini_lakehouse.processing.ocr.core.files import file_sha256
from mini_lakehouse.processing.ocr.core.identity import (
    batch_id,
    config_hash,
    request_id,
)
from mini_lakehouse.processing.ocr.core.protocol import (
    OCR_OUTPUT_SCHEMA_VERSION,
    OcrDocumentRequest,
    OcrInference,
    OcrJob,
    OcrLimits,
    OcrModel,
)
from mini_lakehouse.processing.ocr.kaggle_bundle import KaggleRunnerBundle
from mini_lakehouse.processing.ocr.kaggle_provider import KaggleProvider
from mini_lakehouse.processing.ocr.kaggle_types import (
    KaggleClient,
    KaggleCommandError,
    KaggleKernelNotFoundError,
    KaggleKernelState,
    KaggleRunStatus,
)
from mini_lakehouse.storage.object_store import ObjectStore, create_object_store


class ArxivOcrService:
    def __init__(
        self,
        settings: Settings,
        *,
        executor: SqlExecutor | None = None,
        contracts: PlatformContracts | None = None,
        table_manager: CuratedTableManager | None = None,
        repository: ArxivOcrRepository | None = None,
        provider: KaggleClient | None = None,
        object_store: ObjectStore | None = None,
    ) -> None:
        self._settings = settings
        self._contracts = contracts or load_contracts(settings.contracts_dir)
        self._processor = self._contracts.processor("arxiv_glm_ocr")
        if self._processor.source != "arxiv" or self._processor.curated_product != "arxiv":
            raise ValueError("ArXiv OCR processor ownership is inconsistent")
        if self._processor.output_schema_version != OCR_OUTPUT_SCHEMA_VERSION:
            raise ValueError(
                f"This runtime implements OCR output schema {OCR_OUTPUT_SCHEMA_VERSION}"
            )
        self._runner_bundle = KaggleRunnerBundle.load()
        if provider is not None and provider.runner_bundle_sha256 != self._runner_bundle.sha256:
            raise ValueError("Injected Kaggle provider uses a different runner bundle")
        self._configuration_hash = config_hash(self._processor)
        self._executor = executor
        self._table_manager = table_manager or CuratedTableManager(
            settings,
            self._processor.curated_product,
            self._contracts,
        )
        self._repository = repository or ArxivOcrRepository(settings, self._contracts)
        self._provider = provider
        self._publisher = ArxivOcrPublisher(
            settings,
            self._processor,
            self._repository,
            object_store or create_object_store(settings.storage),
        )

    def run_once(
        self,
        *,
        arxiv_ids: tuple[str, ...] = (),
        verify_pdf: bool = False,
    ) -> OcrCycleResult:
        identifiers = tuple(dict.fromkeys(arxiv_ids))
        limit = self._processor.batch.max_documents
        if len(identifiers) > limit:
            raise ValueError(f"An explicit OCR run accepts at most {limit} unique arxiv_ids")
        if verify_pdf and not identifiers:
            raise ValueError("PDF verification requires at least one explicit ArXiv ID")
        owned_executor = TrinoExecutor(self._settings.trino) if self._executor is None else None
        context = owned_executor if owned_executor is not None else nullcontext(self._executor)
        with context as executor:
            if executor is None:
                raise RuntimeError("A SQL executor is required")
            self._table_manager.ensure_tables(executor)
            active = self._repository.active_batch(executor)
            provider = self._provider_client() if active is not None else None
            reconciliation: OcrCycleResult | None = None
            if active is not None:
                assert provider is not None
                reconciliation = self._reconcile(executor, provider, active)
                if reconciliation.action in {"waiting", "submitted"}:
                    return reconciliation

            submitted = self._submit_next(
                executor,
                provider,
                limit=limit,
                arxiv_ids=identifiers,
                verify_pdf=verify_pdf,
            )
            if submitted is not None:
                if reconciliation is None:
                    return submitted
                return submitted.model_copy(
                    update={
                        "action": "reconciled_and_submitted",
                        "reconciled_batch_id": reconciliation.batch_id,
                        "imported_documents": reconciliation.imported_documents,
                        "retryable_failures": reconciliation.retryable_failures,
                        "terminal_failures": reconciliation.terminal_failures,
                    }
                )
            return reconciliation or OcrCycleResult(action="idle")

    def _provider_client(self) -> KaggleClient:
        return self._provider or KaggleProvider(
            self._settings.kaggle,
            self._processor,
            bundle=self._runner_bundle,
        )

    def _job(
        self,
        batch_key: str,
        documents: tuple[OcrBatchDocument, ...],
    ) -> OcrJob:
        processor = self._processor
        request_ids = [document.request.request_id for document in documents]
        attempts = {document.request.request_id: document.attempt_count for document in documents}
        if batch_key != batch_id(request_ids, attempts=attempts):
            raise RuntimeError(f"OCR batch identity drifted for batch {batch_key}")
        return OcrJob(
            schema_version="1.0.0",
            batch_id=batch_key,
            runner_bundle_sha256=self._runner_bundle.sha256,
            model=OcrModel.model_validate(processor.model.model_dump()),
            layout_model=OcrModel.model_validate(processor.layout_model.model_dump()),
            adapter_version=processor.adapter_version,
            output_schema_version=OCR_OUTPUT_SCHEMA_VERSION,
            config_hash=self._configuration_hash,
            limits=OcrLimits(
                max_pdf_bytes=processor.batch.max_pdf_bytes,
                max_pages_per_document=processor.batch.max_pages_per_document,
                max_output_bytes=processor.batch.max_output_bytes,
            ),
            inference=OcrInference.model_validate(processor.inference.model_dump()),
            documents=tuple(document.request for document in documents),
        )

    def _documents(
        self,
        candidates: tuple[OcrCandidate, ...],
    ) -> tuple[OcrBatchDocument, ...]:
        return tuple(
            OcrBatchDocument(
                request=OcrDocumentRequest(
                    request_id=request_id(
                        arxiv_id=candidate.arxiv_id,
                        source_record_sha256=candidate.source_record_sha256,
                        configuration_hash=self._configuration_hash,
                    ),
                    arxiv_id=candidate.arxiv_id,
                    oai_datestamp=candidate.oai_datestamp,
                    source_record_sha256=candidate.source_record_sha256,
                    pdf_url=candidate.pdf_url,
                    reuse=candidate.reuse,
                ),
                attempt_count=candidate.attempt_count + 1,
            )
            for candidate in candidates
        )

    def _submit_next(
        self,
        executor: SqlExecutor,
        provider: KaggleClient | None,
        *,
        limit: int,
        arxiv_ids: tuple[str, ...],
        verify_pdf: bool,
    ) -> OcrCycleResult | None:
        candidates = self._repository.candidates(
            executor,
            self._processor,
            configuration_hash=self._configuration_hash,
            limit=limit,
            arxiv_ids=arxiv_ids,
            verify_pdf=verify_pdf,
        )
        if not candidates:
            return None
        provider = provider or self._provider_client()
        quota = provider.gpu_quota()
        if quota.remaining_minutes < self._processor.runner.minimum_gpu_quota_minutes:
            return OcrCycleResult(
                action="deferred_quota",
                remaining_gpu_quota_minutes=quota.remaining_minutes,
                quota_refresh_at=quota.refresh_at,
            )
        documents = self._documents(candidates)
        request_keys = [document.request.request_id for document in documents]
        attempts = {document.request.request_id: document.attempt_count for document in documents}
        batch_key = batch_id(
            request_keys,
            attempts=attempts,
        )
        job = self._job(batch_key, documents)
        self._repository.prepare_batch(
            executor,
            job=job,
            documents=documents,
            kernel_slug=provider.kernel_slug,
        )
        self._submit_prepared(executor, provider, job)
        return OcrCycleResult(action="submitted", batch_id=batch_key)

    def _submit_prepared(
        self,
        executor: SqlExecutor,
        provider: KaggleClient,
        job: OcrJob,
    ) -> None:
        with TemporaryDirectory(prefix="arxiv-ocr-submit-") as temporary_directory:
            submission = Path(temporary_directory) / "submission"
            provider.prepare_submission(submission, job)
            provider_run_id = provider.submit(submission)
        self._repository.mark_batch_submitted(
            executor,
            batch_id=job.batch_id,
            provider_run_id=provider_run_id,
        )

    def _reconcile(
        self,
        executor: SqlExecutor,
        provider: KaggleClient,
        active: ActiveOcrBatch,
    ) -> OcrCycleResult:
        job = active.job
        provider_run_id = active.provider_run_id
        if active.state == "prepared" and provider_run_id is None:
            run = provider.latest_run(active.batch_id)
            if run is None or run.state == KaggleKernelState.FAILED:
                if job.config_hash != self._configuration_hash:
                    return self._failed_cycle(
                        executor,
                        active,
                        error_code="stale_prepared_configuration",
                        error_message=(
                            "The prepared batch uses an older processor configuration and "
                            "has no recoverable remote run; a new batch may be submitted"
                        ),
                    )
                self._submit_prepared(executor, provider, job)
                return OcrCycleResult(action="submitted", batch_id=active.batch_id)
            provider_run_id = run.provider_run_id
            self._repository.mark_batch_submitted(
                executor,
                batch_id=active.batch_id,
                provider_run_id=provider_run_id,
            )
            if run.state == KaggleKernelState.COMPLETE:
                try:
                    return self._import_completed(
                        executor,
                        provider,
                        active,
                        job,
                        provider_run_id,
                    )
                except OcrBatchMismatchError:
                    self._submit_prepared(executor, provider, job)
                    return OcrCycleResult(action="submitted", batch_id=active.batch_id)
                except InvalidOcrOutputError as error:
                    return self._failed_cycle(
                        executor,
                        active,
                        error_code="invalid_kaggle_output",
                        error_message=str(error),
                    )
        else:
            if provider_run_id is None:
                raise RuntimeError(f"Submitted OCR batch {active.batch_id} has no provider run ID")
            try:
                run = provider.status(provider_run_id)
            except KaggleKernelNotFoundError:
                return self._failed_cycle(
                    executor,
                    active,
                    error_code="kaggle_run_missing",
                    error_message="The submitted Kaggle kernel is no longer available",
                )

        if run.state.active:
            if run.state == KaggleKernelState.RUNNING:
                self._repository.mark_batch_running(executor, active.batch_id)
            return OcrCycleResult(action="waiting", batch_id=active.batch_id)
        if run.state == KaggleKernelState.FAILED:
            return self._failed_cycle(
                executor,
                active,
                error_code="kaggle_run_failed",
                error_message=self._failure_diagnostic(provider, run),
            )
        try:
            return self._import_completed(
                executor,
                provider,
                active,
                job,
                provider_run_id,
            )
        except OcrBatchMismatchError:
            return self._failed_cycle(
                executor,
                active,
                error_code="kaggle_output_mismatch",
                error_message="The exact Kaggle run output does not belong to this batch",
            )
        except InvalidOcrOutputError as error:
            return self._failed_cycle(
                executor,
                active,
                error_code="invalid_kaggle_output",
                error_message=str(error),
            )

    @staticmethod
    def _failure_diagnostic(
        provider: KaggleClient,
        run: KaggleRunStatus,
    ) -> str:
        message = run.failure_message or "Kaggle reported a failed terminal kernel state"
        try:
            log_tail = provider.logs(run.provider_run_id).strip()[-1500:]
        except KaggleCommandError:
            log_tail = ""
        if log_tail:
            message = f"{message}\n\nKaggle log tail:\n{log_tail}"
        return message[:2000]

    def _failed_cycle(
        self,
        executor: SqlExecutor,
        active: ActiveOcrBatch,
        *,
        error_code: str,
        error_message: str,
    ) -> OcrCycleResult:
        retryable, terminal = self._fail_remote_batch(
            executor,
            active,
            error_code=error_code,
            error_message=error_message,
        )
        return OcrCycleResult(
            action="reconciled",
            batch_id=active.batch_id,
            retryable_failures=retryable,
            terminal_failures=terminal,
        )

    def _fail_remote_batch(
        self,
        executor: SqlExecutor,
        active: ActiveOcrBatch,
        *,
        error_code: str,
        error_message: str,
    ) -> tuple[int, int]:
        diagnostic = error_message[:2000]
        retryable = 0
        terminal = 0
        for document in active.documents:
            if document.state == OcrRunState.IMPORTED:
                continue
            exhausted = (
                document.state == OcrRunState.TERMINAL_FAILED
                or document.attempt_count >= self._processor.retry.max_document_attempts
            )
            if exhausted:
                terminal += 1
            else:
                retryable += 1
            self._repository.mark_document_failure(
                executor,
                batch_id=active.batch_id,
                request_id=document.request.request_id,
                state=(OcrRunState.TERMINAL_FAILED if exhausted else OcrRunState.RETRYABLE_FAILED),
                error_code=error_code,
                error_message=diagnostic,
            )
        self._repository.mark_batch_terminal(
            executor,
            batch_id=active.batch_id,
            state="failed",
            error_code=error_code,
            error_message=diagnostic,
        )
        return retryable, terminal

    def _import_completed(
        self,
        executor: SqlExecutor,
        provider: KaggleClient,
        active: ActiveOcrBatch,
        job: OcrJob,
        provider_run_id: str,
    ) -> OcrCycleResult:
        with TemporaryDirectory(prefix="arxiv-ocr-output-") as temporary_directory:
            temporary = Path(temporary_directory)
            download = temporary / "download"
            extracted = temporary / "extracted"
            provider.download_output(provider_run_id, download)
            manifest = validate_runner_output(download, extracted, job=job)
            manifest_sha256 = file_sha256(download / "result_manifest.json")
            attempts = {
                document.request.request_id: document.attempt_count for document in active.documents
            }
            imported = 0
            retryable = 0
            terminal = 0
            for result in manifest.documents:
                if result.state in {"succeeded", "reused"}:
                    self._publisher.publish(executor, job, result, extracted, temporary)
                    imported += 1
                    continue
                exhausted = attempts[result.request_id] >= (
                    self._processor.retry.max_document_attempts
                )
                terminal_result = result.state == "terminal_failed" or exhausted
                if terminal_result:
                    terminal += 1
                else:
                    retryable += 1
                self._repository.mark_document_failure(
                    executor,
                    batch_id=active.batch_id,
                    request_id=result.request_id,
                    state=(
                        OcrRunState.TERMINAL_FAILED
                        if terminal_result
                        else OcrRunState.RETRYABLE_FAILED
                    ),
                    error_code=result.error_code or "unknown_runner_error",
                    error_message=result.error_message or "Runner returned no error detail",
                )
            self._repository.mark_batch_terminal(
                executor,
                batch_id=active.batch_id,
                state="completed",
                manifest_sha256=manifest_sha256,
            )
        return OcrCycleResult(
            action="reconciled",
            batch_id=active.batch_id,
            imported_documents=imported,
            retryable_failures=retryable,
            terminal_failures=terminal,
        )
