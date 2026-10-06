"""Tests für den periodischen Hintergrund-E-Mail-Abruf (app/main.py::
`_run_periodic_mail_ingestion`, 14.09.).

ECHTER FUND, den dieser Task schließt: `MailIngestionService` (E-Mail-
Abruf) UND `MatterAssignmentService` (automatische Aktenzuordnung) waren
beide vollständig implementiert und getestet (siehe test_mail_service.py/
test_matching_*.py), wurden aber an KEINER Stelle der laufenden Anwendung
je aufgerufen - ein konfiguriertes Postfach hatte nie eine Wirkung.

Nutzt DIESELBEN echten Services (`MailIngestionService`,
`MatterMatchingService`, `MatterAssignmentService`) gegen eine echte
In-Memory-Test-DB - NUR der `MailProvider` (die einzige echte
Netzwerkgrenze) ist ein Fake, gleiches Muster wie test_mail_service.py.
Die "while True"-Schleife wird deterministisch nach GENAU einem
Durchlauf beendet, indem `asyncio.sleep` beim ersten Aufruf
`asyncio.CancelledError` auslöst - identisch zu dem, was beim echten
App-Shutdown (`mail_ingestion_task.cancel()`) passiert."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main as main_module
from app.mail.base import FetchedMessage
from app.main import _run_periodic_mail_ingestion
from app.models import Client, Matter, Message
from app.models.base import Base


class FakeMailProvider:
    """Erfüllt MailProvider strukturell (Protocol), kein echter IMAP-Server."""

    def __init__(self, messages: list[FetchedMessage]) -> None:
        self._messages = messages

    def fetch_new_messages(self) -> list[FetchedMessage]:
        return self._messages


@pytest.fixture()
def db_session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _synthetic_message(**overrides) -> FetchedMessage:
    defaults = dict(
        external_message_id="<synthetisch-14092026@example.test>",
        sender="max.mustermann@example.test",
        recipient="kanzlei@example.test",
        subject="AZ: MUSTER-001 Rückfrage",
        body_text="Aktenzeichen: MUSTER-001. Bitte um Rückmeldung.",
        received_at=datetime.now(timezone.utc),
        attachments=[],
    )
    defaults.update(overrides)
    return FetchedMessage(**defaults)


async def _run_one_iteration(settings, monkeypatch, *, session_factory) -> None:
    """Treibt `_run_periodic_mail_ingestion` an, laesst genau EINEN
    Schleifendurchlauf real ausfuehren und beendet die Endlosschleife
    danach deterministisch (wie ein echtes `task.cancel()`).

    `get_settings()` wird auf dasselbe `settings`-Objekt umgebogen, das
    auch dem Funktionsargument uebergeben wird (06.10., Owner-Direktive
    "SETTINGS -> E-MAIL"): die Schleife liest `mail_auto_sync_enabled`/
    `mail_poll_interval_seconds` JEDE Iteration frisch ueber die globale
    `get_settings()` neu ein (fuer echtes Hot-Reload ohne Neustart, siehe
    app/main.py) - ohne dieses Monkeypatch wuerde der Test versehentlich
    den PROZESSWEITEN `@lru_cache`-Zustand statt der hier extra gebauten
    Test-Settings beeinflussen."""
    monkeypatch.setattr(main_module, "SessionLocal", session_factory)
    monkeypatch.setattr(main_module, "get_settings", lambda: settings)

    sleep_calls = {"count": 0}

    async def _fake_sleep(_seconds: float) -> None:
        sleep_calls["count"] += 1
        raise asyncio.CancelledError()

    monkeypatch.setattr(main_module.asyncio, "sleep", _fake_sleep)

    task = asyncio.create_task(_run_periodic_mail_ingestion(settings))
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert sleep_calls["count"] == 1, "Schleife muss genau einmal echt durchlaufen sein"


def _settings_with_mail(**overrides):
    from app.config.settings import Settings

    defaults = dict(
        mail_provider="imap",
        mail_host="imap.example.test",
        mail_username="kanzlei@example.test",
        mail_password="geheim123",
    )
    defaults.update(overrides)
    return Settings(**defaults)


def test_disabled_mail_provider_does_nothing(monkeypatch) -> None:
    """Standardfall (`mail_provider=None`): kehrt SOFORT zurueck, OHNE
    jemals eine Verbindung zu versuchen oder die Schleife zu starten."""
    from app.config.settings import Settings

    settings = Settings()
    assert settings.mail_provider is None

    calls = {"provider_built": 0}

    def _fake_build(_settings):
        calls["provider_built"] += 1
        return None

    monkeypatch.setattr(main_module, "build_mail_provider", _fake_build)

    asyncio.run(_run_periodic_mail_ingestion(settings))

    assert calls["provider_built"] == 1


def test_configured_mailbox_ingests_and_auto_assigns_matching_message(
    monkeypatch, db_session: Session
) -> None:
    """Reale Ende-zu-Ende-Kette: konfiguriertes Postfach -> echter
    `MailIngestionService`-Aufruf -> neue `Message` -> echter
    `MatterMatchingService`/`MatterAssignmentService`-Aufruf -> bei
    eindeutigem Aktenzeichen-Treffer automatische Zuordnung, GENAU wie
    beim manuellen Aufruf (siehe tests/test_matching_*.py) - hier zum
    ERSTEN MAL tatsächlich automatisch ausgelöst statt nur isoliert
    aufrufbar."""
    client = Client(name="Beispiel Mandant")
    matter = Matter(client=client, title="Bestandsakte", reference_number="MUSTER-001")
    db_session.add_all([client, matter])
    db_session.commit()

    provider = FakeMailProvider([_synthetic_message()])
    monkeypatch.setattr(main_module, "build_mail_provider", lambda settings: provider)

    engine = db_session.get_bind()
    session_factory = sessionmaker(bind=engine)

    settings = _settings_with_mail(mail_attachment_storage_dir="data/mail_attachments_test")
    asyncio.run(_run_one_iteration(settings, monkeypatch, session_factory=session_factory))

    message = db_session.query(Message).filter_by(
        external_message_id="<synthetisch-14092026@example.test>"
    ).first()
    assert message is not None
    assert message.matter_id == matter.id


def test_configured_mailbox_extracts_text_from_attachments(
    monkeypatch, db_session: Session, tmp_path
) -> None:
    """ECHTER FUND (14.09., Nachtrag): `MailIngestionService` legte
    Anhaenge bisher NUR als `Document`-Zeile an, OHNE
    `DocumentProcessingService.process_document` aufzurufen (anders als
    der Chat-/Schriftsatz-Upload-Pfad) - `extracted_text` blieb dauerhaft
    `None`. Jetzt wird jedes neu erfasste Dokument vor der
    Zuordnungsbewertung real extrahiert."""
    from app.mail.base import FetchedAttachment
    from app.models import Document

    provider = FakeMailProvider(
        [
            _synthetic_message(
                attachments=[
                    FetchedAttachment(
                        filename="anhang.txt",
                        content=b"Synthetischer Anhangsinhalt zum Testen der Extraktion.",
                        mime_type="text/plain",
                    )
                ]
            )
        ]
    )
    monkeypatch.setattr(main_module, "build_mail_provider", lambda settings: provider)

    engine = db_session.get_bind()
    session_factory = sessionmaker(bind=engine)

    settings = _settings_with_mail(mail_attachment_storage_dir=str(tmp_path / "mail_attachments"))
    asyncio.run(_run_one_iteration(settings, monkeypatch, session_factory=session_factory))

    message = db_session.query(Message).filter_by(
        external_message_id="<synthetisch-14092026@example.test>"
    ).first()
    assert message is not None
    document = db_session.query(Document).filter_by(message_id=message.id).first()
    assert document is not None
    assert document.extracted_text is not None
    assert "Synthetischer Anhangsinhalt" in document.extracted_text
    assert document.ocr_status == "not_needed"


def test_configured_mailbox_leaves_ambiguous_message_unassigned(
    monkeypatch, db_session: Session
) -> None:
    """Kein Aktenzeichen-Treffer -> Nachricht bleibt unzugeordnet (kein
    Sicherheitsabstrich: lieber manuelle Pruefung als eine falsche
    automatische Zuordnung)."""
    client = Client(name="Anderer Mandant")
    matter = Matter(client=client, title="Andere Akte", reference_number="ANDERE-999")
    db_session.add_all([client, matter])
    db_session.commit()

    provider = FakeMailProvider([_synthetic_message()])
    monkeypatch.setattr(main_module, "build_mail_provider", lambda settings: provider)

    engine = db_session.get_bind()
    session_factory = sessionmaker(bind=engine)

    settings = _settings_with_mail(mail_attachment_storage_dir="data/mail_attachments_test")
    asyncio.run(_run_one_iteration(settings, monkeypatch, session_factory=session_factory))

    message = db_session.query(Message).filter_by(
        external_message_id="<synthetisch-14092026@example.test>"
    ).first()
    assert message is not None
    assert message.matter_id is None


# ==========================================================================
# Synchronisations-Schalter (06.10., Owner-Direktive "SETTINGS -> E-MAIL") -
# echte, GEPRUEFTE Werte (kein kosmetischer UI-Schalter): `mail_auto_sync_
# enabled`/`mail_poll_interval_seconds` werden JEDE Iteration frisch
# eingelesen (siehe _run_one_iteration-Docstring oben).
# ==========================================================================


def test_auto_sync_disabled_skips_ingestion_but_keeps_loop_alive(
    monkeypatch, db_session: Session
) -> None:
    """`mail_auto_sync_enabled=False` darf NICHT bedeuten "Schleife stoppt"
    (sonst wuerde ein spaeteres Wiedereinschalten nie mehr wirken, da der
    MailProvider nur einmal beim Start gebaut wird) - sie darf nur den
    tatsaechlichen Abruf in DIESER Iteration ueberspringen."""
    client = Client(name="Beispiel Mandant")
    matter = Matter(client=client, title="Bestandsakte", reference_number="MUSTER-001")
    db_session.add_all([client, matter])
    db_session.commit()

    provider = FakeMailProvider([_synthetic_message()])
    monkeypatch.setattr(main_module, "build_mail_provider", lambda settings: provider)

    engine = db_session.get_bind()
    session_factory = sessionmaker(bind=engine)

    settings = _settings_with_mail(
        mail_attachment_storage_dir="data/mail_attachments_test",
        mail_auto_sync_enabled=False,
    )
    asyncio.run(_run_one_iteration(settings, monkeypatch, session_factory=session_factory))

    # Kein Abruf fand statt - die synthetische Nachricht wurde NICHT erfasst.
    assert db_session.query(Message).count() == 0


def test_poll_interval_is_read_fresh_each_iteration(monkeypatch, db_session: Session) -> None:
    """`mail_poll_interval_seconds` steuert tatsaechlich die `asyncio.sleep`-
    Dauer - kein hart codierter Wert mehr."""
    provider = FakeMailProvider([])
    monkeypatch.setattr(main_module, "build_mail_provider", lambda settings: provider)

    engine = db_session.get_bind()
    session_factory = sessionmaker(bind=engine)

    settings = _settings_with_mail(
        mail_attachment_storage_dir="data/mail_attachments_test",
        mail_poll_interval_seconds=60,
    )

    sleep_durations: list[float] = []

    async def _drive() -> None:
        monkeypatch.setattr(main_module, "SessionLocal", session_factory)
        monkeypatch.setattr(main_module, "get_settings", lambda: settings)

        async def _fake_sleep(seconds: float) -> None:
            sleep_durations.append(seconds)
            raise asyncio.CancelledError()

        monkeypatch.setattr(main_module.asyncio, "sleep", _fake_sleep)

        task = asyncio.create_task(_run_periodic_mail_ingestion(settings))
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(_drive())

    assert sleep_durations == [60]


def test_ingestion_failure_does_not_crash_the_loop(monkeypatch, db_session: Session) -> None:
    """Ein fehlgeschlagener Abruf (z. B. Postfach kurzzeitig nicht
    erreichbar) darf die Schleife NICHT beenden - naechster Versuch nach
    der Wartezeit."""

    class _FailingProvider:
        def fetch_new_messages(self):
            raise ConnectionError("Postfach nicht erreichbar (simuliert)")

    monkeypatch.setattr(main_module, "build_mail_provider", lambda settings: _FailingProvider())

    engine = db_session.get_bind()
    session_factory = sessionmaker(bind=engine)

    settings = _settings_with_mail(mail_attachment_storage_dir="data/mail_attachments_test")
    # Erwartung: KEINE Exception propagiert nach aussen - der Fehler wird
    # intern behandelt, die Schleife laeuft weiter zum naechsten
    # `sleep()`-Aufruf (der hier wie gewohnt die Schleife per
    # CancelledError deterministisch beendet).
    asyncio.run(_run_one_iteration(settings, monkeypatch, session_factory=session_factory))
