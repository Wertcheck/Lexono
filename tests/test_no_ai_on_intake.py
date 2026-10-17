"""P3 (17.10.2026): eingehende E-Mails und Dokumente loesen keine unbemerkten externen KI-Aufrufe aus.

Geprueft am echten Code, nur die Netzwerkgrenze des Postfachs ist ein Fake:
- Mail-Abruf (`_run_periodic_mail_ingestion`, ein echter Schleifendurchlauf): Nachricht + PDF-Anhang speichern, lokal
  auslesen, Akte zuordnen;
- Ordner-Erfassung (`IntakeService` ueber den Watcher-Handler) samt lokaler Verarbeitung;
- jeweils: 0 Aufrufe an Cloud-Providern/Gateway-Client und 0 ausgehende Socket-Verbindungen (ausser Loopback);
- spaetere, bewusste KI-Aktion auf einer E-Mail: ohne Akte keine Anfrage; mit Akte genau EINE Anfrage ueber die
  zentrale Pruefung, der markierte Name nicht im verifizierten HTTP-Payload.
Nur synthetische Daten."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

from sqlalchemy.orm import sessionmaker

import app.main as main_module
from app.mail.base import FetchedAttachment, FetchedMessage
from app.models import ApiCallLog, Client, Document, Matter, Message
from tests.auth_test_utils import extract_csrf
from tests.test_main_mail_ingestion import _run_one_iteration, _settings_with_mail
from tests.test_no_ai_on_open import spies  # noqa: F401  (Fixture)
from tests.test_upload_without_ai import _wire
from tests.test_upload_without_ai import client, db_session, recorder, writer  # noqa: F401  (Fixtures)
from tests.test_web_privacy_review import _pdf_bytes

PDF = _pdf_bytes(["Beschluss vom 03.11.2026.", "Der Richter Zweigelt hat den Termin verlegt.",
                  "Streitwert 4.250,00 EUR, Mandantin Henrike Wallbaum-Ostertag."])


class _FakeMailbox:
    def __init__(self, messages):
        self._messages = messages

    def fetch_new_messages(self):
        return self._messages


def test_mail_cycle_stores_and_extracts_without_ai_or_network(db_session, spies, monkeypatch, tmp_path) -> None:
    matter = Matter(client=Client(name="Synthetische Mandantin"), title="Akte MUSTER-001", reference_number="MUSTER-001")
    db_session.add(matter)
    db_session.commit()
    mail = FetchedMessage(
        external_message_id="<p3-synthetisch@example.test>", sender="gericht@example.test",
        recipient="kanzlei@example.test", subject="AZ: MUSTER-001 Beschluss",
        body_text="Aktenzeichen MUSTER-001. Anbei der Beschluss von Richter Zweigelt.",
        received_at=datetime.now(timezone.utc),
        attachments=[FetchedAttachment(filename="beschluss.pdf", content=PDF, mime_type="application/pdf")],
    )
    settings = _settings_with_mail(mail_attachment_storage_dir=str(tmp_path / "mail"))
    monkeypatch.setattr(main_module, "build_mail_provider", lambda _settings: _FakeMailbox([mail]))
    asyncio.run(_run_one_iteration(settings, monkeypatch,
                                   session_factory=sessionmaker(bind=db_session.get_bind())))
    db_session.expire_all()
    message = db_session.query(Message).one()
    document = db_session.query(Document).one()
    assert "Zweigelt" in (document.extracted_text or "")  # lokal ausgelesen
    assert document.message_id == message.id
    assert spies["provider"] == [] and spies["network"] == []  # keine KI, kein Netz (Postfach ist der Fake)
    assert db_session.query(ApiCallLog).count() == 0


def test_folder_intake_extracts_locally_without_ai_or_network(db_session, spies, tmp_path) -> None:
    from app.documents.service import DocumentProcessingService
    from app.ingestion.intake import IntakeService
    from app.ingestion.watcher import _NewFileEventHandler

    incoming = tmp_path / "eingang" / "scan.pdf"
    incoming.parent.mkdir()
    incoming.write_bytes(PDF)
    ingested = []
    handler = _NewFileEventHandler(IntakeService(tmp_path / "ablage"), sessionmaker(bind=db_session.get_bind()),
                                   on_ingested=ingested.append)
    handler._handle_new_file(incoming)
    db_session.expire_all()
    document = db_session.query(Document).one()
    DocumentProcessingService(ocr_enabled=False).process_document(document, db_session, actor="qa")  # wie nach der Zuordnung
    assert ingested and "Zweigelt" in (document.extracted_text or "")
    assert spies["provider"] == [] and spies["network"] == []


def _message(db, *, matter=None) -> Message:
    message = Message(matter_id=matter.id if matter else None, direction="inbound", sender="gericht@example.test",
                      subject="Beschluss MUSTER-002",
                      body_text="Der Richter Zweigelt hat den Termin auf den 22.07.2026 verlegt; Streitwert 4.250,00 EUR.")
    db.add(message)
    db.commit()
    return message


def _act(client, message, *, terms=None):
    page = client.get("/dashboard/chat").text  # CSRF ist sitzungsgebunden
    data = {"csrf_token": extract_csrf(page), "action": "summarize"}
    if terms is not None:
        data["protected_terms"] = json.dumps(terms)
    return client.post(f"/dashboard/chat/from-message/{message.id}", data=data, follow_redirects=False)


def test_ai_action_on_an_unassigned_mail_sends_nothing(client, db_session, recorder) -> None:
    response = _act(client, _message(db_session))
    assert response.status_code == 400 and recorder.requests == []  # erst zuordnen (Aktenisolation)


def test_deliberate_ai_action_on_an_assigned_mail_is_one_checked_request(client, db_session, recorder) -> None:
    matter = Matter(client=Client(name="Synthetische Mandantin"), title="Akte MUSTER-002")
    db_session.add(matter)
    db_session.commit()
    response = _act(client, _message(db_session, matter=matter), terms=["Zweigelt"])
    assert response.status_code == 303
    assert len(recorder.requests) == 1  # genau die bewusste Aktion
    wire = _wire(recorder.requests[0])
    assert "Zweigelt" not in wire  # markierter Name nicht im verifizierten Payload
    assert "4.250,00 EUR" in wire and "22.07.2026" in wire  # juristisch relevante Fakten erhalten
