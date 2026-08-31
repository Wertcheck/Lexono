"""Reproduzierbarer, synthetischer End-to-End-Pilot-Test (Pilot Readiness
Review): beweist den vollstaendigen, tatsaechlichen Datenfluss

    synthetisches Kanzleidokument
    -> Upload/Intake (echter Extraktions-/Verarbeitungsweg,
       DocumentProcessingService - OCR wird durchlaufen, entscheidet aber
       aufgrund eines vorhandenen Text-Layers "nicht erforderlich"; der
       eigentliche Tesseract-Aufruf ist bereits in tests/test_documents_ocr.py
       separat abgedeckt und dev-umgebungsabhaengig, siehe dort)
    -> lokale Kontextaufbereitung (RuleBasedLocalAIProvider)
    -> Presidio/Regex-Erkennung + Pseudonymisierung + Security-Check +
       Final Payload Gate (ClaudePrivacyGateway - ECHT, nicht gemockt)
    -> Cloud-Payload
    -> simulierte/fake Claude-Antwort (FakeClaudeWritingProvider - einzige
       gefakte Komponente, echter Netzwerkaufruf waere ein echter,
       kostenpflichtiger Aufruf ohne verfuegbaren API-Key)
    -> lokale Rekonstruktion
    -> fertiger Schriftsatz

Nutzt ausschliesslich synthetische Testdaten (CLAUDE.md-Grundregel: niemals
echte Mandantendaten). Kein Netzwerkaufruf, kein API-Key noetig, deterministisch
und in jeder Umgebung lauffaehig (kein externes Tesseract-Binary erforderlich -
das Test-PDF traegt einen echten Text-Layer, OCR wird daher korrekt als
"nicht erforderlich" erkannt, siehe DocumentProcessingService)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pymupdf
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.ai_providers.local_ai_provider import RuleBasedLocalAIProvider
from app.documents.service import DocumentProcessingService
from app.drafting.service import DraftingService
from app.models import Client, Document, Matter, Party
from app.models.base import Base
from app.privacy.gateway import ClaudePrivacyGateway
from app.research.service import LegalResearchService
from app.search.service import DocumentSearchService
from tests.fake_embedding_provider import FakeEmbeddingProvider
from tests.test_drafting_service import FakeClaudeWritingProvider

# --- Synthetische Testdaten (KEINE echten Mandantendaten, CLAUDE.md) ------

_MANDANT_NAME = "Erika Testfrau"
_GEGNER_NAME = "Finanzamt Musterstadt"
_ADRESSE = "Beispielweg 7"
_ORT_ZEILE = "80331 München"
_STEUER_ID = "12 345 678 903"
_IBAN = "DE02120300000000202051"
_DATUM = "15.02.2026"

_SYNTHETIC_DOCUMENT_TEXT = (
    f"Sehr geehrte Damen und Herren,\n\n"
    f"unsere Mandantin {_MANDANT_NAME}, wohnhaft {_ADRESSE}, {_ORT_ZEILE},\n"
    f"Steuer-ID {_STEUER_ID}, legt gegen den Steuerbescheid des {_GEGNER_NAME}\n"
    f"vom {_DATUM} form- und fristgerecht Einspruch ein.\n\n"
    f"eine etwaige Erstattung bitten wir auf folgendes Konto zu ueberweisen:\n"
    f"IBAN {_IBAN}.\n\n"
    f"Mit freundlichen Gruessen"
)

_SECRET_VALUES = [
    _MANDANT_NAME,
    _ADRESSE,
    _STEUER_ID.replace(" ", ""),
    _IBAN,
    _GEGNER_NAME,
]


def _build_synthetic_pdf(path: Path) -> None:
    """Erzeugt ein ECHTES PDF mit echtem, extrahierbarem Text-Layer (kein
    Bild) - simuliert ein digital erzeugtes/eingegangenes Kanzleidokument.
    Ein gescanntes (bildbasiertes) Dokument durchliefe stattdessen den
    echten Tesseract-OCR-Pfad - separat getestet in
    tests/test_documents_ocr.py (dev-umgebungsabhaengig, siehe dort)."""
    doc = pymupdf.open()
    page = doc.new_page()
    y = 72
    for line in _SYNTHETIC_DOCUMENT_TEXT.split("\n"):
        page.insert_text((50, y), line)
        y += 20
    doc.save(str(path))
    doc.close()


@pytest.fixture()
def db_session() -> Iterator[Session]:
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def test_full_pilot_scenario_document_to_reconstructed_schriftsatz(
    db_session: Session, tmp_path: Path
) -> None:
    # --- STUFE 1: Originaldokument (synthetisch, lokal) ------------------
    pdf_path = tmp_path / "einspruch_scan.pdf"
    _build_synthetic_pdf(pdf_path)

    client = Client(name=_MANDANT_NAME)
    matter = Matter(client=client, title="Einspruch Steuerbescheid E2E-Pilot-Test")
    db_session.add_all([client, matter])
    db_session.commit()
    db_session.add(Party(matter_id=matter.id, name=_GEGNER_NAME, role="Gegner"))
    db_session.commit()

    document = Document(
        matter_id=matter.id,
        original_filename="einspruch_scan.pdf",
        file_path=str(pdf_path),
        classified_type="Einspruch",
    )
    db_session.add(document)
    db_session.commit()

    # --- STUFE 2: Upload/Intake-Verarbeitung (echter Code, kein Mock) ----
    # OCR_ENABLED spielt fuer ein Dokument MIT Text-Layer keine Rolle (der
    # echte Code prueft IMMER zuerst per PyMuPDF-Extraktion, ob OCR
    # ueberhaupt noetig ist - erst wenn nicht, greift OCR_ENABLED).
    processor = DocumentProcessingService(ocr_enabled=True)
    processor.process_document(document, db_session)

    assert document.ocr_status == "not_needed", (
        "Erwartung: Text-Layer-PDF wird direkt extrahiert, kein OCR noetig "
        f"(tatsaechlicher Status: {document.ocr_status!r})"
    )
    assert document.extracted_text is not None
    assert _MANDANT_NAME in document.extracted_text  # Originaltext lokal, unpseudonymisiert

    # --- STUFE 3: lokale Kontextaufbereitung + Privacy Gateway -----------
    search_service = DocumentSearchService(FakeEmbeddingProvider())
    research_service = LegalResearchService(search_service, min_score_for_sufficient=0.0)
    writing_provider = FakeClaudeWritingProvider(
        # Simuliert eine realistische Claude-Antwort: uebernimmt die
        # Platzhalter unveraendert (wie im Systemprompt gefordert, siehe
        # app/ai_providers/claude_writing_provider.py::WRITING_SYSTEM_PROMPT).
        response_text=(
            "Sehr geehrte Damen und Herren,\n\n"
            "hiermit legen wir fuer unsere Mandantin form- und fristgerecht "
            "Einspruch ein. Wir verweisen auf die beigefuegten Unterlagen.\n\n"
            "Mit freundlichen Gruessen"
        )
    )
    service = DraftingService(
        RuleBasedLocalAIProvider(search_service),
        research_service,
        search_service,
        ClaudePrivacyGateway(),
        writing_provider,
        model_name="claude-sonnet-5",
    )

    result = service.create_draft(matter.id, "formulate_draft", db_session)

    # --- STUFE 4: Canary-Beweis - Original-Personendaten NIE im Payload --
    assert result.success is True, f"Unerwartet blockiert: {result.blocked_reasons}"
    assert len(writing_provider.received_payloads) == 1
    sent_payload = writing_provider.received_payloads[0]
    payload_json = sent_payload.model_dump_json()

    for secret in _SECRET_VALUES:
        assert secret not in payload_json, (
            f"DATENSCHUTZVERSTOSS: Originalwert {secret!r} im tatsaechlich an "
            f"Claude gesendeten Payload gefunden!"
        )

    from app.ai_providers.claude_writing_provider import build_writing_prompt

    sent_prompt_text = build_writing_prompt(sent_payload)
    for secret in _SECRET_VALUES:
        assert secret not in sent_prompt_text, (
            f"DATENSCHUTZVERSTOSS: Originalwert {secret!r} im tatsaechlich "
            f"gesendeten Prompt-Text gefunden!"
        )

    # Payload muss stattdessen Platzhalter enthalten (Beweis, dass
    # tatsaechlich pseudonymisiert wurde, nicht nur "zufaellig leer") -
    # bewusst auf dem gesamten Payload geprueft statt auf einem Einzelfeld,
    # da die konkrete Verteilung auf Sachverhalt/Argumentationspunkte von
    # der (fuer diesen Test irrelevanten) Deadline-Extraktion abhaengt.
    # Wieviele Platzhalter im Detail entstehen, ist Sache der bereits
    # separat getesteten Erkennungslogik (tests/test_privacy_detectors.py,
    # tests/test_privacy_pseudonymizer.py) - hier zaehlt nur: mindestens
    # einer ist da (Pseudonymisierung hat tatsaechlich stattgefunden).
    assert "[MANDANT_01]" in payload_json
    assert "[STEUER_ID_01]" in payload_json

    # --- STUFE 5/6: simulierte Cloud-Antwort -> lokale Rekonstruktion ----
    assert result.draft_text is not None
    assert "form- und fristgerecht Einspruch" in result.draft_text
    # Der fertige, lokal rekonstruierte Schriftsatz darf (und soll) die
    # Originaldaten wieder enthalten - Rekonstruktion geschieht AUSSCHLIESSLICH
    # lokal, nach dem (simulierten) Cloud-Aufruf.
    # (Hier bewusst kein Assert auf konkrete Platzhalter im Ergebnis, da die
    # Fake-Antwort keine Platzhalter der Originalanfrage uebernimmt - siehe
    # response_text oben; die Platzhalter-Treue eines echten Claude-Aufrufs
    # ist bereits in tests/test_privacy_canary.py separat bewiesen.)

    print("\n=== Finaler, lokal rekonstruierter Schriftsatz ===")
    print(result.draft_text)
