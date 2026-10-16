"""Dashboard: Qualitaetsmeldungen, lokale Diagnose, synthetische Regressionsfaelle (ARCHITECTURE.md §75).

Melden darf jede angemeldete Rolle (aus der Dokumentansicht, an genau eine Akte gebunden). Ansehen, Diagnose,
Ursache bestaetigen, Abschliessen, Loeschen sowie Regressionsfaelle anlegen/freigeben/exportieren nur mit
PERM_QUALITY_REVIEW (Admin, Anwalt) - serverseitig, unabhaengig von der Anzeige. Kein Weg sendet Inhalte nach aussen;
der Export enthaelt ausschliesslich freigegebene synthetische Faelle."""

from __future__ import annotations

import json
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.api.deps import get_or_404
from app.auth.permissions import (
    PERM_QUALITY_REVIEW,
    PermissionDeniedError,
    has_permission,
    require_login,
    require_role,
)
from app.config import get_settings
from app.db.session import get_db
from app.models import Matter, QualityReport, RegressionCase, User
from app.models.quality_report import DIAGNOSIS_STAGES, QUALITY_CATEGORIES, STATUS_DISMISSED, STATUS_RESOLVED
from app.quality_reports.regression import CASE_CATEGORIES, RegressionCaseError
from app.quality_reports.service import QualityReportError, QualityReportService
from app.web.template_paths import TEMPLATES_DIR

router = APIRouter(prefix="/dashboard/quality-reports", tags=["dashboard-quality-reports"])
templates = Jinja2Templates(directory=TEMPLATES_DIR)


def _require_review(user: User) -> None:
    if not has_permission(user, PERM_QUALITY_REVIEW):
        raise PermissionDeniedError("Qualitätsmeldungen prüfen dürfen nur Anwälte und Administratoren.")


def _back(report_id: str | None = None, *, error: str | None = None, notice: str | None = None) -> RedirectResponse:
    url = "/dashboard/quality-reports"
    params = []
    if report_id:
        params.append(f"selected={report_id}")
    if error:
        params.append(f"error={quote(error)}")
    if notice:
        params.append(f"notice={quote(notice)}")
    return RedirectResponse(url=url + ("?" + "&".join(params) if params else ""), status_code=303)


@router.post("")
def create_report(
    matter_id: str = Form(...),
    document_id: str = Form(""),
    category: str = Form(...),
    excerpt: str = Form(""),
    description: str = Form(""),
    current_user: User = Depends(require_role()),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    target = f"/dashboard/matters/{quote(matter_id)}" + (f"/document/{quote(document_id)}" if document_id else "")
    try:
        QualityReportService().create(db, matter_id=matter_id, document_id=document_id or None, category=category,
                                      excerpt=excerpt, description=description, actor=current_user.email)
    except QualityReportError as exc:
        return RedirectResponse(url=f"{target}?quality_error={quote(str(exc))}", status_code=303)
    return RedirectResponse(url=f"{target}?quality_reported=1", status_code=303)


@router.get("", response_class=HTMLResponse)
def reports_page(
    request: Request,
    selected: str = "",
    error: str = "",
    notice: str = "",
    current_user: User = Depends(require_login),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    _require_review(current_user)
    # Aufbewahrung (§75.1) auch ohne Neustart wirksam: vor dem Anzeigen abgelaufene Inhalte leeren.
    QualityReportService().expire_content(db, content_days=get_settings().quality_report_content_days)
    reports = db.query(QualityReport).order_by(QualityReport.created_at.desc()).all()
    matters = {m.id: m for m in db.query(Matter).filter(Matter.id.in_({r.matter_id for r in reports})).all()} if reports else {}
    report = db.get(QualityReport, selected) if selected else None
    cases = db.query(RegressionCase).order_by(RegressionCase.created_at.desc()).all()
    context = {
        "request": request,
        "current_user": current_user,
        "active_nav": "Qualitätsmeldungen",
        "reports": reports,
        "matters": matters,
        "report": report,
        "diagnosis": json.loads(report.diagnosis_json) if report and report.diagnosis_json else None,
        "report_cases": [c for c in cases if report and c.report_id == report.id],
        "cases": cases,
        "categories": QUALITY_CATEGORIES,
        "case_categories": CASE_CATEGORIES,
        "stages": DIAGNOSIS_STAGES,
        "csrf_token": getattr(request.state, "csrf_token", ""),
        "error": error or None,
        "notice": notice or None,
    }
    return templates.TemplateResponse(request, "quality_reports.html", context)


@router.post("/{report_id}/diagnose")
def diagnose_report(report_id: str, current_user: User = Depends(require_role(permission=PERM_QUALITY_REVIEW)),
                    db: Session = Depends(get_db)) -> RedirectResponse:
    report = get_or_404(db, QualityReport, report_id, "Meldung")
    try:
        QualityReportService().diagnose(db, report, actor=current_user.email)
    except QualityReportError as exc:
        return _back(report.id, error=str(exc))
    return _back(report.id, notice="Diagnose abgeschlossen (lokal, ohne Cloud).")


@router.post("/{report_id}/confirm")
def confirm_report(report_id: str, stage: str = Form(...), note: str = Form(""),
                   current_user: User = Depends(require_role(permission=PERM_QUALITY_REVIEW)),
                   db: Session = Depends(get_db)) -> RedirectResponse:
    report = get_or_404(db, QualityReport, report_id, "Meldung")
    try:
        QualityReportService().confirm_cause(db, report, stage=stage, note=note, actor=current_user.email)
    except QualityReportError as exc:
        return _back(report.id, error=str(exc))
    return _back(report.id, notice="Ursache bestätigt.")


@router.post("/{report_id}/close")
def close_report(report_id: str, outcome: str = Form(...), note: str = Form(""), fix_reference: str = Form(""),
                 current_user: User = Depends(require_role(permission=PERM_QUALITY_REVIEW)),
                 db: Session = Depends(get_db)) -> RedirectResponse:
    report = get_or_404(db, QualityReport, report_id, "Meldung")
    try:
        QualityReportService().close(db, report, outcome=outcome, note=note, fix_reference=fix_reference,
                                     actor=current_user.email)
    except QualityReportError as exc:
        return _back(report.id, error=str(exc))
    label = "behoben" if outcome == STATUS_RESOLVED else "verworfen" if outcome == STATUS_DISMISSED else outcome
    return _back(report.id, notice=f"Meldung {label}; Fundstelle gelöscht.")


@router.post("/{report_id}/delete")
def delete_report(report_id: str, current_user: User = Depends(require_role(permission=PERM_QUALITY_REVIEW)),
                  db: Session = Depends(get_db)) -> RedirectResponse:
    report = get_or_404(db, QualityReport, report_id, "Meldung")
    QualityReportService().delete(db, report, actor=current_user.email)
    return _back(notice="Meldung gelöscht.")


@router.post("/{report_id}/cases")
def create_case(report_id: str, category: str = Form(...), title: str = Form(...), synthetic_text: str = Form(...),
                must_mask: str = Form(""), must_keep: str = Form(""), due_dates: str = Form(""),
                current_user: User = Depends(require_role(permission=PERM_QUALITY_REVIEW)),
                db: Session = Depends(get_db)) -> RedirectResponse:
    report = get_or_404(db, QualityReport, report_id, "Meldung")
    try:
        QualityReportService().create_case(db, report, category=category, title=title, synthetic_text=synthetic_text,
                                           must_mask=must_mask, must_keep=must_keep, due_dates=due_dates,
                                           actor=current_user.email)
    except RegressionCaseError as exc:
        return _back(report.id, error=str(exc))
    return _back(report.id, notice="Regressionsfall als Entwurf angelegt.")


@router.post("/cases/{case_id}/decide")
def decide_case(case_id: str, decision: str = Form(...), test_reference: str = Form(""),
                current_user: User = Depends(require_role(permission=PERM_QUALITY_REVIEW)),
                db: Session = Depends(get_db)) -> RedirectResponse:
    case = get_or_404(db, RegressionCase, case_id, "Regressionsfall")
    try:
        QualityReportService().decide_case(db, case, approve=decision == "approve", test_reference=test_reference,
                                           actor=current_user.email)
    except RegressionCaseError as exc:
        return _back(case.report_id, error=str(exc))
    return _back(case.report_id, notice="Regressionsfall " + ("freigegeben." if decision == "approve" else "verworfen."))


@router.get("/cases/export.json")
def export_cases(current_user: User = Depends(require_login), db: Session = Depends(get_db)) -> JSONResponse:
    """Nur freigegebene synthetische Faelle (erneut leck-geprueft) - zur bewussten Uebernahme durch Entwickler nach
    tests/regression_cases/. Kein automatischer Versand, keine Akten-/Meldungsbezuege."""
    _require_review(current_user)
    return JSONResponse(
        {"format": "lexono-regression-cases/1", "cases": QualityReportService().export_cases(db)},
        headers={"Content-Disposition": 'attachment; filename="lexono_regressionsfaelle.json"',
                 "Cache-Control": "no-store"},
    )
