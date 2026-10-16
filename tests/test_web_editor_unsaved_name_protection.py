"""§55: "Namen schützen" fuer Namen im noch NICHT gespeicherten Editorinhalt.

Vorher prueften Browser und Server gegen verschiedene Texte: der Browser gegen den aktuellen Editorinhalt, der Server
gegen Anweisung, Auswahl und den GESPEICHERTEN Entwurf - ein Name, der nur im ungespeicherten Text stand, wurde
abgewiesen und die ganze KI-Bearbeitung abgebrochen. Jetzt schickt der Editor den aktuellen Inhalt als Pruefgrundlage
mit; er wird weder gespeichert noch in die Cloud-Anfrage uebernommen. Nur synthetische Daten."""

from __future__ import annotations

import json
import re

import pytest
from sqlalchemy.orm import Session

from app.models import Draft
from app.models.privacy_decision import PrivacyDecision
from app.web.service_factory import get_editor_service_for_ai_edit  # noqa: F401 - vom client-Fixture gesetzt
from tests.auth_test_utils import extract_csrf
from tests.test_web_form_name_protection import (  # noqa: F401 - Fixtures
    EchoWriter,
    _draft,
    _sent,
    client,
    db_session,
    writer,
)

SAVED = "Gespeicherter Entwurf: Der Termin wurde auf den 22.07.2026 verlegt; Streitwert 4.250,00 EUR nach § 3 ZPO."
UNSAVED_SENTENCE = "Richter Brummelbeck hat den Termin am 22.07.2026 verlegt (Az. 3 C 117/26)."
UNSAVED = SAVED + "\n" + UNSAVED_SENTENCE


def _ai_edit(client, draft_id: str, *, instruction: str, selected: str = "", terms=None, editor_text=None):
    page = client.get(f"/dashboard/drafts/{draft_id}/edit")
    data = {"csrf_token": extract_csrf(page.text), "instruction_text": instruction, "purpose": "improve_draft",
            "selected_text": selected}
    if terms is not None:
        data["protected_terms"] = json.dumps(terms)
    if editor_text is not None:
        data["editor_text"] = editor_text
    return client.post(f"/dashboard/drafts/{draft_id}/ai-edit", data=data)


def test_before_without_the_editor_text_an_unsaved_name_is_still_rejected(client, db_session, writer) -> None:
    """Gegenprobe (Verhalten bis §54 und weiterhin ohne mitgeschickten Editortext): kein Senden, keine Entscheidung."""
    draft = _draft(db_session, SAVED)
    response = _ai_edit(client, draft.id, instruction="Kürzen.", terms=["Brummelbeck"])
    assert response.status_code == 422 and writer.received_payloads == []
    assert db_session.query(PrivacyDecision).count() == 0


def test_name_only_in_unsaved_text_can_be_protected_without_saving(client, db_session, writer) -> None:
    draft = _draft(db_session, SAVED)
    response = _ai_edit(client, draft.id, instruction="Formuliere den markierten Satz neu.", selected=UNSAVED_SENTENCE,
                        terms=["Brummelbeck"], editor_text=UNSAVED)
    assert response.status_code == 200 and response.json()["success"] is True
    sent = _sent(writer)
    assert "Brummelbeck" not in sent
    # Inhalt erhalten, Aktenzeichen wie bisher geschuetzt ("Richter" am Satzanfang: bekannte Grenze §46/§50)
    for value in ("hat den Termin am 22.07.2026 verlegt", "[AKTENZEICHEN_"):
        assert value in sent
    assert db_session.query(PrivacyDecision).filter_by(matter_id=draft.matter_id, term="Brummelbeck").count() == 1
    db_session.refresh(draft)
    assert draft.content == SAVED  # der ungespeicherte Editortext wurde NICHT gespeichert
    assert "editor_text" not in sent and "Gespeicherter Entwurf" not in sent  # nicht in die Cloud-Anfrage uebernommen


def test_name_only_in_unsaved_text_outside_the_selection_is_recorded_for_the_matter(client, db_session, writer) -> None:
    """Steht der Name nur im ungespeicherten Text (nicht in Anweisung/Auswahl), geht er in DIESER Anfrage gar nicht hinaus
    - die Markierung wird trotzdem angenommen und gilt aktenweit (z. B. fuer die spaetere Entwurfspruefung)."""
    draft = _draft(db_session, SAVED)
    response = _ai_edit(client, draft.id, instruction="Kürzen.", terms=["Brummelbeck"], editor_text=UNSAVED)
    assert response.status_code == 200
    assert db_session.query(PrivacyDecision).filter_by(matter_id=draft.matter_id, term="Brummelbeck").count() == 1
    from tests.test_review_engine import FakeReviewProvider, _engine

    draft.content = UNSAVED  # spaeter gespeichert und zur Pruefung gegeben
    db_session.commit()
    provider = FakeReviewProvider()
    _engine(provider).review_draft(draft.id, db_session)
    assert "Brummelbeck" not in provider.received_payloads[0].anonymisierter_sachverhalt


@pytest.mark.parametrize("editor_text,terms", [
    (SAVED + "\nRichter Weber hat den Termin verlegt.", ["Brummelbeck"]),  # Name nach der Markierung ersetzt
    (SAVED, ["Brummelbeck"]),  # Satz wieder geloescht
    (UNSAVED, ["Brummel"]),  # nur ein Teil eines Wortes
    (UNSAVED, ["Richter Brummelbeck"]),  # Rolle im Namen
    (UNSAVED, ["Brummelbeck'); DROP TABLE x;--"]),  # manipuliert
])
def test_stale_ambiguous_or_invalid_marking_aborts_safely(client, db_session, writer, editor_text, terms) -> None:
    draft = _draft(db_session, SAVED)
    response = _ai_edit(client, draft.id, instruction="Kürzen.", terms=terms, editor_text=editor_text)
    assert response.status_code == 422 and response.json()["success"] is False
    assert "Brummel" not in response.json()["error"] and response.json()["error"]  # verstaendlich, ohne Begriff
    assert writer.received_payloads == [] and db_session.query(PrivacyDecision).count() == 0


def test_too_long_editor_text_aborts_without_a_request(client, db_session, writer) -> None:
    draft = _draft(db_session, SAVED)
    response = _ai_edit(client, draft.id, instruction="Kürzen.", terms=["Brummelbeck"],
                        editor_text=UNSAVED + " x" * 120_000)
    assert response.status_code == 422 and writer.received_payloads == []


def test_failed_storage_sends_nothing_and_keeps_the_draft(client, db_session, writer, monkeypatch) -> None:
    import app.web.name_protection as name_protection_module

    def broken(*_args, **_kwargs):
        raise RuntimeError("simulierter Speicherfehler")

    monkeypatch.setattr(name_protection_module, "record_manual_terms", broken)
    draft = _draft(db_session, SAVED)
    response = _ai_edit(client, draft.id, instruction="Formuliere neu.", selected=UNSAVED_SENTENCE,
                        terms=["Brummelbeck"], editor_text=UNSAVED)
    assert response.status_code == 422 and writer.received_payloads == []
    db_session.refresh(draft)
    assert draft.content == SAVED


def test_compound_names_and_two_people_in_unsaved_text(client, db_session, writer) -> None:
    sentence = ("Anna von Berg und Bernd von Berg stritten; Frau von Berg zahlte 2.300,00 EUR an Dr. Hans-Peter Müller. "
                "Herr Müller und Zeuge Seán O’Brien bestätigten § 535 BGB.")
    draft = _draft(db_session, SAVED)
    response = _ai_edit(client, draft.id, instruction="Formuliere den markierten Absatz neu.", selected=sentence,
                        terms=["Anna von Berg", "Bernd von Berg", "Hans-Peter Müller", "O'Brien"],
                        editor_text=SAVED + "\n" + sentence)
    assert response.status_code == 200, response.json()
    sent = _sent(writer)
    for secret in ("Anna", "Bernd", "Berg", "Hans-Peter", "Müller", "Brien"):
        assert secret not in sent
    assert "2.300,00 EUR" in sent and "§ 535 BGB" in sent
    pair = re.search(r"(\[PERSON_\d+\]) und (\[PERSON_\d+\]) stritten", sent)
    assert pair and pair.group(1) != pair.group(2)


def test_reconstruction_restores_the_unsaved_selection_exactly(client, db_session, writer) -> None:
    sentence = "Frau von Berg zahlte 2.300,00 EUR an Dr. Hans-Peter Müller; Herr Müller quittierte am 03.11.2026."
    draft = _draft(db_session, SAVED)
    response = _ai_edit(client, draft.id, instruction="Formuliere neu.", selected=sentence,
                        terms=["von Berg", "Hans-Peter Müller"], editor_text=SAVED + "\n" + sentence)
    assert response.status_code == 200, response.json()
    content = response.json()["content"]
    assert sentence in content  # die Echo-Antwort enthaelt die Auswahl; zurueckgefuehrt exakt das Original
    assert "von Anna" not in content and "Dr. Dr." not in content and "Herr Dr." not in content


def test_unsaved_name_never_reaches_the_http_request(client, db_session, monkeypatch) -> None:
    from app.attorney_instructions.service import AttorneyInstructionService
    from app.drafting.editor_service import EditorService
    from app.main import app
    from tests.test_privacy_wire_payload import _provider, _Recorder
    from tests.test_web_chat import _working_drafting_service

    recorder = _Recorder()
    for key, value in (("ANTHROPIC_BASE_URL", recorder.url), ("LEXONO_QA_LOOPBACK_CLOUD_ENDPOINT", "1"),
                       ("HTTPS_PROXY", "http://127.0.0.1:9"), ("HTTP_PROXY", "http://127.0.0.1:9"),
                       ("NO_PROXY", "127.0.0.1,localhost")):
        monkeypatch.setenv(key, value)
    try:
        app.dependency_overrides[get_editor_service_for_ai_edit] = lambda: EditorService(
            AttorneyInstructionService(_working_drafting_service(_provider())))
        draft = _draft(db_session, SAVED)
        _ai_edit(client, draft.id, instruction="Formuliere den markierten Satz neu.", selected=UNSAVED_SENTENCE,
                 terms=["Brummelbeck"], editor_text=UNSAVED)
        assert len(recorder.requests) == 1
        decoded = json.dumps(json.loads(recorder.requests[0]["raw"].decode("utf-8")), ensure_ascii=False)
        assert "Brummelbeck" not in decoded and "Gespeicherter Entwurf" not in decoded
        assert "22.07.2026" in decoded
    finally:
        recorder.server.shutdown()
        recorder.server.server_close()
