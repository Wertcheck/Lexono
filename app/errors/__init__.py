"""Fehler-/Retry-System (Prompt 31).

Macht fehlgeschlagene Pipeline-Schritte (OCR, Intake, ...) sichtbar,
nachvollziehbar und gezielt wiederholbar - ohne externen Task-Queue-
Dienst, konsistent mit der Ein-Prozess-Architektur des Projekts.
"""

from app.errors.service import RetryService, mask_path_like
from app.models import VALID_ERROR_CATEGORIES, VALID_PROCESSING_ERROR_STATUSES, ProcessingError

__all__ = [
    "ProcessingError",
    "RetryService",
    "VALID_ERROR_CATEGORIES",
    "VALID_PROCESSING_ERROR_STATUSES",
    "mask_path_like",
]
