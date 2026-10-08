"""Tests fuer app/privacy/api_logger.py.

Schwerpunkt: Logs duerfen NIEMALS personenbezogene Inhalte enthalten -
auch nicht ueber Umwege wie Security-Check-Gruende."""

from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.models import ApiCallLog
from app.models.base import Base
from app.privacy.api_logger import (
    ApiCallLogger,
    categorize_block_reasons,
    compute_anonymized_prompt_id,
    friendly_block_message,
)
from app.privacy.gateway_schema import ClaudeRequestPayload


@pytest.fixture()
def db_session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def test_categorize_block_reasons_never_contains_original_text() -> None:
    """Kernanforderung: die im Grund enthaltene PII darf NICHT in der
    Kategorie landen."""
    reasons = ["Möglicherweise nicht erkannte Namen/Entitäten gefunden: ['Peter Müller']"]

    category = categorize_block_reasons(reasons)

    assert category == "unrecognized_entity_suspected"
    assert "Peter" not in category
    assert "Müller" not in category


def test_categorize_multiple_reasons_combines_categories() -> None:
    reasons = [
        "Zweck 'analyze_full_file' ist nicht in der Allowlist",
        "Nach Pseudonymisierung weiterhin erkennbare Muster: ['email']",
    ]

    category = categorize_block_reasons(reasons)

    assert "purpose_not_allowed" in category
    assert "residual_pii_detected" in category


def test_categorize_unknown_reason_falls_back_to_generic_category() -> None:
    category = categorize_block_reasons(["Ein völlig neuer, unbekannter Grund."])
    assert category == "unknown_block_reason"


def test_categorize_empty_reasons_returns_none() -> None:
    assert categorize_block_reasons([]) is None


def test_anonymized_prompt_id_is_deterministic_and_short() -> None:
    payload = ClaudeRequestPayload(
        schreibauftrag="formulate_draft", anonymisierter_sachverhalt="Text"
    )

    id_1 = compute_anonymized_prompt_id(payload)
    id_2 = compute_anonymized_prompt_id(payload)

    assert id_1 == id_2
    assert len(id_1) == 16
    # Darf den Inhalt nicht im Klartext enthalten.
    assert "Text" not in id_1


def test_anonymized_prompt_id_differs_for_different_payloads() -> None:
    payload_a = ClaudeRequestPayload(
        schreibauftrag="formulate_draft", anonymisierter_sachverhalt="Text A"
    )
    payload_b = ClaudeRequestPayload(
        schreibauftrag="formulate_draft", anonymisierter_sachverhalt="Text B"
    )

    assert compute_anonymized_prompt_id(payload_a) != compute_anonymized_prompt_id(payload_b)


def test_log_success_persists_safe_fields_only(db_session: Session) -> None:
    logger = ApiCallLogger()
    payload = ClaudeRequestPayload(
        schreibauftrag="formulate_draft",
        anonymisierter_sachverhalt="Mandant [MANDANT_01] bittet um Hilfe.",
    )

    log_entry = logger.log_success(
        db_session,
        workflow_id="matter-123",
        model="claude-sonnet-5",
        purpose="formulate_draft",
        payload=payload,
    )

    assert log_entry.result_status == "success"
    assert log_entry.error_status is None
    assert log_entry.anonymized_prompt_id is not None
    persisted = db_session.query(ApiCallLog).all()
    assert len(persisted) == 1


def test_log_blocked_never_stores_raw_reasons(db_session: Session) -> None:
    logger = ApiCallLogger()

    log_entry = logger.log_blocked(
        db_session,
        workflow_id="matter-123",
        model="claude-sonnet-5",
        purpose="formulate_draft",
        reasons=["Möglicherweise nicht erkannte Namen/Entitäten gefunden: ['Peter Müller']"],
    )

    assert log_entry.result_status == "blocked"
    assert log_entry.error_status == "unrecognized_entity_suspected"
    assert log_entry.anonymized_prompt_id is None
    # Explizit sicherstellen: der Name landet NIRGENDS im DB-Eintrag.
    assert "Peter" not in (log_entry.error_status or "")
    assert "Müller" not in (log_entry.error_status or "")


def test_log_error_never_stores_exception_message(db_session: Session) -> None:
    logger = ApiCallLogger()

    log_entry = logger.log_error(
        db_session, workflow_id="matter-123", model="claude-sonnet-5", purpose="formulate_draft"
    )

    assert log_entry.result_status == "error"
    assert log_entry.error_status == "writing_provider_exception"


def test_api_call_log_model_has_no_free_text_content_field() -> None:
    """Architektonischer Schutztest: das Modell darf kein generisches
    Freitextfeld haben, in dem sich Inhalte verstecken könnten."""
    columns = {c.name for c in ApiCallLog.__table__.columns}
    forbidden_field_names = {"content", "text", "prompt", "response", "details", "message"}
    assert not (columns & forbidden_field_names)


# --- Technischer Fehler vs. Datenschutz-Blockierung (15.09.) ---


def test_technical_failure_is_not_reported_as_a_privacy_block() -> None:
    """ECHTER FUND (Chat-Intelligence-Forensik, 15.09.): ein technischer
    Fehlschlag der Textproduktion ("Interner Fehler bei der
    Textproduktion", siehe app/ai_providers/orchestrator.py und
    app/drafting/service.py) fiel in keine Kategorie und wurde dem Anwalt
    als "Die Anfrage wurde aus Datenschutzgründen blockiert." angezeigt.
    Das verschleiert den echten Fehler UND untergraebt das Vertrauen in
    die Datenschutzmeldungen."""
    message = friendly_block_message(["Interner Fehler bei der Textproduktion"])

    assert "technischen Gründen" in message
    assert "Datenschutzgründen blockiert" not in message


def test_real_privacy_block_message_is_unchanged() -> None:
    """Die Gegenprobe: eine echte Datenschutz-Blockierung muss weiterhin
    als solche erscheinen."""
    message = friendly_block_message(
        ["Im Text wurden weiterhin erkennbare Muster gefunden: ..."]
    )

    assert "erkennbare Muster" in message
    assert "technischen Gründen" not in message


def test_technical_error_message_still_never_contains_the_raw_reason() -> None:
    """Der Leak-Schutz gilt unveraendert auch fuer die neue Kategorie: die
    rohen Gruende koennen sensible Werte enthalten und duerfen nie in die
    Meldung geraten."""
    message = friendly_block_message(
        ["Interner Fehler bei der Textproduktion: Frau Müller, Musterweg 3"]
    )

    assert "Müller" not in message
    assert "Musterweg" not in message


# --- Original-Wert-Leck in der Claude-Antwort (17.09., UI-Live-Validierung) ---


def test_leaked_original_value_reason_gets_its_own_category_not_unknown() -> None:
    """ECHTER FUND (UI-Live-Validierung "Zusammenfassen"-Aktion in der
    laufenden App, 17.09., reproduziert per DraftingService-Direktaufruf
    mit echter Presidio-Pseudonymisierung + echtem lokalem LLM): der Grund
    aus `check_response_placeholder_integrity` (security_check.py) fuer
    einen im Antworttext wiedergefundenen Originalwert - der
    schwerwiegendste der drei Stufe-1-Befunde - matchte bisher KEINES der
    Muster und landete im nichtssagenden "unknown_block_reason"-Eimer,
    identisch zu jedem beliebigen unklassifizierten Fehler."""
    reasons = [
        "Urspruenglicher, nicht pseudonymisierter Wert fuer [MANDANT_01] im Text "
        "gefunden - moeglicher Datenschutzverstoss"
    ]

    category = categorize_block_reasons(reasons)

    assert category == "original_value_leaked"
    assert category != "unknown_block_reason"


def test_leaked_original_value_friendly_message_is_specific_and_pii_free() -> None:
    message = friendly_block_message(
        [
            "Urspruenglicher, nicht pseudonymisierter Wert fuer [MANDANT_01] im Text "
            "gefunden - moeglicher Datenschutzverstoss"
        ]
    )

    assert message != "Die Anfrage wurde aus Datenschutzgründen blockiert."


# --- Leere/abgeschnittene KI-Antwort (19.09., Schriftsatz-Generator-E2E) ---


def test_empty_writing_response_reason_gets_its_own_category_not_unknown() -> None:
    """ECHTER FUND (19.09., live am echten Server reproduziert, Owner-
    Direktive "CONTINUE AUTONOMOUS PRODUCT COMPLETION"): der neue
    Mindestinhalt-Check in app/drafting/service.py (leere/abgeschnittene
    KI-Antwort, z. B. durch das Token-Limit) matchte bisher KEINES der
    Muster und landete im nichtssagenden "unknown_block_reason"-Eimer -
    identisches Fehlerbild wie "technical_error"/"original_value_leaked"
    vorher, zeigte dem Anwalt faelschlich eine Datenschutz-Meldung."""
    reasons = [
        "Die KI hat keinen verwertbaren Text zurückgegeben (leere Antwort, "
        "möglicherweise durch das Token-Limit abgeschnitten) - Entwurf "
        "wurde nicht übernommen. Bitte erneut versuchen, ggf. mit "
        "kürzeren Anmerkungen/weniger Dokumenten."
    ]

    category = categorize_block_reasons(reasons)

    assert category == "empty_writing_response"
    assert category != "unknown_block_reason"

    message = friendly_block_message(reasons)
    assert message != "Die Anfrage wurde aus Datenschutzgründen blockiert."
    assert "Token-Limit" in message


# --- Stufe-2 (lokale LLM-Qualitätsprüfung) Fund != Datenschutzvorfall
# (05.10., Owner-Direktive "P1-BUGFIX: Schriftsatz unvollständig,
# Folgefragen blockiert, Datenschutzprüfung fehlerhaft" - mit dem real
# konfigurierten lokalen Modell reproduziert: hielt auf einem
# vollständigen, fehlerfreien Entwurf frei erfundene "Befunde" für echte
# Probleme). ---


def test_local_quality_check_reason_gets_its_own_category_not_unknown() -> None:
    """Der von app/drafting/service.py selbst kontrollierte Wortlaut (NICHT
    die vom lokalen Modell frei erfundenen `validation.issues` - deren
    Formulierung ist je Aufruf unterschiedlich und nicht zuverlässig genug
    für einen Mustervergleich, siehe dortiger Kommentar) muss zuverlässig
    erkannt werden."""
    reasons = [
        "Die lokale Qualitätsprüfung konnte die Antwort nicht eindeutig "
        "bestätigen - kein Datenschutzvorfall. Entwurf wurde "
        "sicherheitshalber nicht übernommen.",
        # Typisches, vom lokalen Modell frei erfundenes Issue - bewusst
        # NICHT das, was den Mustervergleich tragen soll.
        "consistent_placeholder_usage (high): The placeholders [KATEGORIE_XX] "
        "are not used consistently.",
    ]

    category = categorize_block_reasons(reasons)

    assert category == "local_quality_check_uncertain"
    assert category != "unknown_block_reason"


def test_local_quality_check_friendly_message_does_not_claim_a_privacy_violation() -> None:
    reasons = [
        "Die lokale Qualitätsprüfung konnte die Antwort nicht eindeutig "
        "bestätigen - kein Datenschutzvorfall. Entwurf wurde "
        "sicherheitshalber nicht übernommen.",
    ]

    message = friendly_block_message(reasons)

    assert message != "Die Anfrage wurde aus Datenschutzgründen blockiert."
    assert "kein Datenschutzvorfall" in message or "keine Datenschutzentscheidung" in message or "unsichere automatische" in message


def test_blocked_call_logs_category_and_placeholder_names_but_never_values() -> None:
    """Diagnose-Hardening (Real-E2E 08.10.): zwei sporadische Blockaden waren nachtraeglich
    nicht zuzuordnen. Geloggt werden nur Kategorie und Platzhaltername, nie Werte."""
    import logging

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.models.base import Base
    from app.privacy.api_logger import ApiCallLogger

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    reasons = [
        "Urspruenglicher, nicht pseudonymisierter Wert fuer [ORGANISATION_03] im Text gefunden - "
        "moeglicher Datenschutzverstoss",
        "Interne Notiz mit Max Mustermann",
    ]

    records: list[logging.LogRecord] = []

    class _Collect(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    logger = logging.getLogger("lexono.privacy")
    handler = _Collect(level=logging.WARNING)
    previous_level, previous_disabled = logger.level, logger.disabled
    logger.setLevel(logging.WARNING)
    logger.disabled = False
    logger.addHandler(handler)
    try:
        ApiCallLogger().log_blocked(
            session, workflow_id=None, model="m", purpose="chat_response", reasons=reasons
        )
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)
        logger.disabled = previous_disabled

    text = " ".join(r.getMessage() for r in records)
    assert "original_value_leaked" in text and "[ORGANISATION_03]" in text
    assert "Max Mustermann" not in text and "Urspruenglicher" not in text
    session.close()
    engine.dispose()
