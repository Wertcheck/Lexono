"""Lokale Diagnose einer Qualitaetsmeldung (ARCHITECTURE.md §75).

Grenzt ein, in welcher Verarbeitungsstufe ein gemeldetes Problem entsteht - mit DENSELBEN Bausteinen wie der
Produktivpfad: extrahierter Dokumenttext (OCR/Extraktion), Regex-/Aktendaten-Erkennung und Presidio-NER
(app/privacy/detectors.py), der lokale Teil des Datenschutz-Gateways (`ClaudePrivacyGateway.prepare_request` ruft
selbst KEINE Cloud auf) und die Fristenerkennung (app/deadlines/extractor.py).

Datenschutz der Diagnose selbst:
- Kein Netzwerkzugriff, keine Cloud, kein Logging von Inhalten.
- Das Ergebnis (`findings`) enthaelt AUSSCHLIESSLICH feste Schluessel mit Ja/Nein-Werten, Zaehlern, Kategorie- und
  Stufennamen. Weder Fundstelle noch Dokumenttext, Platzhalter oder Gruende des Gateways (koennen Begriffe enthalten)
  werden uebernommen - per Test abgesichert.
- Die Diagnose aendert nichts: keine Regel, keine Entscheidung, keine Frist.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.models.quality_report import (
    CATEGORY_ALTERED_LEGAL_CONTENT,
    CATEGORY_MISSED_DEADLINE,
    CATEGORY_MISSED_PERSONAL_DATA,
    CATEGORY_OVER_PSEUDONYMIZATION,
    CATEGORY_PRIVACY_CONCERN,
)

#: Kontext um die Fundstelle, der analysiert wird (Laufzeit; Erkennung braucht Umgebung, aber nicht das ganze Dokument).
WINDOW_CHARS = 1500


@dataclass
class DiagnosisResult:
    suspected_stage: str
    reproduced: bool | None
    findings: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Location:
    match: str  # "exact" | "normalized" | "missing" | "no_text"
    start: int | None = None
    end: int | None = None


def _compact(text: str) -> tuple[str, list[int]]:
    """Nur Buchstaben/Ziffern, kleingeschrieben, mit Positionszuordnung - fuer Abweichungen bei Leerraum,
    Gross-/Kleinschreibung, Silbentrennung und Satzzeichen (typische OCR-/Extraktionseffekte)."""
    chars: list[str] = []
    positions: list[int] = []
    for index, char in enumerate(text):
        if char.isalnum():
            chars.append(char.lower())
            positions.append(index)
    return "".join(chars), positions


def locate(excerpt: str, text: str | None) -> Location:
    if not text or not text.strip():
        return Location("no_text")
    index = text.find(excerpt)
    if index >= 0:
        return Location("exact", index, index + len(excerpt))
    compact_text, positions = _compact(text)
    compact_excerpt, _ = _compact(excerpt)
    if compact_excerpt:
        hit = compact_text.find(compact_excerpt)
        if hit >= 0:
            return Location("normalized", positions[hit], positions[hit + len(compact_excerpt) - 1] + 1)
    return Location("missing")


def _contains(haystack: str, needle: str) -> bool:
    if needle.lower() in haystack.lower():
        return True
    compact_needle, _ = _compact(needle)
    return bool(compact_needle) and compact_needle in _compact(haystack)[0]


def _payload_text(payload) -> str:
    if payload is None:
        return ""
    data = payload.model_dump() if hasattr(payload, "model_dump") else dict(payload.__dict__)
    parts: list[str] = []
    for value in data.values():
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, list):
            parts.extend(str(item) for item in value)
    return "\n".join(parts)


def _overlapping_categories(spans, start: int, end: int) -> list[str]:
    return sorted({span.category for span in spans if span.start < end and span.end > start})


class QualityDiagnostician:
    """Fuehrt die Diagnose aus. Abhaengigkeiten sind injizierbar (Tests ohne Presidio-Ladezeit); der Standard ist
    der produktive lokale Pfad."""

    def __init__(
        self,
        *,
        gateway_factory: Callable[[], Any] | None = None,
        ner_detector: Callable[[str], list] | None = None,
        deadline_extractor: Any | None = None,
    ) -> None:
        self._gateway_factory = gateway_factory
        self._ner_detector = ner_detector
        self._deadline_extractor = deadline_extractor

    def _gateway(self):
        if self._gateway_factory is not None:
            return self._gateway_factory()
        from app.privacy.gateway import ClaudePrivacyGateway

        return ClaudePrivacyGateway()

    def _ner(self):
        if self._ner_detector is not None:
            return self._ner_detector
        from app.privacy.presidio_ner import detect_presidio_entities

        return detect_presidio_entities

    def _extractor(self):
        if self._deadline_extractor is not None:
            return self._deadline_extractor
        from app.deadlines.extractor import PlaceholderDeadlineExtractor

        return PlaceholderDeadlineExtractor()

    # --- Einstieg ---------------------------------------------------------------------------------------------------

    def diagnose(
        self,
        *,
        category: str,
        excerpt: str | None,
        document_text: str | None,
        known_entities: dict[str, list[str]],
        document_deadlines: list[Any] | None = None,
        ocr_used: bool | None = None,
    ) -> DiagnosisResult:
        excerpt = (excerpt or "").strip()
        if not excerpt:
            return DiagnosisResult("unknown", None, {"excerpt_given": False, "manual_review_required": True})
        location = locate(excerpt, document_text)
        findings: dict[str, Any] = {
            "excerpt_given": True,
            "document_text_available": location.match != "no_text",
            "excerpt_in_text": location.match,
            "ocr_used": ocr_used,
        }
        if location.match in ("no_text", "missing"):
            # Ohne Fundstelle im extrahierten Text ist keine spaetere Stufe pruefbar: Ursache vor der Erkennung.
            stage = "ocr" if ocr_used else "extraction"
            findings["manual_review_required"] = True
            return DiagnosisResult(stage, None, findings)
        if location.match == "normalized":
            findings["text_differs_in_spacing_case_or_hyphenation"] = True

        assert document_text is not None and location.start is not None and location.end is not None
        window_start = max(0, location.start - WINDOW_CHARS)
        window = document_text[window_start : location.end + WINDOW_CHARS]
        start, end = location.start - window_start, location.end - window_start
        found_value = window[start:end]

        handlers = {
            CATEGORY_MISSED_PERSONAL_DATA: self._missed_personal_data,
            CATEGORY_OVER_PSEUDONYMIZATION: self._over_pseudonymization,
            CATEGORY_ALTERED_LEGAL_CONTENT: self._altered_legal_content,
            CATEGORY_MISSED_DEADLINE: self._missed_deadline,
        }
        handler = handlers.get(category)
        if handler is None:  # CATEGORY_PRIVACY_CONCERN und Unbekanntes: nur Lokalisierung, Rest manuell
            findings["manual_review_required"] = True
            return DiagnosisResult("unknown", None, findings)
        try:
            return handler(window, start, end, found_value, known_entities, findings, document_deadlines or [])
        except Exception as exc:  # noqa: BLE001 - Diagnose darf nie abbrechen; nur der Fehlertyp wird festgehalten
            findings["diagnosis_error"] = type(exc).__name__
            findings["manual_review_required"] = True
            return DiagnosisResult("unknown", None, findings)

    # --- Erkennung --------------------------------------------------------------------------------------------------

    def _detection(self, window: str, start: int, end: int, known_entities, findings: dict) -> tuple[list, list]:
        from app.privacy.detectors import detect_all

        rule_spans = detect_all(window, known_entities)
        ner_spans = self._ner()(window)
        findings["rule_or_case_data_categories"] = _overlapping_categories(rule_spans, start, end)
        findings["known_entity_hit"] = any(
            span.start < end and span.end > start and span.category in known_entities for span in rule_spans
        )
        findings["ner_categories"] = _overlapping_categories(ner_spans, start, end)
        return rule_spans, ner_spans

    def _gateway_run(self, window: str, known_entities, findings: dict):
        result = self._gateway().prepare_request(
            purpose="formulate_draft", sachverhalt=window, known_entities=known_entities, review_mode="mask"
        )
        findings["gateway_allowed"] = bool(result.allowed)
        findings["gateway_block_reason_count"] = len(result.reasons or [])
        findings["gateway_review_candidates"] = len(result.review_candidates or [])
        return result

    def _missed_personal_data(self, window, start, end, value, known_entities, findings, _deadlines):
        self._detection(window, start, end, known_entities, findings)
        result = self._gateway_run(window, known_entities, findings)
        detected = bool(findings["rule_or_case_data_categories"] or findings["ner_categories"])
        leaked = bool(result.allowed) and _contains(_payload_text(result.payload), value)
        findings["value_left_local_path"] = leaked
        if not detected:
            # Erkennungsluecke - auch wenn die Pruefung vor der Cloud fail-closed blockiert (dann kein Leck, aber
            # Blockade statt Pseudonymisierung).
            return DiagnosisResult("presidio", leaked, findings)
        if leaked:
            return DiagnosisResult("pseudonymization", True, findings)
        # Erkannt und im Standardpfad geschuetzt: das gemeldete Problem entstand auf einem anderen Weg.
        return DiagnosisResult("routing", False, findings)

    def _over_pseudonymization(self, window, start, end, value, known_entities, findings, _deadlines):
        self._detection(window, start, end, known_entities, findings)
        result = self._gateway_run(window, known_entities, findings)
        auto_masked = [term for term in (result.auto_masked_terms or []) if _contains(term, value) or _contains(value, term)]
        findings["masked_by_pre_cloud_check"] = bool(auto_masked)
        kept = result.allowed and _contains(_payload_text(result.payload), value)
        findings["value_kept_in_payload"] = bool(kept)
        if kept:
            return DiagnosisResult("routing", False, findings)
        if findings["known_entity_hit"]:
            stage = "routing"  # Begriff steht in den Akten-/Beteiligtendaten
        elif findings["rule_or_case_data_categories"]:
            stage = "pseudonymization"
        elif findings["ner_categories"]:
            stage = "presidio"
        elif auto_masked or not result.allowed:
            stage = "validation"
        else:
            stage = "unknown"
        return DiagnosisResult(stage, True, findings)

    def _altered_legal_content(self, window, start, end, value, known_entities, findings, _deadlines):
        self._detection(window, start, end, known_entities, findings)
        result = self._gateway_run(window, known_entities, findings)
        if not result.allowed:
            return DiagnosisResult("validation", None, findings)
        pseudonymized = result.payload.anonymisierter_sachverhalt
        findings["preserved_in_payload"] = _contains(pseudonymized, value)
        restored = self._gateway().reconstruct_response(pseudonymized, result.mappings)
        findings["preserved_after_reconstruction"] = _contains(restored, value)
        if findings["preserved_in_payload"] and findings["preserved_after_reconstruction"]:
            # Lokale Vor- und Nachverarbeitung erhalten den Inhalt - Veraenderung vermutlich in der Modellantwort.
            findings["local_pipeline_preserves_content"] = True
            return DiagnosisResult("unknown", False, findings)
        if findings["rule_or_case_data_categories"] or findings["ner_categories"]:
            stage = "presidio" if findings["ner_categories"] and not findings["rule_or_case_data_categories"] else "pseudonymization"
        else:
            stage = "pseudonymization"
        return DiagnosisResult(stage, True, findings)

    def _missed_deadline(self, window, start, end, value, _known_entities, findings, deadlines):
        offset = max(0, start - 300)
        sentence_window = window[offset : end + 300]
        local_start, local_end = start - offset, end - offset

        def overlaps_report(candidate) -> bool:
            # Nur Funde, deren Datums-/Dauerangabe die gemeldete Stelle beruehrt - nicht ein Datum aus einem
            # Nachbarsatz, dessen Kontext die Stelle zufaellig mit umfasst.
            raw = candidate.raw_date_text or ""
            position = sentence_window.find(raw) if raw else -1
            while position >= 0:
                if position < local_end and position + len(raw) > local_start:
                    return True
                position = sentence_window.find(raw, position + 1)
            return False

        candidates = [c for c in self._extractor().extract(sentence_window) if overlaps_report(c)]
        findings["extractor_candidates_near_excerpt"] = len(candidates)
        if not candidates:
            return DiagnosisResult("deadline_rules", True, findings)
        candidate_dates = {c.due_date for c in candidates if c.due_date is not None}
        stored = list(deadlines)
        findings["stored_suggestions_for_document"] = len(stored)
        matching = [d for d in stored if d.due_date in candidate_dates]
        findings["matching_stored_suggestion_statuses"] = sorted({d.review_status for d in matching})
        if matching:
            return DiagnosisResult("validation" if all(d.review_status == "rejected" for d in matching) else "unknown",
                                   False, findings)
        # Regeln finden die Frist, gespeichert ist sie nicht: Analyse lief nicht (keine Akte/kein Text) oder auf altem Text.
        findings["reanalysis_recommended"] = True
        return DiagnosisResult("routing", True, findings)


#: Feste, inhaltsfreie Schluessel - alles andere wird verworfen (Schutz vor versehentlich uebernommenem Text).
ALLOWED_FINDING_KEYS = frozenset({
    "excerpt_given", "document_text_available", "excerpt_in_text", "ocr_used", "manual_review_required",
    "text_differs_in_spacing_case_or_hyphenation", "rule_or_case_data_categories", "known_entity_hit",
    "ner_categories", "gateway_allowed", "gateway_block_reason_count", "gateway_review_candidates",
    "value_left_local_path", "masked_by_pre_cloud_check", "value_kept_in_payload", "preserved_in_payload",
    "preserved_after_reconstruction", "local_pipeline_preserves_content", "extractor_candidates_near_excerpt",
    "stored_suggestions_for_document", "matching_stored_suggestion_statuses", "reanalysis_recommended",
    "diagnosis_error",
})
_SAFE_TOKEN = re.compile(r"^[a-z_]{1,40}$")


def sanitize_findings(findings: dict[str, Any]) -> dict[str, Any]:
    """Nur erlaubte Schluessel; Werte nur bool/int/None oder kurze Bezeichner aus Kleinbuchstaben/Unterstrich."""
    clean: dict[str, Any] = {}
    for key, value in findings.items():
        if key not in ALLOWED_FINDING_KEYS:
            continue
        if value is None or isinstance(value, (bool, int)):
            clean[key] = value
        elif isinstance(value, str) and (_SAFE_TOKEN.match(value) or key == "diagnosis_error" and value.isidentifier()):
            clean[key] = value
        elif isinstance(value, list) and all(isinstance(v, str) and _SAFE_TOKEN.match(v) for v in value):
            clean[key] = value
    return clean


__all__ = ["DiagnosisResult", "QualityDiagnostician", "locate", "sanitize_findings", "CATEGORY_PRIVACY_CONCERN"]
