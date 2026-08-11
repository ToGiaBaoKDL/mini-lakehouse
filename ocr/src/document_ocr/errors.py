"""Stable, machine-readable failures shared by document processors."""


class DocumentProcessingError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
