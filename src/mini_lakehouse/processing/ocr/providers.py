from typing import assert_never

from mini_lakehouse.config.settings import Settings
from mini_lakehouse.contracts.processors import ProcessorContract
from mini_lakehouse.processing.ocr.provider import OcrProvider, OcrProviderName


def create_ocr_provider(
    settings: Settings,
    processor: ProcessorContract,
    name: OcrProviderName,
) -> OcrProvider:
    match name:
        case "kaggle":
            from mini_lakehouse.processing.ocr.kaggle_provider import KaggleProvider

            return KaggleProvider(settings.kaggle, processor)
        case "modal":
            from mini_lakehouse.processing.ocr.modal_provider import ModalProvider

            return ModalProvider(settings.modal, processor)
    assert_never(name)
