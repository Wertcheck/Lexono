"""Qualitaetsmeldungen, lokale Diagnose, synthetische Regressionsfaelle (ARCHITECTURE.md §75).

Nur synthetische Daten, kein Netz. Die Diagnose laeuft mit dem echten lokalen Gateway-/Erkennungscode; Presidio wird
durch eine kontrollierte Attrappe ersetzt (Ladezeit), damit Treffer/Nicht-Treffer gezielt einstellbar sind."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import get_db
from app.main import app
from app.models import AuditEvent, Client, Deadline, Document, Matter, Party, QualityReport, RegressionCase
from app.models.base import Base
from app.privacy.detectors import DetectedSpan
from app.privacy.gateway import ClaudePrivacyGateway
from app.privacy.pseudonymizer import Pseudonymizer
from app.privacy.security_check import SecurityCheckService
from app.quality_reports.diagnosis import QualityDiagnostician, locate, sanitize_findings
from app.quality_reports.regression import RegressionCaseError
from app.quality_reports.service import QualityReportError, QualityReportService
from tests.auth_test_utils import create_test_user, extract_csrf, login, seed_roles

DOC_TEXT = ("Sehr geehrte Damen und Herren, die Zeugin Hildegard Quaste wurde am Tatort gesehen. "
            "Bitte antworten Sie bis zum 15.03.2027. Der Fahrer fuhr bei Grün über die Kreuzung.")


def _ner_for(*names: str):
    def detect(text: str) -> list[DetectedSpan]:
        spans = []
        for name in names:
            i = text.find(name)
            if i >= 0:
                spans.append(DetectedSpan("person", i, i + len(name), name))
        return spans
    return detect


def _diagnostician(*ner_names: str) -> QualityDiagnostician:
    ner = _ner_for(*ner_names) if ner_names else (lambda text: [])
    return QualityDiagnostician(
        gateway_factory=lambda: ClaudePrivacyGateway(pseudonymizer=Pseudonymizer(ner_detector=ner),
                                                     security_check=SecurityCheckService(ner_detector=ner)),
        ner_detector=ner,
    )


@pytest.fixture()
def db_session() -> Iterator[Session]:
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture()
def client(db_session: Session) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: (yield db_session)
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _matter(db: Session, client_name: str = "Lisa Grün (synthetisch)", title: str = "Akte QA") -> Matter:
    matter = Matter(client=Client(name=client_name), title=title, reference_number=title)
    db.add(matter)
    db.commit()
    return matter


def _document(db: Session, matter: Matter, text: str = DOC_TEXT, ocr_status: str = "done") -> Document:
    doc = Document(file_path="/tmp/synthetisch.txt", matter_id=matter.id, extracted_text=text, ocr_status=ocr_status)
    db.add(doc)
    db.commit()
    return doc


# --- Lokalisierung ---------------------------------------------------------------------------------------------------


def test_locate_distinguishes_exact_normalized_and_missing() -> None:
    assert locate("Quaste", DOC_TEXT).match == "exact"
    loc = locate("Muster-mann", "Herr Muster-\nmann kam.")  # Silbentrennung ueber Zeilenumbruch
    assert loc.match == "normalized" and "Muster-\nmann" == "Herr Muster-\nmann kam."[loc.start:loc.end]
    assert locate("Unbekannt", DOC_TEXT).match == "missing"
    assert locate("x", "").match == "no_text"


# --- Diagnose je Kategorie -------------------------------------------------------------------------------------------


def test_missed_name_not_detected_is_attributed_to_name_recognition() -> None:
    result = _diagnostician().diagnose(category="missed_personal_data", excerpt="Hildegard Quaste",
                                       document_text=DOC_TEXT, known_entities={})
    assert result.suspected_stage == "presidio"
    assert result.findings["ner_categories"] == [] and result.findings["rule_or_case_data_categories"] == []


def test_missed_name_detected_and_masked_points_to_another_route() -> None:
    result = _diagnostician("Hildegard Quaste").diagnose(category="missed_personal_data", excerpt="Hildegard Quaste",
                                                         document_text=DOC_TEXT, known_entities={})
    assert (result.suspected_stage, result.reproduced) == ("routing", False)
    assert result.findings["ner_categories"] == ["person"] and result.findings["value_left_local_path"] is False


def test_missing_excerpt_points_to_ocr_or_extraction() -> None:
    d = _diagnostician()
    assert d.diagnose(category="missed_personal_data", excerpt="Gerda Nirgends", document_text=DOC_TEXT,
                      known_entities={}, ocr_used=True).suspected_stage == "ocr"
    assert d.diagnose(category="missed_personal_data", excerpt="Gerda Nirgends", document_text=DOC_TEXT,
                      known_entities={}, ocr_used=False).suspected_stage == "extraction"


def test_over_pseudonymization_by_case_data_is_attributed_to_routing() -> None:
    result = _diagnostician().diagnose(category="over_pseudonymization", excerpt="Grün", document_text=DOC_TEXT,
                                       known_entities={"mandant": ["Lisa Grün", "Grün"]})
    assert (result.suspected_stage, result.reproduced) == ("routing", True)
    assert result.findings["known_entity_hit"] is True and result.findings["value_kept_in_payload"] is False


def test_altered_content_preserved_locally_is_not_blamed_on_pipeline() -> None:
    result = _diagnostician().diagnose(category="altered_legal_content", excerpt="bis zum 15.03.2027",
                                       document_text=DOC_TEXT, known_entities={})
    assert result.suspected_stage == "unknown" and result.findings["local_pipeline_preserves_content"] is True


@pytest.mark.parametrize("excerpt,stored,expected_stage", [
    ("bis zum 15.03.2027", [], "routing"),  # Regel findet, Analyse hat nichts gespeichert
    ("bei Grün über die Kreuzung", [], "deadline_rules"),  # Regel findet nichts
])
def test_missed_deadline_stages(excerpt, stored, expected_stage) -> None:
    result = _diagnostician().diagnose(category="missed_deadline", excerpt=excerpt, document_text=DOC_TEXT,
                                       known_entities={}, document_deadlines=stored)
    assert result.suspected_stage == expected_stage


def test_missed_deadline_that_was_rejected_points_to_review() -> None:
    rejected = Deadline(due_date=date(2027, 3, 15), review_status="rejected")
    result = _diagnostician().diagnose(category="missed_deadline", excerpt="bis zum 15.03.2027",
                                       document_text=DOC_TEXT, known_entities={}, document_deadlines=[rejected])
    assert result.suspected_stage == "validation" and result.findings["matching_stored_suggestion_statuses"] == ["rejected"]


def test_diagnosis_error_is_contained() -> None:
    def broken():
        raise RuntimeError("Quaste darf nicht im Ergebnis landen")
    result = QualityDiagnostician(gateway_factory=broken, ner_detector=lambda t: []).diagnose(
        category="missed_personal_data", excerpt="Hildegard Quaste", document_text=DOC_TEXT, known_entities={})
    assert result.suspected_stage == "unknown" and result.findings["diagnosis_error"] == "RuntimeError"
    assert "Quaste" not in json.dumps(sanitize_findings(result.findings))


def test_sanitize_drops_unknown_keys_and_text_values() -> None:
    dirty = {"excerpt_in_text": "exact", "ner_categories": ["person"], "leak": "Hildegard Quaste",
             "known_entity_hit": "Hildegard Quaste", "rule_or_case_data_categories": ["Hildegard Quaste"]}
    assert sanitize_findings(dirty) == {"excerpt_in_text": "exact", "ner_categories": ["person"]}


# --- Workflow --------------------------------------------------------------------------------------------------------


def _service(*ner_names: str) -> QualityReportService:
    return QualityReportService(diagnostician=_diagnostician(*ner_names))


def test_full_flow_keeps_content_out_of_diagnosis_and_audit_and_clears_excerpt(db_session: Session) -> None:
    matter = _matter(db_session)
    doc = _document(db_session, matter)
    svc = _service()
    report = svc.create(db_session, matter_id=matter.id, document_id=doc.id, category="missed_personal_data",
                        excerpt="Hildegard Quaste", description="Name der Zeugin blieb im Entwurf stehen",
                        actor="ma@kanzlei.test")
    svc.diagnose(db_session, report, actor="anwalt@kanzlei.test")
    assert report.status == "diagnostiziert" and report.suspected_stage == "presidio"
    assert "Quaste" not in report.diagnosis_json and "Zeugin" not in report.diagnosis_json

    with pytest.raises(QualityReportError):  # behoben ohne bestaetigte Ursache
        svc.close(db_session, report, outcome="behoben", note="", fix_reference="abc123", actor="anwalt@kanzlei.test")
    svc.confirm_cause(db_session, report, stage="presidio", note="NER-Luecke", actor="anwalt@kanzlei.test")
    with pytest.raises(QualityReportError):  # behoben ohne Korrektur und ohne freigegebenen Fall
        svc.close(db_session, report, outcome="behoben", note="", fix_reference="", actor="anwalt@kanzlei.test")
    svc.close(db_session, report, outcome="behoben", note="", fix_reference="commit abc123", actor="anwalt@kanzlei.test")
    assert report.status == "behoben" and report.excerpt is None and report.fix_reference == "commit abc123"

    audit = " ".join(d or "" for (d,) in db_session.query(AuditEvent.details).filter(
        AuditEvent.entity_type == "QualityReport").all())
    assert "Quaste" not in audit and "Zeugin" not in audit
    assert {e for (e,) in db_session.query(AuditEvent.event_type).filter(AuditEvent.entity_id == report.id)} == {
        "quality_report_created", "quality_report_diagnosed", "quality_report_cause_confirmed", "quality_report_behoben"}


def test_dismiss_clears_excerpt_and_blocks_further_diagnosis(db_session: Session) -> None:
    matter = _matter(db_session)
    svc = _service()
    report = svc.create(db_session, matter_id=matter.id, document_id=None, category="privacy_concern",
                        excerpt="irgendwas", description="", actor="x")
    svc.close(db_session, report, outcome="verworfen", note="kein Fehler", fix_reference="", actor="y")
    assert report.excerpt is None
    with pytest.raises(QualityReportError):
        svc.diagnose(db_session, report, actor="y")


def test_create_enforces_matter_isolation_and_limits(db_session: Session) -> None:
    a, b = _matter(db_session, title="A"), _matter(db_session, "Kurt Beispiel (synthetisch)", title="B")
    doc_b = _document(db_session, b)
    svc = _service()
    with pytest.raises(QualityReportError):
        svc.create(db_session, matter_id=a.id, document_id=doc_b.id, category="missed_deadline", excerpt="x",
                   description="", actor="x")
    with pytest.raises(QualityReportError):
        svc.create(db_session, matter_id=a.id, document_id=None, category="unbekannt", excerpt="x", description="",
                   actor="x")
    with pytest.raises(QualityReportError):
        svc.create(db_session, matter_id=a.id, document_id=None, category="missed_deadline", excerpt="x" * 301,
                   description="", actor="x")
    a.deleted_at = date(2026, 1, 1)
    db_session.commit()
    with pytest.raises(QualityReportError):
        svc.create(db_session, matter_id=a.id, document_id=None, category="missed_deadline", excerpt="x",
                   description="", actor="x")
    assert db_session.query(QualityReport).count() == 0


def test_diagnosis_uses_only_the_reported_matter(db_session: Session) -> None:
    """Aktendaten einer fremden Akte duerfen die Diagnose nicht beeinflussen."""
    a = _matter(db_session, "Lisa Grün (synthetisch)", "A")
    b = _matter(db_session, "Hildegard Quaste", "B")  # gleicher Name als Mandantin einer ANDEREN Akte
    doc = _document(db_session, a)
    svc = _service()
    report = svc.create(db_session, matter_id=a.id, document_id=doc.id, category="missed_personal_data",
                        excerpt="Hildegard Quaste", description="", actor="x")
    svc.diagnose(db_session, report, actor="y")
    assert json.loads(report.diagnosis_json)["known_entity_hit"] is False
    assert b.id != a.id


def test_deleting_report_keeps_synthetic_case_without_link(db_session: Session) -> None:
    matter = _matter(db_session)
    svc = _service()
    report = svc.create(db_session, matter_id=matter.id, document_id=None, category="missed_personal_data",
                        excerpt="Hildegard Quaste", description="", actor="x")
    svc.confirm_cause(db_session, report, stage="presidio", note="", actor="y")
    case = svc.create_case(db_session, report, category="missed_personal_data", title="Zeugin im Fliesstext",
                           synthetic_text="Die Zeugin Erna Probewitz wurde gesehen.", must_mask="Erna Probewitz",
                           must_keep="", due_dates="", actor="y")
    svc.delete(db_session, report, actor="y")
    db_session.refresh(case)
    assert case.report_id is None and db_session.query(QualityReport).count() == 0


# --- Regressionsfaelle -----------------------------------------------------------------------------------------------


@pytest.fixture()
def confirmed_report(db_session: Session) -> QualityReport:
    matter = _matter(db_session)
    db_session.add(Party(matter_id=matter.id, name="Konrad Pfefferkorn", role="Gegner"))
    db_session.commit()
    svc = _service()
    report = svc.create(db_session, matter_id=matter.id, document_id=None, category="missed_personal_data",
                        excerpt="Hildegard Quaste", description="", actor="x")
    svc.confirm_cause(db_session, report, stage="presidio", note="", actor="y")
    return report


@pytest.mark.parametrize("text", [
    "Die Zeugin Hildegard Quaste wurde gesehen.",  # Fundstelle
    "Herr Pfefferkorn widersprach.",  # Nachname eines gespeicherten Beteiligten
    "Frau Lisa Grün (synthetisch) kam.",  # gespeicherter Mandant
    "Erreichbar unter erna.probe@example.org.",  # Kontaktdaten
])
def test_case_rejects_text_that_is_not_synthetic(db_session: Session, confirmed_report, text) -> None:
    with pytest.raises(RegressionCaseError):
        _service().create_case(db_session, confirmed_report, category="missed_personal_data", title="t",
                               synthetic_text=text, must_mask=text.split()[1], must_keep="", due_dates="", actor="y")
    assert db_session.query(RegressionCase).count() == 0


def test_case_expectation_must_match_category_and_text(db_session: Session, confirmed_report) -> None:
    svc = _service()
    for kwargs in ({"must_mask": ""}, {"must_mask": "Nicht im Text"}):
        with pytest.raises(RegressionCaseError):
            svc.create_case(db_session, confirmed_report, category="missed_personal_data", title="t",
                            synthetic_text="Die Zeugin Erna Probewitz wurde gesehen.", must_keep="", due_dates="",
                            actor="y", **kwargs)
    with pytest.raises(RegressionCaseError):
        svc.create_case(db_session, confirmed_report, category="missed_deadline", title="t",
                        synthetic_text="Bitte bis 1.4. antworten.", must_mask="", must_keep="", due_dates="1.4.2027",
                        actor="y")


def test_case_requires_confirmed_cause_and_export_contains_only_synthetic_data(db_session: Session) -> None:
    matter = _matter(db_session)
    svc = _service()
    report = svc.create(db_session, matter_id=matter.id, document_id=None, category="missed_personal_data",
                        excerpt="Hildegard Quaste", description="geheime Beschreibung", actor="x")
    with pytest.raises(RegressionCaseError):
        svc.create_case(db_session, report, category="missed_personal_data", title="t",
                        synthetic_text="Die Zeugin Erna Probewitz wurde gesehen.", must_mask="Erna Probewitz",
                        must_keep="", due_dates="", actor="y")
    svc.confirm_cause(db_session, report, stage="presidio", note="", actor="y")
    case = svc.create_case(db_session, report, category="missed_personal_data", title="Zeugin im Fliesstext",
                           synthetic_text="Die Zeugin Erna Probewitz wurde gesehen.", must_mask="Erna Probewitz",
                           must_keep="", due_dates="", actor="y")
    assert svc.export_cases(db_session) == []  # Entwurf wird nicht exportiert
    svc.decide_case(db_session, case, approve=True, test_reference="tests/regression_cases/qa.json", actor="z")
    exported = svc.export_cases(db_session)
    dumped = json.dumps(exported, ensure_ascii=False)
    assert len(exported) == 1 and exported[0]["expected"]["must_mask"] == ["Erna Probewitz"]
    for secret in ("Hildegard Quaste", "geheime Beschreibung", matter.id, report.id, "Lisa Grün"):
        assert secret not in dumped
    with pytest.raises(RegressionCaseError):  # nur Entwuerfe entscheiden
        svc.decide_case(db_session, case, approve=False, test_reference="", actor="z")


def test_approval_rechecks_leaks_against_names_added_later(db_session: Session, confirmed_report) -> None:
    svc = _service()
    case = svc.create_case(db_session, confirmed_report, category="missed_personal_data", title="t",
                           synthetic_text="Die Zeugin Erna Probewitz wurde gesehen.", must_mask="Erna Probewitz",
                           must_keep="", due_dates="", actor="y")
    other = _matter(db_session, "Erna Probewitz", "C")  # spaeter real angelegt
    assert other.client.name == "Erna Probewitz"
    with pytest.raises(RegressionCaseError):
        svc.decide_case(db_session, case, approve=True, test_reference="", actor="z")
    assert case.status == "entwurf"


# --- Web: Rechte, CSRF -----------------------------------------------------------------------------------------------


def _login_role(db: Session, client: TestClient, role: str) -> str:
    roles = seed_roles(db)
    email = f"{role}@kanzlei.test"
    create_test_user(db, roles[role], email)
    login(client, email)
    return extract_csrf(client.get("/dashboard/tasks").text)


def test_staff_can_report_but_not_review(client: TestClient, db_session: Session, monkeypatch) -> None:
    matter = _matter(db_session)
    doc = _document(db_session, matter)
    csrf = _login_role(db_session, client, "mitarbeiter")
    page = client.get(f"/dashboard/matters/{matter.id}/document/{doc.id}").text
    assert "Problem mit Datenschutz oder Inhalt melden" in page

    response = client.post("/dashboard/quality-reports", data={
        "csrf_token": csrf, "matter_id": matter.id, "document_id": doc.id, "category": "missed_personal_data",
        "excerpt": "Hildegard Quaste", "description": ""}, follow_redirects=False)
    assert response.status_code == 303 and "quality_reported=1" in response.headers["location"]
    report = db_session.query(QualityReport).one()
    assert report.reported_by == "mitarbeiter@kanzlei.test"

    assert client.get("/dashboard/quality-reports").status_code == 403
    assert client.get("/dashboard/quality-reports/cases/export.json").status_code == 403
    for path in (f"/dashboard/quality-reports/{report.id}/diagnose", f"/dashboard/quality-reports/{report.id}/delete"):
        assert client.post(path, data={"csrf_token": csrf}, follow_redirects=False).status_code == 403
    assert db_session.query(QualityReport).count() == 1 and report.diagnosis_json is None


def test_lawyer_reviews_via_web_and_csrf_is_required(client: TestClient, db_session: Session, monkeypatch) -> None:
    import app.web.quality_reports_router as router_module

    monkeypatch.setattr(router_module, "QualityReportService", lambda: _service())
    matter = _matter(db_session)
    doc = _document(db_session, matter)
    report = _service().create(db_session, matter_id=matter.id, document_id=doc.id, category="missed_personal_data",
                               excerpt="Hildegard Quaste", description="", actor="x")
    csrf = _login_role(db_session, client, "anwalt")
    assert client.post(f"/dashboard/quality-reports/{report.id}/diagnose", data={"csrf_token": "falsch"},
                       follow_redirects=False).status_code in (400, 403)
    assert report.diagnosis_json is None

    response = client.post(f"/dashboard/quality-reports/{report.id}/diagnose", data={"csrf_token": csrf},
                           follow_redirects=False)
    assert response.status_code == 303
    db_session.refresh(report)
    assert report.suspected_stage == "presidio"
    page = client.get(f"/dashboard/quality-reports?selected={report.id}").text
    assert "Namenserkennung (Presidio/NER)" in page and "Hildegard Quaste" in page  # Fundstelle nur hier, lokal
    export = client.get("/dashboard/quality-reports/cases/export.json")
    assert export.status_code == 200 and export.json()["cases"] == []


def test_export_format_is_directly_usable_by_the_regression_harness(db_session: Session, confirmed_report) -> None:
    svc = _service()
    case = svc.create_case(db_session, confirmed_report, category="missed_personal_data", title="t",
                           synthetic_text="Die Zeugin Erna Probewitz wurde gesehen.", must_mask="Erna Probewitz",
                           must_keep="", due_dates="", actor="y")
    svc.decide_case(db_session, case, approve=True, test_reference="", actor="z")
    (exported,) = svc.export_cases(db_session)
    assert set(exported) == {"id", "category", "title", "text", "expected"}
    assert set(exported["expected"]) == {"must_mask", "must_keep", "expected_due_dates"}


def test_production_default_diagnosis_with_real_presidio_stays_content_free() -> None:
    """Produktiv-Default (echter Gateway inkl. Presidio-NER, kein Netz): Ergebnis nur Stufen/Kategorien/Zaehler."""
    result = QualityDiagnostician().diagnose(category="missed_personal_data", excerpt="Hildegard Quaste",
                                             document_text=DOC_TEXT, known_entities={})
    clean = sanitize_findings(result.findings)
    assert result.suspected_stage in ("routing", "presidio", "pseudonymization")
    assert clean["excerpt_in_text"] == "exact" and "gateway_allowed" in clean
    dumped = json.dumps(clean, ensure_ascii=False)
    for fragment in ("Quaste", "Hildegard", "Zeugin", "Tatort", "PERSON_"):
        assert fragment not in dumped


def test_report_confirmation_and_error_are_visible_not_collapsed(client: TestClient, db_session: Session) -> None:
    """Im echten Klickablauf gefunden: die Bestaetigung lag im zugeklappten <details>-Bereich und war unsichtbar."""
    matter = _matter(db_session)
    doc = _document(db_session, matter)
    _login_role(db_session, client, "mitarbeiter")
    base = f"/dashboard/matters/{matter.id}/document/{doc.id}"
    assert '<details style="margin-top: 16px;">' in client.get(base).text  # ohne Anlass zugeklappt
    for query, text in (("?quality_reported=1", "Danke – die Meldung wurde gespeichert"),
                        ("?quality_error=Unbekannte%20Kategorie.", "Unbekannte Kategorie.")):
        page = client.get(base + query).text
        assert '<details style="margin-top: 16px;" open>' in page and text in page


# --- Aufbewahrung (§75.1) --------------------------------------------------------------------------------------------


def _aged(db: Session, report: QualityReport, days: int) -> None:
    from datetime import datetime, timedelta, timezone

    report.created_at = datetime.now(timezone.utc) - timedelta(days=days)
    db.commit()


def test_closing_clears_excerpt_and_description(db_session: Session) -> None:
    matter = _matter(db_session)
    svc = _service()
    report = svc.create(db_session, matter_id=matter.id, document_id=None, category="privacy_concern",
                        excerpt="Hildegard Quaste", description="Zeugin Quaste stand im Entwurf", actor="x")
    svc.close(db_session, report, outcome="verworfen", note="", fix_reference="", actor="y")
    assert (report.excerpt, report.description) == (None, None)


def test_open_reports_lose_content_after_the_retention_period_but_stay_traceable(db_session: Session) -> None:
    matter = _matter(db_session)
    doc = _document(db_session, matter)
    svc = _service()
    old = svc.create(db_session, matter_id=matter.id, document_id=doc.id, category="missed_personal_data",
                     excerpt="Hildegard Quaste", description="Zeugin Quaste", actor="x")
    svc.diagnose(db_session, old, actor="y")
    young = svc.create(db_session, matter_id=matter.id, document_id=doc.id, category="missed_deadline",
                       excerpt="bis zum 15.03.2027", description="Frist fehlt", actor="x")
    _aged(db_session, old, 91)
    _aged(db_session, young, 89)

    assert svc.expire_content(db_session, content_days=90) == 1
    db_session.refresh(old)
    db_session.refresh(young)
    assert (old.excerpt, old.description) == (None, None)
    assert old.status == "diagnostiziert" and old.suspected_stage == "presidio" and old.diagnosis_json
    assert (young.excerpt, young.description) == ("bis zum 15.03.2027", "Frist fehlt")
    (event,) = db_session.query(AuditEvent).filter(AuditEvent.entity_id == old.id,
                                                   AuditEvent.event_type == "quality_report_content_expired").all()
    assert event.actor == "system" and "frist_90_tage" in event.details and "Quaste" not in event.details
    assert svc.expire_content(db_session, content_days=90) == 0  # idempotent


def test_reports_of_deleted_matters_lose_content_immediately(db_session: Session) -> None:
    from datetime import datetime, timezone

    matter = _matter(db_session)
    report = _service().create(db_session, matter_id=matter.id, document_id=None, category="missed_deadline",
                               excerpt="bis zum 15.03.2027", description="", actor="x")
    matter.deleted_at = datetime.now(timezone.utc)
    db_session.commit()
    assert _service().expire_content(db_session, content_days=3650) == 1
    db_session.refresh(report)
    assert report.excerpt is None
    assert "akte_geloescht" in db_session.query(AuditEvent.details).filter(
        AuditEvent.event_type == "quality_report_content_expired").scalar()


def test_review_page_applies_retention_before_showing_content(client: TestClient, db_session: Session,
                                                              monkeypatch) -> None:
    import app.web.quality_reports_router as router_module

    monkeypatch.setattr(router_module, "QualityReportService", lambda: _service())
    matter = _matter(db_session)
    report = _service().create(db_session, matter_id=matter.id, document_id=None, category="missed_deadline",
                               excerpt="Hildegard Quaste", description="", actor="x")
    _aged(db_session, report, 400)
    _login_role(db_session, client, "anwalt")
    page = client.get(f"/dashboard/quality-reports?selected={report.id}").text
    assert "Hildegard Quaste" not in page and "Ablauf der Aufbewahrungsfrist" in page


@pytest.mark.parametrize("value,ok", [(0, False), (1, True), (90, True), (3650, True), (3651, False)])
def test_retention_setting_range(value, ok) -> None:
    from pydantic import ValidationError

    from app.config.settings import Settings

    if ok:
        assert Settings(_env_file=None, quality_report_content_days=value).quality_report_content_days == value
    else:
        with pytest.raises(ValidationError):
            Settings(_env_file=None, quality_report_content_days=value)
