"""Tests für app/web/drafts_router.py (Prompt 23).

Gleiches Testmuster wie tests/test_web_inbox.py (In-Memory-SQLite über
app.dependency_overrides, StaticPool). Für die "Änderungen übernehmen &
neu formulieren"-Aktion wird `get_attorney_instruction_service` in
app.web.drafts_router direkt gemonkeypatcht, da dieser Aufruf bewusst
NICHT über FastAPIs Depends() läuft (siehe Begründung im Router-Modul-
Docstring: ermöglicht, WritingProviderNotConfiguredError im Routenkörper
selbst abzufangen und eine freundliche Meldung statt eines 500ers zu
zeigen).
"""

from __future__ import annotations

import re
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.web.drafts_router as drafts_router_module
from app.ai_providers.claude_writing_provider import ClaudeWritingResult
from app.ai_providers.local_ai_provider import RuleBasedLocalAIProvider
from app.attorney_instructions.service import AttorneyInstructionService
from app.db.session import get_db
from app.drafting.service import DraftingService
from app.main import app
from app.models import AttorneyInstruction, Client, Draft, FirmProfile, Matter
from app.models.base import Base
from app.privacy.gateway import ClaudePrivacyGateway
from app.privacy.gateway_schema import ClaudeRequestPayload
from app.research.service import LegalResearchService
from app.search.service import DocumentSearchService
from app.web.service_factory import (
    WritingProviderNotConfiguredError,
    get_attorney_instruction_service_for_saving_only,
)
from tests.auth_test_utils import extract_csrf, login_as_admin
from tests.fake_embedding_provider import FakeEmbeddingProvider


class FakeClaudeWritingProvider:
    def __init__(self, response_text: str = "Neu formulierte Antwort.") -> None:
        self.response_text = response_text

    def write(self, payload: ClaudeRequestPayload) -> ClaudeWritingResult:
        return ClaudeWritingResult(text=self.response_text, token_count=10)


@pytest.fixture()
def db_session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture()
def client(db_session: Session) -> Iterator[TestClient]:
    def _override_get_db() -> Iterator[Session]:
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    # "Anmerkung speichern" braucht keinen echten DraftingService - die
    # Standard-Factory reicht (baut ohnehin drafting_service=None).
    app.dependency_overrides[get_attorney_instruction_service_for_saving_only] = (
        lambda: AttorneyInstructionService(drafting_service=None)
    )
    try:
        test_client = TestClient(app)
        login_as_admin(db_session, test_client)
        yield test_client
    finally:
        app.dependency_overrides.clear()


@pytest.fixture()
def seeded(db_session: Session) -> dict[str, str]:
    client_ = Client(name="Synthetischer Testmandant GmbH")
    matter = Matter(client=client_, title="Einspruch Steuerbescheid 2025")
    db_session.add_all([client_, matter])
    db_session.commit()
    draft = Draft(matter_id=matter.id, content="Ursprünglicher Entwurfstext.")
    db_session.add(draft)
    db_session.commit()
    return {"matter_id": matter.id, "draft_id": draft.id}


def _working_attorney_instruction_service(db_session: Session) -> AttorneyInstructionService:
    """Baut einen funktionsfähigen Service mit einem Fake-Writing-Provider
    (kein echter Claude-API-Aufruf, kein echter Embedding-Modell-
    Download)."""
    search_service = DocumentSearchService(FakeEmbeddingProvider())
    research_service = LegalResearchService(search_service, min_score_for_sufficient=0.0)
    drafting_service = DraftingService(
        RuleBasedLocalAIProvider(search_service),
        research_service,
        search_service,
        ClaudePrivacyGateway(),
        FakeClaudeWritingProvider(),
        model_name="claude-sonnet-5",
    )
    return AttorneyInstructionService(drafting_service)


# --- Briefkopf-/Signatur-Vorschau im Editor (20.09., Owner-Direktive
# "CONTEXT EXTENSION" §5/§6 - der zuvor als "decision-dependent"
# eingeordnete Editor-Ausbau) ---


def test_draft_detail_shows_empty_state_hint_without_firm_profile(
    client: TestClient, seeded: dict
) -> None:
    """Ohne jedes Kanzleiprofil (aktueller echter Produktionsstand) zeigt
    die Entwurfsseite einen ehrlichen Hinweis statt eines leeren/kaputten
    Briefkopf-Bereichs, mit Link zu den Einstellungen."""
    response = client.get(f"/dashboard/drafts/{seeded['draft_id']}")
    assert response.status_code == 200
    assert "Kein Kanzleiprofil hinterlegt" in response.text
    assert '/dashboard/settings?tab=kanzlei"' in response.text
    assert 'class="document-page__letterhead"' not in response.text
    assert 'class="document-page__signature"' not in response.text


def test_draft_detail_shows_letterhead_preview_with_firm_name(
    db_session: Session, client: TestClient, seeded: dict
) -> None:
    """Mit hinterlegtem Kanzleinamen erscheint der echte Briefkopf-Bereich
    (dieselben Bausteine wie im PDF-/DOCX-Export, siehe
    app/export/letterhead.py) statt der reinen Text-Box."""
    db_session.add(
        FirmProfile(
            firm_name="Kanzlei Musterfrau",
            street="Beispielstraße 1",
            postal_code="80331",
            city="München",
        )
    )
    db_session.commit()

    response = client.get(f"/dashboard/drafts/{seeded['draft_id']}")
    assert response.status_code == 200
    assert 'class="document-page__letterhead"' in response.text
    assert "Kanzlei Musterfrau" in response.text
    assert "Beispielstraße 1, 80331 München" in response.text


def test_draft_detail_export_links_default_to_pdf_first(
    client: TestClient, seeded: dict
) -> None:
    """06.10., Owner-Direktive "SETTINGS -> KANZLEI FINAL UI/UX":
    `FirmProfile.default_document_format` hat Server-Default "pdf" -
    jede bestehende/neue Installation ohne explizite Aenderung muss
    daher weiterhin exakt das bisherige Verhalten zeigen (PDF vor
    DOCX, 18.09.-Referenzabgleich)."""
    response = client.get(f"/dashboard/drafts/{seeded['draft_id']}")
    assert response.status_code == 200
    pdf_index = response.text.index("Als PDF exportieren")
    docx_index = response.text.index("Als DOCX exportieren")
    assert pdf_index < docx_index


def test_draft_detail_export_links_follow_docx_default_when_configured(
    db_session: Session, client: TestClient, seeded: dict
) -> None:
    db_session.add(FirmProfile(firm_name="Kanzlei Musterfrau", default_document_format="docx"))
    db_session.commit()

    response = client.get(f"/dashboard/drafts/{seeded['draft_id']}")
    assert response.status_code == 200
    pdf_index = response.text.index("Als PDF exportieren")
    docx_index = response.text.index("Als DOCX exportieren")
    assert docx_index < pdf_index
    assert "Kein Kanzleiprofil hinterlegt" not in response.text


def test_draft_detail_shows_signature_block_with_signatory_name(
    db_session: Session, client: TestClient, seeded: dict
) -> None:
    """Mit hinterlegtem Unterzeichner-Namen erscheint der Signatur-Bereich
    unter dem Entwurfstext - auch ohne Kanzleiname/Briefkopf (unabhängige
    Bedingungen, siehe has_letterhead_content/has_signature_content in
    app/export/letterhead.py)."""
    db_session.add(FirmProfile(firm_name="", signatory_name="Rechtsanwältin Anna Muster"))
    db_session.commit()

    response = client.get(f"/dashboard/drafts/{seeded['draft_id']}")
    assert response.status_code == 200
    assert 'class="document-page__signature"' in response.text
    assert "Rechtsanwältin Anna Muster" in response.text
    assert 'class="document-page__letterhead"' not in response.text


# --- GET Entwurfsansicht ---


def test_draft_detail_page_returns_200(client: TestClient, seeded: dict) -> None:
    response = client.get(f"/dashboard/drafts/{seeded['draft_id']}")
    assert response.status_code == 200
    assert "Ursprünglicher Entwurfstext" in response.text
    # "Anwaltliche Anmerkungen"-Panel wurde 20.08. durch die feste
    # Anweisungs-Leiste (instruction-bar) direkt unter dem Entwurfstext
    # ersetzt - siehe test_draft_detail_page_shows_instruction_bar.
    assert "Änderungsauftrag an die KI" in response.text


def test_draft_detail_page_shows_version_one_as_only_chip(
    client: TestClient, seeded: dict
) -> None:
    response = client.get(f"/dashboard/drafts/{seeded['draft_id']}")
    # AKTUALISIERT 14.09.: die Versions-Chips zeigten bisher den ROHEN
    # internen Status ("v1 · draft"). Interne Statusbezeichner gehoeren
    # nicht in die Produktoberflaeche (UI-Direktive §11) - der Chip nutzt
    # jetzt dieselbe Beschriftung wie der Rest des Produkts.
    assert "v1 · Entwurf" in response.text
    assert "v2 · draft" not in response.text


def test_draft_detail_page_shows_status_pill_and_last_saved_timestamp(
    client: TestClient, seeded: dict
) -> None:
    """UI/UX-Ueberarbeitung, Phase 5 (13.09.): der Entwurf-Header wirkt
    jetzt wie bei einem Dokument-Editor (Statuspille + "Zuletzt
    gespeichert"-Zeitstempel statt reinem "Status: draft"-Text)."""
    response = client.get(f"/dashboard/drafts/{seeded['draft_id']}")
    # AKTUALISIERT 14.09.: die Statuspille kommt jetzt aus dem gemeinsamen
    # Makro `_labels.html::draft_status_tag` (vorher dreimal dieselbe
    # Inline-Map, wovon ZWEI Stellen den rohen internen Wert "draft"/
    # "approved" zeigten). Sie enthaelt zusaetzlich den Farbpunkt, der im
    # restlichen Produkt ebenfalls verwendet wird - die geprueften
    # Garantien (richtige Beschriftung + richtige Pillen-Klasse) bleiben
    # unveraendert, nur nicht mehr an exaktes Innen-Markup gebunden.
    assert re.search(r'class="tag tag--unmatched"[^>]*>.*?Entwurf', response.text, re.S)
    assert "Zuletzt gespeichert:" in response.text
    assert "Status: <strong>draft</strong>" not in response.text


def test_draft_detail_page_shows_instruction_bar_with_mic_button(
    client: TestClient, seeded: dict
) -> None:
    """20.08.: feste Anweisungs-Leiste mit Diktier-Button (Web Speech API)
    direkt unter dem Entwurfstext - postet weiterhin an dieselben,
    bestehenden Endpunkte wie zuvor das Sidebar-Panel (kein Router-Umbau
    nötig, siehe app/web/drafts_router.py)."""
    response = client.get(f"/dashboard/drafts/{seeded['draft_id']}")
    assert response.status_code == 200
    assert 'id="instruction-mic-btn"' in response.text
    assert 'id="instruction-text"' in response.text
    assert f'/dashboard/drafts/{seeded["draft_id"]}/instructions"' in response.text


def test_draft_detail_instruction_form_has_ai_loading_wiring(
    client: TestClient, seeded: dict
) -> None:
    """KI-Waiting-/Buffering-UX (20.09., Owner-Direktive "KI-WAITING-/
    BUFFERING-UX PROJEKTWEIT PRÜFEN UND VERBESSERN"): "Änderungen
    übernehmen & neu formulieren" löst einen echten, synchronen,
    kostenpflichtigen Claude-Aufruf VOR dem Redirect aus (siehe
    app/web/drafts_router.py::save_and_apply_instruction) - es gab bisher
    KEIN Feedback zwischen Klick und fertigem Ergebnis. Prüft nur die
    Markup-Verdrahtung (js-ai-form/data-ai-loading-label) - das eigentliche
    Verhalten liegt in app_ai_loading.js (kein JS-Test-Runner in diesem
    Projekt, siehe reale GUI-Verifikation in OPEN_ISSUES.md)."""
    response = client.get(f"/dashboard/drafts/{seeded['draft_id']}")
    assert response.status_code == 200
    assert 'id="instruction-form" class="instruction-bar__form js-ai-form"' in response.text
    assert 'data-ai-loading-label="Wird neu formuliert' in response.text


def test_draft_detail_regenerate_form_has_ai_loading_wiring(
    client: TestClient, seeded: dict
) -> None:
    """Derselbe Fund/dieselbe Behebung wie oben, fuer "Neu generieren"
    (app/web/drafts_router.py::regenerate_draft - ebenfalls ein echter,
    synchroner Claude-Aufruf vor dem Redirect)."""
    response = client.get(f"/dashboard/drafts/{seeded['draft_id']}")
    assert response.status_code == 200
    assert f'action="/dashboard/drafts/{seeded["draft_id"]}/regenerate" class="js-ai-form"' in response.text
    assert 'data-ai-loading-label="Wird neu generiert' in response.text


def test_draft_detail_mic_button_does_not_use_cloud_speech_recognition(
    client: TestClient, seeded: dict
) -> None:
    """PRIVACY-KORREKTUR (15.09.): dieser Button rief bis dahin die native
    Browser-`SpeechRecognition` auf. In Chromium/WebView2 gibt es dafuer
    KEIN On-Device-Modell - das Audio wird an einen Cloud-Dienst gesendet,
    BEVOR die lokale Pseudonymisierung greifen kann. Ein Anwalt, der hier
    "fuege hinzu, dass Herr Mueller die Frist bestreitet" diktiert (der
    kanonische Regressionsfall dieses Projekts), haette den Mandantennamen
    unpseudonymisiert an einen externen Dienst geschickt.

    `chat.html` hatte diese Erkenntnis bereits gezogen; diese Anweisungs-
    Leiste war es nicht. Jetzt derselbe ehrliche "in Vorbereitung"-Zustand
    in BEIDEN Composern, bis eine echte lokale Spracherkennung angebunden
    ist (siehe .agentic/OPEN_ISSUES.md, STT-Evaluation)."""
    response = client.get(f"/dashboard/drafts/{seeded['draft_id']}")
    assert response.status_code == 200
    # Die tatsaechliche API-INSTANZIIERUNG/-Nutzung darf nicht mehr
    # vorkommen - der Name selbst darf (wie in chat.html) weiterhin in
    # einem erklaerenden Kommentar auftauchen, WARUM bewusst nicht
    # angebunden wurde.
    assert "new SpeechRecognitionImpl(" not in response.text
    assert "recognition.start()" not in response.text
    assert "recognition.continuous" not in response.text
    assert "in Vorbereitung" in response.text
    assert f'/dashboard/drafts/{seeded["draft_id"]}/instructions/apply"' in response.text


# --- Standard-Prompts im Entwurf-Editor (16.09., UI/UX-Sweep - Referenz-
# bilder 12/17/24/31/38/41 zeigen eine Standard-Prompts-Liste neben der
# KI-Anweisung; dieselbe, bereits bestehende und in chat.html schon
# produktiv genutzte Vorlagenbibliothek, nur um einen zweiten Einstiegs-
# punkt ergaenzt - reines Vorausfuellen, KEINE neue Anbindung an die
# Drafting-Pipeline) ---


def test_draft_detail_shows_no_standard_prompts_section_when_library_is_empty(
    client: TestClient, seeded: dict
) -> None:
    """Keine leere/nutzlose Sektion, wenn noch keine Vorlage existiert -
    dieselbe Zurueckhaltung wie bei den anderen bedingten Panels dieser
    Seite (Quellen, Anmerkungen, ...)."""
    response = client.get(f"/dashboard/drafts/{seeded['draft_id']}")
    assert response.status_code == 200
    assert "Standard-Prompts" not in response.text


def test_draft_detail_shows_existing_prompt_templates_as_prefill_chips(
    client: TestClient, db_session: Session, seeded: dict
) -> None:
    from app.models import PromptTemplate

    template = PromptTemplate(
        name="Fristverlängerung beantragen",
        description="Bittet um eine Fristverlängerung.",
        content="Bitte formuliere einen Antrag auf Fristverlängerung um zwei Wochen.",
    )
    db_session.add(template)
    db_session.commit()

    response = client.get(f"/dashboard/drafts/{seeded['draft_id']}")

    assert response.status_code == 200
    assert "Standard-Prompts" in response.text
    assert "Fristverlängerung beantragen" in response.text
    assert 'class="draft-prompt-row"' in response.text
    assert "Bitte formuliere einen Antrag auf Fristverlängerung um zwei Wochen." in response.text
    # Derselbe, bereits bestehende Verwaltungs-Einstiegspunkt wie im Chat.
    assert 'href="/dashboard/library/prompts"' in response.text


def test_draft_detail_prompt_chips_only_prefill_never_auto_submit(
    client: TestClient, db_session: Session, seeded: dict
) -> None:
    """Sicherheitsrelevante Abgrenzung (siehe PromptTemplate-Moduldocstring):
    die Vorlage darf NIEMALS automatisch an die Drafting-Pipeline gehen -
    nur ins vom Anwalt sichtbare, weiterhin manuell abzusendende
    Anweisungsfeld."""
    from app.models import PromptTemplate

    template = PromptTemplate(name="Kurzvorlage", content="Kurzer Vorlagentext.")
    db_session.add(template)
    db_session.commit()

    response = client.get(f"/dashboard/drafts/{seeded['draft_id']}")

    assert response.status_code == 200
    # Der Chip ist ein reiner Client-Button (type="button"), kein eigenes
    # Formular/kein "submit" - das Absenden bleibt ausschliesslich ueber
    # die bestehenden "Anmerkung speichern"/"Änderungen übernehmen"-Buttons
    # im instruction-bar__form moeglich.
    assert 'type="button" class="draft-prompt-row"' in response.text


# --- "Vorschläge"-Schnellaktionen im Entwurf-Editor (24.09., Owner-
# Direktive "PRODUCT COMPLETION MODE" §5/§6 - Referenzbilder 12/24/38
# zeigen im KI-Assistenten vier feste Verfeinerungs-Schnellaktionen,
# getrennt von den bereits bestehenden, variablen Standard-Prompts).
# Dasselbe bereits etablierte Vorausfuell-Muster - kein zweites System. ---


def test_draft_detail_always_shows_the_four_fixed_vorschlaege_regardless_of_prompt_library(
    client: TestClient, seeded: dict
) -> None:
    """Anders als die Standard-Prompts (nur sichtbar, wenn die Kanzlei
    bereits Vorlagen angelegt hat) sind die vier "Vorschläge" fest und
    immer vorhanden - sie haengen an keiner Kanzlei-spezifischen Daten."""
    response = client.get(f"/dashboard/drafts/{seeded['draft_id']}")

    assert response.status_code == 200
    assert "Vorschläge" in response.text
    assert 'class="draft-suggestion"' in response.text
    for label in ("Formulierung präzisieren", "Text kürzen", "Rechtliche Prüfung", "Ton anpassen"):
        assert label in response.text


def test_draft_detail_vorschlaege_prefill_the_existing_instruction_field_only(
    client: TestClient, seeded: dict
) -> None:
    """Dieselbe sicherheitsrelevante Abgrenzung wie bei den Standard-
    Prompts: reines Vorausfuellen, kein automatischer KI-Aufruf."""
    response = client.get(f"/dashboard/drafts/{seeded['draft_id']}")

    assert response.status_code == 200
    assert 'data-prefill="Bitte formuliere den Text klarer und rechtssicherer."' in response.text
    assert 'data-prefill="Bitte kürze den Text auf die wesentlichen Aussagen."' in response.text
    assert (
        'data-prefill="Bitte prüfe den Text auf rechtliche Risiken und weise auf mögliche Probleme hin."'
        in response.text
    )
    assert 'data-prefill="Bitte formuliere den Text sachlicher und formeller."' in response.text


def test_draft_not_found_returns_404(client: TestClient) -> None:
    response = client.get("/dashboard/drafts/does-not-exist")
    assert response.status_code == 404


def test_draft_detail_page_shows_error_banner_from_query_param(
    client: TestClient, seeded: dict
) -> None:
    response = client.get(
        f"/dashboard/drafts/{seeded['draft_id']}", params={"error": "Testfehler"}
    )
    assert "Testfehler" in response.text
    assert "banner--error" in response.text


def test_draft_detail_page_has_no_error_banner_by_default(
    client: TestClient, seeded: dict
) -> None:
    response = client.get(f"/dashboard/drafts/{seeded['draft_id']}")
    assert "banner--error" not in response.text


# --- Manuelle Bearbeitung ---


def test_manual_edit_creates_new_version_and_redirects(
    client: TestClient, db_session: Session, seeded: dict
) -> None:
    csrf = extract_csrf(client.get(f"/dashboard/drafts/{seeded['draft_id']}").text)
    response = client.post(
        f"/dashboard/drafts/{seeded['draft_id']}/manual-edit",
        data={"content": "Bearbeiteter Text.", "csrf_token": csrf},
        follow_redirects=False,
    )
    assert response.status_code == 303
    new_location = response.headers["location"]
    assert new_location != f"/dashboard/drafts/{seeded['draft_id']}"

    new_draft_id = new_location.rsplit("/", 1)[-1]
    new_draft = db_session.get(Draft, new_draft_id)
    assert new_draft.content == "Bearbeiteter Text."
    assert new_draft.version == 2
    assert new_draft.previous_version_id == seeded["draft_id"]

    # Original unveraendert.
    db_session.expire_all()
    original = db_session.get(Draft, seeded["draft_id"])
    assert original.content == "Ursprünglicher Entwurfstext."
    assert original.version == 1


def test_manual_edit_redirect_target_shows_two_version_chips(
    client: TestClient, seeded: dict
) -> None:
    csrf = extract_csrf(client.get(f"/dashboard/drafts/{seeded['draft_id']}").text)
    response = client.post(
        f"/dashboard/drafts/{seeded['draft_id']}/manual-edit",
        data={"content": "Bearbeiteter Text.", "csrf_token": csrf},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "v1" in response.text
    assert "v2" in response.text
    assert "Bearbeiteter Text." in response.text


# --- Anmerkung speichern (ohne Neugenerierung) ---


def test_save_instruction_works_without_configured_api_key(
    client: TestClient, db_session: Session, seeded: dict
) -> None:
    """Kernanforderung/Regressionstest: 'Anmerkung speichern' darf NICHT
    daran scheitern, dass kein Claude-API-Key konfiguriert ist."""
    csrf = extract_csrf(client.get(f"/dashboard/drafts/{seeded['draft_id']}").text)
    response = client.post(
        f"/dashboard/drafts/{seeded['draft_id']}/instructions",
        data={"instruction_text": "Auf Punkt 3 eingehen.", "csrf_token": csrf},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == f"/dashboard/drafts/{seeded['draft_id']}"

    instructions = (
        db_session.query(AttorneyInstruction)
        .filter_by(draft_id=seeded["draft_id"])
        .all()
    )
    assert len(instructions) == 1
    assert instructions[0].status == "open"
    assert instructions[0].instruction_text == "Auf Punkt 3 eingehen."


def test_save_instruction_does_not_create_new_draft_version(
    client: TestClient, db_session: Session, seeded: dict
) -> None:
    csrf = extract_csrf(client.get(f"/dashboard/drafts/{seeded['draft_id']}").text)
    client.post(
        f"/dashboard/drafts/{seeded['draft_id']}/instructions",
        data={"instruction_text": "Testanmerkung.", "csrf_token": csrf},
    )
    assert db_session.query(Draft).count() == 1


def test_saved_instruction_appears_on_draft_page(
    client: TestClient, seeded: dict
) -> None:
    csrf = extract_csrf(client.get(f"/dashboard/drafts/{seeded['draft_id']}").text)
    client.post(
        f"/dashboard/drafts/{seeded['draft_id']}/instructions",
        data={"instruction_text": "Ton bestimmter formulieren.", "csrf_token": csrf},
    )
    response = client.get(f"/dashboard/drafts/{seeded['draft_id']}")
    assert "Ton bestimmter formulieren." in response.text
    assert "admin@kanzlei.test" in response.text


# --- Änderungen übernehmen & neu formulieren (mit Fake-Provider) ---


def test_apply_instruction_via_web_creates_new_version(
    client: TestClient, db_session: Session, seeded: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = _working_attorney_instruction_service(db_session)
    monkeypatch.setattr(
        drafts_router_module, "get_attorney_instruction_service", lambda: service
    )

    csrf = extract_csrf(client.get(f"/dashboard/drafts/{seeded['draft_id']}").text)
    response = client.post(
        f"/dashboard/drafts/{seeded['draft_id']}/instructions/apply",
        data={
            "instruction_text": "Schadensersatzhöhe nicht anerkennen.",
            "csrf_token": csrf,
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    new_location = response.headers["location"]
    assert new_location != f"/dashboard/drafts/{seeded['draft_id']}"
    new_draft_id = new_location.rsplit("/", 1)[-1]
    new_draft = db_session.get(Draft, new_draft_id)
    # 05.10., Owner-Direktive "LONG-RUN PRODUCT QUALITY PASS" Phase D:
    # KI-generierter Inhalt wird jetzt zu Editor-HTML gewandelt (siehe
    # app/drafting/markdown_to_draft_html.py) statt roh gespeichert.
    assert new_draft.content == "<p>Neu formulierte Antwort.</p>"
    assert new_draft.content_format == "html"
    assert new_draft.previous_version_id == seeded["draft_id"]

    instructions = db_session.query(AttorneyInstruction).all()
    assert len(instructions) == 1
    assert instructions[0].status == "applied"
    assert instructions[0].resulting_draft_id == new_draft_id


def test_apply_instruction_without_api_key_shows_friendly_error(
    client: TestClient, seeded: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Kein 500er, wenn kein Claude-API-Key konfiguriert ist - stattdessen
    Redirect mit Fehlermeldung im Query-Parameter."""

    def _raise() -> AttorneyInstructionService:
        raise WritingProviderNotConfiguredError("ANTHROPIC_API_KEY ist nicht konfiguriert")

    monkeypatch.setattr(drafts_router_module, "get_attorney_instruction_service", _raise)

    csrf = extract_csrf(client.get(f"/dashboard/drafts/{seeded['draft_id']}").text)
    response = client.post(
        f"/dashboard/drafts/{seeded['draft_id']}/instructions/apply",
        data={"instruction_text": "Testanmerkung.", "csrf_token": csrf},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert seeded["draft_id"] in response.headers["location"]
    assert "error=" in response.headers["location"]


def test_apply_instruction_without_api_key_creates_no_new_draft(
    client: TestClient, db_session: Session, seeded: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _raise() -> AttorneyInstructionService:
        raise WritingProviderNotConfiguredError("nicht konfiguriert")

    monkeypatch.setattr(drafts_router_module, "get_attorney_instruction_service", _raise)

    csrf = extract_csrf(client.get(f"/dashboard/drafts/{seeded['draft_id']}").text)
    client.post(
        f"/dashboard/drafts/{seeded['draft_id']}/instructions/apply",
        data={"instruction_text": "Testanmerkung.", "csrf_token": csrf},
    )

    assert db_session.query(Draft).count() == 1
    # Auch keine AttorneyInstruction, da der Fehler bereits VOR
    # create_instruction auftritt (Service konnte gar nicht gebaut werden).
    assert db_session.query(AttorneyInstruction).count() == 0


# --- Gold-Workflow-Abschluss: Akte, Postausgang, Mandant (14.09.) ---


def test_draft_detail_links_back_to_the_matter_instead_of_showing_a_raw_uuid(
    client: TestClient, seeded: dict, db_session: Session
) -> None:
    """Der Gold-Workflow endet in der Akte ("... -> Schreiben -> Speichern
    -> Akte"). Die Entwurfsseite zeigte dort aber die ROHE Akten-UUID als
    toten Text - weder lesbar noch benutzbar."""
    response = client.get(f"/dashboard/drafts/{seeded['draft_id']}")

    assert response.status_code == 200
    assert f'href="/dashboard/matters/{seeded["matter_id"]}"' in response.text
    assert "Einspruch Steuerbescheid 2025" in response.text
    assert "Synthetischer Testmandant GmbH" in response.text


def test_draft_detail_does_not_claim_the_outbox_is_missing(
    client: TestClient, seeded: dict
) -> None:
    """ECHTER FUND (14.09.): die Seite behauptete im Fliesstext, ein
    "tatsaechlicher Postausgang mit Versandfunktion" existiere noch nicht.
    Seit Prompt 25 ist das falsch - `approve_draft` legt ueber
    `OutboxService.add_to_outbox` wirklich einen Eintrag an. Eine
    Oberflaeche, die dem Anwalt sagt, sein freigegebener Entwurf sei
    nirgends gelandet, ist schlimmer als gar kein Hinweis."""
    response = client.get(f"/dashboard/drafts/{seeded['draft_id']}")

    assert "existiert noch nicht" not in response.text
    assert '/dashboard/outbox' in response.text


def test_draft_detail_keeps_the_no_automatic_sending_guarantee(
    client: TestClient, seeded: dict
) -> None:
    """Die Korrektur oben darf die Garantie NICHT mitentfernen, die
    tatsaechlich gilt (CLAUDE.md: keine automatische externe
    Kommunikation ohne Freigabe)."""
    response = client.get(f"/dashboard/drafts/{seeded['draft_id']}")

    assert "automatisch" in response.text
    assert "Warteschlange ohne Versandfunktion" in response.text


def test_draft_detail_shows_the_real_outbox_entry_after_approval(
    client: TestClient, seeded: dict, db_session: Session
) -> None:
    """Nach der Freigabe muss die Seite den TATSAECHLICHEN Verbleib des
    Entwurfs zeigen, nicht nur eine allgemeine Erklaerung."""
    from app.models import OutboxEntry

    page = client.get(f"/dashboard/drafts/{seeded['draft_id']}")
    token = extract_csrf(page.text)
    response = client.post(
        f"/dashboard/drafts/{seeded['draft_id']}/approve",
        data={"csrf_token": token},
        follow_redirects=True,
    )

    assert response.status_code == 200
    entry = db_session.query(OutboxEntry).filter_by(draft_id=seeded["draft_id"]).one()
    assert entry.status == "pending"
    # Interner Statuswert darf NICHT roh in der Oberflaeche stehen.
    assert "wartet auf Versand" in response.text
    assert ">pending<" not in response.text


def test_drafts_list_shows_the_client_and_a_real_link(
    client: TestClient, seeded: dict
) -> None:
    """Eine Freigabeliste ohne Mandantennamen zwingt den Anwalt, jede Zeile
    einzeln zu oeffnen, nur um zu sehen, um WEN es geht. Und ein reines
    `onclick` auf dem <tr> ist per Tastatur nicht erreichbar."""
    response = client.get("/dashboard/drafts")

    assert response.status_code == 200
    assert "Synthetischer Testmandant GmbH" in response.text
    assert f'href="/dashboard/drafts/{seeded["draft_id"]}"' in response.text


def test_drafts_list_excludes_chat_reference_drafts(
    client: TestClient, db_session: Session, seeded: dict
) -> None:
    """05.10., Owner-Direktive "ARCHITECTURE & PRODUCT FLOW PASS" §11/§12 -
    ein `status="chat_reference"`-Draft (normale Chat-Antwort ohne
    Schriftsatz-Intent, siehe app/drafting/service.py::_persist_draft) ist
    kein freigabepflichtiger Schriftsatz und darf in dieser Liste nicht
    auftauchen, auch nicht ohne expliziten `?status=`-Filter."""
    chat_draft = Draft(
        matter_id=seeded["matter_id"], content="<p>Chat-Antwort.</p>",
        status="chat_reference", content_format="html",
    )
    db_session.add(chat_draft)
    db_session.commit()

    response = client.get("/dashboard/drafts")

    assert response.status_code == 200
    assert f'href="/dashboard/drafts/{chat_draft.id}"' not in response.text
    # Der echte Schriftsatz aus der seeded-Fixture bleibt unveraendert sichtbar.
    assert f'href="/dashboard/drafts/{seeded["draft_id"]}"' in response.text
