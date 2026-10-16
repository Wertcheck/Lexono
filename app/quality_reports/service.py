"""Qualitaetsmeldungen: Erfassen, Diagnose, Pruefung, Abschluss, Regressionsfall (ARCHITECTURE.md §75).

Regeln:
- Jede Meldung gehoert zu GENAU einer (nicht geloeschten) Akte; ein angegebenes Dokument muss zu dieser Akte gehoeren.
- Audit-Eintraege enthalten nur Kategorie, Status und Stufen - nie Fundstelle oder Beschreibung.
- Abschliessen ("behoben") oder Verwerfen leert die Fundstelle (Datenminimierung); Diagnose und Verknuepfungen bleiben.
- Nichts davon aendert Erkennungsregeln, Datenschutz-Entscheidungen oder Fristen.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.models import AuditEvent, Deadline, Document, Matter, Party
from app.models.quality_report import (
    CASE_APPROVED,
    CASE_DRAFT,
    CASE_REJECTED,
    CLOSED_STATUSES,
    DIAGNOSIS_STAGES,
    MAX_EXCERPT_LENGTH,
    QUALITY_CATEGORIES,
    STATUS_CONFIRMED,
    STATUS_DIAGNOSED,
    STATUS_DISMISSED,
    STATUS_RESOLVED,
    QualityReport,
    RegressionCase,
)
from app.privacy.pseudonymizer import strip_legal_form
from app.quality_reports.diagnosis import QualityDiagnostician, sanitize_findings
from app.quality_reports.regression import (
    RegressionCaseError,
    build_expectation,
    leak_findings,
    validate_synthetic_text,
)

MAX_DESCRIPTION_LENGTH = 2000


class QualityReportError(ValueError):
    pass


def _audit(db: Session, entity_type: str, entity_id: str, event_type: str, actor: str, details: str) -> None:
    db.add(AuditEvent(entity_type=entity_type, entity_id=entity_id, event_type=event_type, actor=actor,
                      details=details))


def matter_known_entities(db: Session, matter: Matter) -> dict[str, list[str]]:
    """Akten-/Beteiligtennamen wie im Produktivpfad (Mandant, Beteiligte, Nachname allein, Firmenname ohne
    Rechtsform) - Grundlage fuer die Diagnose der Pseudonymisierung."""
    known: dict[str, list[str]] = {"mandant": [], "beteiligter": []}
    if matter.client and matter.client.name:
        known["mandant"].append(matter.client.name)
    for party in db.query(Party).filter(Party.matter_id == matter.id).all():
        if party.name:
            known["beteiligter"].append(party.name)
    for category, names in known.items():
        extra = []
        for name in names:
            stripped = strip_legal_form(name)
            parts = name.strip().split()
            if stripped != name.strip() and len(stripped.split()) >= 2:
                extra.append(stripped)
            elif len(parts) >= 2 and len(parts[-1]) >= 3:
                extra.append(parts[-1])
        known[category] = names + [e for e in extra if e not in names]
    return {k: v for k, v in known.items() if v}


class QualityReportService:
    def __init__(self, diagnostician: QualityDiagnostician | None = None) -> None:
        self.diagnostician = diagnostician or QualityDiagnostician()

    # --- Meldung ----------------------------------------------------------------------------------------------------

    def create(self, db: Session, *, matter_id: str, document_id: str | None, category: str, excerpt: str,
               description: str, actor: str) -> QualityReport:
        if category not in QUALITY_CATEGORIES:
            raise QualityReportError("Unbekannte Kategorie.")
        matter = db.get(Matter, matter_id)
        if matter is None or matter.deleted_at is not None:
            raise QualityReportError("Akte nicht gefunden.")
        if document_id:
            document = db.get(Document, document_id)
            if document is None or document.matter_id != matter.id or document.deleted_at is not None:
                raise QualityReportError("Dokument gehört nicht zu dieser Akte.")
        excerpt = (excerpt or "").strip()
        description = (description or "").strip()
        if not excerpt and not description:
            raise QualityReportError("Bitte die Fundstelle oder eine Beschreibung angeben.")
        if len(excerpt) > MAX_EXCERPT_LENGTH:
            raise QualityReportError(f"Die Fundstelle darf höchstens {MAX_EXCERPT_LENGTH} Zeichen lang sein.")
        if len(description) > MAX_DESCRIPTION_LENGTH:
            raise QualityReportError(f"Die Beschreibung darf höchstens {MAX_DESCRIPTION_LENGTH} Zeichen lang sein.")
        report = QualityReport(matter_id=matter.id, document_id=document_id or None, category=category,
                               excerpt=excerpt or None, description=description or None, reported_by=actor)
        db.add(report)
        db.flush()
        _audit(db, "QualityReport", report.id, "quality_report_created", actor, f"Kategorie: {category}")
        db.commit()
        return report

    # --- Diagnose ---------------------------------------------------------------------------------------------------

    def diagnose(self, db: Session, report: QualityReport, *, actor: str) -> QualityReport:
        if report.status in CLOSED_STATUSES:
            raise QualityReportError("Abgeschlossene Meldungen werden nicht mehr diagnostiziert.")
        matter = db.get(Matter, report.matter_id)
        document = db.get(Document, report.document_id) if report.document_id else None
        deadlines = (db.query(Deadline).filter(Deadline.document_id == document.id).all() if document else [])
        result = self.diagnostician.diagnose(
            category=report.category,
            excerpt=report.excerpt,
            document_text=document.extracted_text if document and document.deleted_at is None else None,
            known_entities=matter_known_entities(db, matter),
            document_deadlines=deadlines,
            ocr_used=(document.ocr_status not in (None, "", "not_needed", "skipped")) if document else None,
        )
        findings = sanitize_findings(result.findings)
        findings["reproduced"] = result.reproduced
        report.diagnosis_json = json.dumps(findings, ensure_ascii=False, sort_keys=True)
        report.suspected_stage = result.suspected_stage if result.suspected_stage in DIAGNOSIS_STAGES else "unknown"
        if report.status == "neu":
            report.status = STATUS_DIAGNOSED
        _audit(db, "QualityReport", report.id, "quality_report_diagnosed", actor,
               f"Vermutete Stufe: {report.suspected_stage}; reproduziert: {result.reproduced}")
        db.commit()
        return report

    # --- Pruefung ---------------------------------------------------------------------------------------------------

    def confirm_cause(self, db: Session, report: QualityReport, *, stage: str, note: str, actor: str) -> QualityReport:
        if stage not in DIAGNOSIS_STAGES:
            raise QualityReportError("Unbekannte Verarbeitungsstufe.")
        if report.status in CLOSED_STATUSES:
            raise QualityReportError("Die Meldung ist bereits abgeschlossen.")
        report.confirmed_stage = stage
        report.resolution_note = (note or "").strip()[:2000] or report.resolution_note
        report.status = STATUS_CONFIRMED
        report.reviewed_by = actor
        _audit(db, "QualityReport", report.id, "quality_report_cause_confirmed", actor,
               f"Bestaetigte Stufe: {stage} (Diagnose: {report.suspected_stage})")
        db.commit()
        return report

    def close(self, db: Session, report: QualityReport, *, outcome: str, note: str, fix_reference: str,
              actor: str) -> QualityReport:
        """`behoben` nur mit bestaetigter Ursache und Verweis auf Korrektur/Regressionstest; `verworfen` jederzeit.
        Beides leert die Fundstelle."""
        if outcome not in (STATUS_RESOLVED, STATUS_DISMISSED):
            raise QualityReportError("Unbekanntes Ergebnis.")
        if report.status in CLOSED_STATUSES:
            raise QualityReportError("Die Meldung ist bereits abgeschlossen.")
        fix_reference = (fix_reference or "").strip()[:200]
        if outcome == STATUS_RESOLVED:
            if report.status != STATUS_CONFIRMED:
                raise QualityReportError("„Behoben“ setzt eine bestätigte Ursache voraus.")
            has_case = db.query(RegressionCase).filter(RegressionCase.report_id == report.id,
                                                       RegressionCase.status == CASE_APPROVED).count()
            if not fix_reference and not has_case:
                raise QualityReportError("Bitte die Korrektur (Commit/Version) angeben oder einen Regressionsfall "
                                         "freigeben.")
        report.status = outcome
        report.fix_reference = fix_reference or report.fix_reference
        report.resolution_note = (note or "").strip()[:2000] or report.resolution_note
        report.reviewed_by = actor
        # Datenminimierung (§75.1): Fundstelle UND freie Beschreibung koennen Mandantendaten enthalten.
        report.excerpt = None
        report.description = None
        _audit(db, "QualityReport", report.id, f"quality_report_{outcome}", actor,
               f"Abgeschlossen: {outcome}; Fundstelle und Beschreibung geleert")
        db.commit()
        return report

    def expire_content(self, db: Session, *, content_days: int, now: datetime | None = None) -> int:
        """Aufbewahrung (§75.1): leert Fundstelle und Beschreibung offener Meldungen, die aelter als `content_days`
        sind, sowie aller Meldungen zu (weich) geloeschten Akten oder Dokumenten (§75.3: die Fundstelle stammt aus dem
        Dokument). Die Meldung selbst, Diagnose, Status, Verknuepfungen und Audit bleiben (Nachvollziehbarkeit ohne
        Inhalt). Gibt die Zahl geleerter Meldungen zurueck."""
        now = now or datetime.now(timezone.utc)
        cutoff = now - timedelta(days=content_days)
        expired = 0
        rows = (
            db.query(QualityReport, Matter.deleted_at, Document.deleted_at)
            .join(Matter, Matter.id == QualityReport.matter_id)
            .outerjoin(Document, Document.id == QualityReport.document_id)
            .filter((QualityReport.excerpt.isnot(None)) | (QualityReport.description.isnot(None)))
            .all()
        )
        for report, matter_deleted_at, document_deleted_at in rows:
            created = report.created_at if report.created_at.tzinfo else report.created_at.replace(tzinfo=timezone.utc)
            if matter_deleted_at is not None:
                reason = "akte_geloescht"
            elif document_deleted_at is not None:
                reason = "dokument_geloescht"
            elif created < cutoff:
                reason = f"frist_{content_days}_tage"
            else:
                continue
            report.excerpt = None
            report.description = None
            _audit(db, "QualityReport", report.id, "quality_report_content_expired", "system",
                   f"Fundstelle und Beschreibung geleert: {reason}")
            expired += 1
        if expired:
            db.commit()
        return expired

    def delete(self, db: Session, report: QualityReport, *, actor: str) -> None:
        _audit(db, "QualityReport", report.id, "quality_report_deleted", actor,
               f"Kategorie: {report.category}; Status: {report.status}")
        for case in db.query(RegressionCase).filter(RegressionCase.report_id == report.id).all():
            case.report_id = None  # synthetische Faelle bleiben, ohne Bezug
        db.delete(report)
        db.commit()

    # --- Regressionsfall --------------------------------------------------------------------------------------------

    def create_case(self, db: Session, report: QualityReport | None, *, category: str, title: str,
                    synthetic_text: str, must_mask: str, must_keep: str, due_dates: str, actor: str) -> RegressionCase:
        if report is not None and report.status != STATUS_CONFIRMED:
            raise RegressionCaseError("Ein Regressionsfall setzt eine bestätigte Ursache voraus.")
        title = (title or "").strip()[:200]
        if not title:
            raise RegressionCaseError("Bitte einen Titel angeben.")
        text = validate_synthetic_text(db, synthetic_text, report_excerpt=report.excerpt if report else None)
        expectation = build_expectation(category, must_mask=must_mask, must_keep=must_keep, due_dates=due_dates,
                                        synthetic_text=text)
        case = RegressionCase(report_id=report.id if report else None, category=category, title=title,
                              synthetic_text=text, expected_json=expectation.to_json(), created_by=actor)
        db.add(case)
        db.flush()
        _audit(db, "RegressionCase", case.id, "regression_case_created", actor, f"Kategorie: {category}")
        db.commit()
        return case

    def decide_case(self, db: Session, case: RegressionCase, *, approve: bool, test_reference: str,
                    actor: str) -> RegressionCase:
        if case.status != CASE_DRAFT:
            raise RegressionCaseError("Nur Entwürfe können freigegeben oder verworfen werden.")
        if approve:
            report = db.get(QualityReport, case.report_id) if case.report_id else None
            problems = leak_findings(db, case.synthetic_text, report_excerpt=report.excerpt if report else None)
            if problems:  # erneut pruefen: seit dem Anlegen koennen neue Akten/Beteiligte hinzugekommen sein
                raise RegressionCaseError("Freigabe abgelehnt – der Text wirkt nicht mehr synthetisch.")
        case.status = CASE_APPROVED if approve else CASE_REJECTED
        case.approved_by = actor
        case.test_reference = (test_reference or "").strip()[:200] or case.test_reference
        _audit(db, "RegressionCase", case.id, f"regression_case_{case.status}", actor, f"Status: {case.status}")
        db.commit()
        return case

    def export_cases(self, db: Session) -> list[dict]:
        """Freigegebene Faelle als JSON-faehige Liste - nur synthetischer Text und Erwartung, keine Akten-, Meldungs-
        oder Nutzerbezuege. Faelle, die die Leck-Pruefung (erneut) nicht bestehen, werden ausgelassen."""
        cases = []
        for case in db.query(RegressionCase).filter(RegressionCase.status == CASE_APPROVED).order_by(
                RegressionCase.created_at).all():
            if leak_findings(db, case.synthetic_text, report_excerpt=None):
                continue
            cases.append({"id": case.id, "category": case.category, "title": case.title,
                          "text": case.synthetic_text, "expected": json.loads(case.expected_json)})
        return cases
