"""Tests für app/web/matters_router.py (Akten, 13.09., UI/UX-Überarbeitung) -
löst den bisherigen Platzhalter unter `/dashboard/matters` ab.

Gleiches Testmuster wie tests/test_web_clients.py: In-Memory-SQLite über
app.dependency_overrides."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import get_db
from app.main import app
from app.models import (
    AuditEvent,
    ChatConversation,
    Client,
    Deadline,
    Document,
    Draft,
    Matter,
    Message,
    Note,
    Task,
    User,
)
from app.models.base import Base
from tests.auth_test_utils import extract_csrf, login_as_admin, seed_roles


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
    try:
        test_client = TestClient(app)
        login_as_admin(db_session, test_client)
        yield test_client
    finally:
        app.dependency_overrides.clear()


def _matter(db: Session, *, title: str = "Testakte", reference_number: str | None = "M-1") -> Matter:
    client_row = Client(name="Max Mustermann", client_number=f"K-{uuid4().hex[:8]}")
    matter = Matter(client=client_row, title=title, reference_number=reference_number)
    db.add_all([client_row, matter])
    db.commit()
    db.refresh(matter)
    return matter


def test_matters_page_is_no_longer_a_placeholder(client: TestClient) -> None:
    response = client.get("/dashboard/matters")
    assert response.status_code == 200
    assert "in Vorbereitung" not in response.text
    assert "Akten" in response.text


def test_matters_page_lists_created_matter(client: TestClient, db_session: Session) -> None:
    _matter(db_session, title="Sichtbare Akte")
    response = client.get("/dashboard/matters")
    assert "Sichtbare Akte" in response.text


def test_matters_page_row_menu_offers_quick_actions_without_opening_the_matter(
    client: TestClient, db_session: Session
) -> None:
    """ECHTER FUND (19.09., UI/UX-Referenzabgleich "13_akten_uebersicht.png"):
    die Referenz zeigt ein "..."-Schnellzugriffsmenü pro Zeile (öffnen/Chat/
    archivieren), bisher musste man dafür immer erst die Akte öffnen. Alle
    verlinkten Aktionen sind bereits bestehende, echte Routen - reine
    Verdrahtung, keine neue Logik."""
    matter = _matter(db_session, title="Akte mit Schnellmenü")
    response = client.get("/dashboard/matters")

    assert f'href="/dashboard/matters/{matter.id}"' in response.text
    assert f'href="/dashboard/chat?new=1&amp;matter={matter.id}"' in response.text
    assert f'action="/dashboard/matters/{matter.id}/archive"' in response.text
    assert "Akte wieder öffnen" not in response.text


def test_matters_page_row_menu_offers_reopen_for_archived_matters(
    client: TestClient, db_session: Session
) -> None:
    matter = _matter(db_session, title="Abgeschlossene Akte")
    matter.status = "closed"
    db_session.commit()

    response = client.get("/dashboard/matters")

    assert f'action="/dashboard/matters/{matter.id}/reopen"' in response.text
    assert f'action="/dashboard/matters/{matter.id}/archive"' not in response.text


def test_matters_page_search_filters_by_title(client: TestClient, db_session: Session) -> None:
    _matter(db_session, title="Mietrechtsstreit")
    _matter(db_session, title="Steuerbescheid Einspruch", reference_number="M-2")
    response = client.get("/dashboard/matters?search=Mietrecht")
    assert "Mietrechtsstreit" in response.text
    assert "Steuerbescheid Einspruch" not in response.text


def test_matters_page_shows_create_matter_form(client: TestClient, db_session: Session) -> None:
    """ECHTER FUND (18.09., Referenzabgleich `13_akten_uebersicht.png`):
    die Referenz zeigt "+ Neue Akte" als primaere Aktion - im Produkt
    fehlte projektweit jeder manuelle Anlegeweg fuer Akten (nur der
    automatische Schnellentwurf-Weg existierte)."""
    client_row = Client(name="Anna Musterfrau", client_number="K-1")
    db_session.add(client_row)
    db_session.commit()

    response = client.get("/dashboard/matters")

    assert response.status_code == 200
    assert "Akte anlegen" in response.text
    assert 'name="title"' in response.text
    assert 'name="client_id"' in response.text
    assert "Anna Musterfrau" in response.text


def test_create_matter_persists_it_and_links_to_chosen_client(
    client: TestClient, db_session: Session
) -> None:
    client_row = Client(name="Beispiel GmbH", client_number="K-2")
    db_session.add(client_row)
    db_session.commit()
    csrf = extract_csrf(client.get("/dashboard/matters").text)

    response = client.post(
        "/dashboard/matters/create",
        data={
            "csrf_token": csrf,
            "title": "Einspruch Steuerbescheid 2026",
            "client_id": client_row.id,
            "reference_number": "005/2026",
            "practice_area": "Steuerrecht",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    matter = db_session.query(Matter).filter_by(title="Einspruch Steuerbescheid 2026").first()
    assert matter is not None
    assert matter.client_id == client_row.id
    assert matter.reference_number == "005/2026"
    assert matter.practice_area == "Steuerrecht"
    assert matter.status == "open"
    assert response.headers["location"] == f"/dashboard/matters/{matter.id}"

    event = (
        db_session.query(AuditEvent)
        .filter_by(entity_type="Matter", entity_id=matter.id)
        .first()
    )
    assert event is not None
    assert event.event_type == "matter_created_manually"


def test_create_matter_with_blank_title_is_rejected_without_persisting(
    client: TestClient, db_session: Session
) -> None:
    client_row = Client(name="Beispiel GmbH", client_number="K-3")
    db_session.add(client_row)
    db_session.commit()
    csrf = extract_csrf(client.get("/dashboard/matters").text)

    response = client.post(
        "/dashboard/matters/create",
        data={"csrf_token": csrf, "title": "   ", "client_id": client_row.id},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "error=" in response.headers["location"]
    assert db_session.query(Matter).count() == 0


def test_create_matter_with_unknown_client_is_rejected(
    client: TestClient, db_session: Session
) -> None:
    csrf = extract_csrf(client.get("/dashboard/matters").text)

    response = client.post(
        "/dashboard/matters/create",
        data={"csrf_token": csrf, "title": "Neue Akte", "client_id": "does-not-exist"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "error=" in response.headers["location"]
    assert db_session.query(Matter).count() == 0


def test_create_matter_with_duplicate_reference_number_is_rejected(
    client: TestClient, db_session: Session
) -> None:
    client_row = Client(name="Beispiel GmbH", client_number="K-4")
    db_session.add(client_row)
    db_session.commit()
    _matter(db_session, title="Bestehende Akte", reference_number="007/2026")
    csrf = extract_csrf(client.get("/dashboard/matters").text)

    response = client.post(
        "/dashboard/matters/create",
        data={
            "csrf_token": csrf,
            "title": "Neue Akte",
            "client_id": client_row.id,
            "reference_number": "007/2026",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "error=" in response.headers["location"]
    assert db_session.query(Matter).filter_by(title="Neue Akte").count() == 0


def test_create_matter_requires_a_valid_csrf_token(
    client: TestClient, db_session: Session
) -> None:
    client_row = Client(name="Beispiel GmbH", client_number="K-5")
    db_session.add(client_row)
    db_session.commit()

    response = client.post(
        "/dashboard/matters/create",
        data={"csrf_token": "invalid", "title": "Neue Akte", "client_id": client_row.id},
    )

    assert response.status_code == 403
    assert db_session.query(Matter).count() == 0


def test_matters_page_status_filter(client: TestClient, db_session: Session) -> None:
    open_matter = _matter(db_session, title="Offene Akte")
    closed_matter = _matter(db_session, title="Geschlossene Akte", reference_number="M-3")
    closed_matter.status = "closed"
    db_session.commit()

    response = client.get("/dashboard/matters?status=open")
    assert open_matter.title in response.text
    assert closed_matter.title not in response.text


def test_matter_detail_page_returns_404_for_unknown_matter(client: TestClient) -> None:
    response = client.get("/dashboard/matters/does-not-exist")
    assert response.status_code == 404


def test_matter_detail_page_shows_client_and_status(client: TestClient, db_session: Session) -> None:
    matter = _matter(db_session)
    response = client.get(f"/dashboard/matters/{matter.id}")
    assert response.status_code == 200
    assert matter.title in response.text
    assert matter.client.name in response.text
    assert matter.reference_number in response.text


def test_matter_detail_page_shows_linked_documents(client: TestClient, db_session: Session) -> None:
    matter = _matter(db_session)
    document = Document(
        matter_id=matter.id,
        file_path="/tmp/x.pdf",
        original_filename="Mietvertrag.pdf",
        classified_type="Mietvertrag",
    )
    db_session.add(document)
    db_session.commit()

    response = client.get(f"/dashboard/matters/{matter.id}")
    assert "Mietvertrag.pdf" in response.text
    assert "Mietvertrag" in response.text


def test_matter_detail_page_shows_file_type_badges_for_documents(
    client: TestClient, db_session: Session
) -> None:
    """ECHTER FUND (19.09., Owner-Direktive "Dateiformat-Icons"): Dokumente
    erschienen projektweit nur als reiner Dateiname, ohne jede visuelle
    Typ-Kennzeichnung (siehe icons.file_type_badge() in _icons.html) -
    die Referenzbilder zeigen durchgaengig ein farbiges Typ-Badge. Prueft
    dass PDF/DOCX/ein unbekanntes Format je ihr korrektes, EHRLICHES
    Badge bekommen (kein erfundenes Icon fuer ein nicht unterstuetztes
    Format)."""
    matter = _matter(db_session)
    db_session.add_all(
        [
            Document(matter_id=matter.id, file_path="/tmp/a.pdf", original_filename="Bescheid.pdf"),
            Document(matter_id=matter.id, file_path="/tmp/b.docx", original_filename="Entwurf.docx"),
            Document(matter_id=matter.id, file_path="/tmp/c.xyz", original_filename="Sonstiges.xyz"),
            # ECHTER FUND (20.09.): "Umbenennen" (document_actions_router.py::
            # rename_document) laesst `original_filename` OHNE Endungspruefung
            # aendern - das Badge MUSS trotzdem korrekt bleiben, weil es aus
            # dem unveraenderlichen `file_path` abgeleitet wird, nicht aus dem
            # frei umbenennbaren Anzeigenamen.
            Document(
                matter_id=matter.id,
                file_path="/tmp/uuid1234_original.pdf",
                original_filename="Umbenannt ohne Endung",
            ),
        ]
    )
    db_session.commit()

    response = client.get(f"/dashboard/matters/{matter.id}")

    assert "file-type-badge--pdf" in response.text
    assert ">PDF</span>" in response.text
    assert "file-type-badge--docx" in response.text
    assert ">DOCX</span>" in response.text
    # Unbekanntes Format zeigt ehrlich seine echte Endung statt eines
    # falschen PDF/DOCX-Icons:
    assert "file-type-badge--generic" in response.text
    assert ">XYZ</span>" in response.text
    # Trotz irrefuehrendem Anzeigenamen ("Umbenannt ohne Endung", keine
    # erkennbare Endung) zeigt das Badge weiterhin korrekt PDF, weil es aus
    # file_path stammt statt aus dem frei umbenennbaren Anzeigenamen - ZWEI
    # PDF-Badges (das erste echte + das umbenannte), nicht eines echtes plus
    # ein faelschliches "?"-Badge fuer den vierten Eintrag:
    assert "Umbenannt ohne Endung" in response.text
    assert response.text.count(">PDF</span>") == 2
    assert ">?</span>" not in response.text


def test_matter_detail_page_shows_tasks_and_deadlines(client: TestClient, db_session: Session) -> None:
    matter = _matter(db_session)
    task = Task(matter_id=matter.id, title="Fristverlängerung beantragen", due_date=date(2026, 10, 1))
    deadline = Deadline(
        matter_id=matter.id,
        source_text="Einspruchsfrist läuft am 01.11.2026 ab.",
        due_date=date(2026, 11, 1),
        review_status="unreviewed",
    )
    db_session.add_all([task, deadline])
    db_session.commit()

    response = client.get(f"/dashboard/matters/{matter.id}")
    assert "Fristverlängerung beantragen" in response.text
    assert "Einspruchsfrist läuft am 01.11.2026 ab." in response.text


def test_matter_detail_page_shows_messages(client: TestClient, db_session: Session) -> None:
    matter = _matter(db_session)
    message = Message(
        matter_id=matter.id, direction="inbound", sender="gegner@example.test", subject="Ihr Schreiben vom..."
    )
    db_session.add(message)
    db_session.commit()

    response = client.get(f"/dashboard/matters/{matter.id}")
    assert "Ihr Schreiben vom..." in response.text
    assert "gegner@example.test" in response.text
    # ECHTER FUND (17.09., Owner-Direktive §5/§7): diese Zeile zeigte
    # bereits eine echte Nachricht an, war aber kein Link auf die
    # vollstaendige Posteingang-Detailansicht (Volltext, Anhaenge,
    # Zusammenfassen/Antworten) - jetzt ein echter Link.
    assert f'href="/dashboard/inbox/{message.id}"' in response.text


def test_matter_detail_page_shows_linked_chat_conversations_and_offers_to_open_them(
    client: TestClient, db_session: Session
) -> None:
    matter = _matter(db_session)
    user = db_session.query(User).first()
    conversation = ChatConversation(matter_id=matter.id, user_id=user.id, title="Frage zur Mieterhöhung")
    db_session.add(conversation)
    db_session.commit()

    response = client.get(f"/dashboard/matters/{matter.id}")
    assert "Frage zur Mieterhöhung" in response.text
    assert f'href="/dashboard/chat/{conversation.id}"' in response.text


def test_matter_detail_page_without_chat_conversation_offers_a_new_chat_instead_of_a_fake_link(
    client: TestClient, db_session: Session
) -> None:
    """Es gibt (noch) keine Funktion, einen NEUEN Chat direkt an eine
    bestehende Akte zu binden (app/web/chat_router.py::send_message legt
    bei matter_id=None immer eine eigene neue Quick-Akte an) - die Seite
    darf das nicht vortäuschen, sondern muss ehrlich auf einen neuen,
    eigenständigen Chat verweisen."""
    matter = _matter(db_session)
    response = client.get(f"/dashboard/matters/{matter.id}")
    assert 'href="/dashboard/chat?new=1"' in response.text


def test_matter_detail_page_shows_drafts(client: TestClient, db_session: Session) -> None:
    matter = _matter(db_session)
    draft = Draft(matter_id=matter.id, content="Sehr geehrte Damen und Herren...", version=1, status="draft")
    db_session.add(draft)
    db_session.commit()

    response = client.get(f"/dashboard/matters/{matter.id}")
    assert "Version 1" in response.text
    assert f'href="/dashboard/drafts/{draft.id}"' in response.text


def test_matters_are_isolated_by_matter_id(client: TestClient, db_session: Session) -> None:
    """Aktenisolation (CLAUDE.md): Dokumente/Aufgaben/Nachrichten einer
    ANDEREN Akte dürfen auf der Detailseite nie auftauchen."""
    matter_a = _matter(db_session, title="Akte A", reference_number="A-1")
    matter_b = _matter(db_session, title="Akte B", reference_number="B-1")
    db_session.add(Document(matter_id=matter_b.id, file_path="/tmp/b.pdf", original_filename="NurAkteB.pdf"))
    db_session.commit()

    response = client.get(f"/dashboard/matters/{matter_a.id}")
    assert "NurAkteB.pdf" not in response.text


def test_matter_page_shows_human_readable_statuses_not_internal_values(
    client: TestClient, db_session: Session
) -> None:
    """ECHTER FUND beim UI-Durchgang (14.09.): derselbe Entwurfsstatus
    erschien je nach Seite unterschiedlich - die Entwurfs-Detailseite
    uebersetzte ihn ("freigegeben"), Aktenseite und Entwurfsliste zeigten
    dagegen den ROHEN internen Wert ("approved"). Gleiches Bild bei der
    Frist ("unreviewed"). Interne Statusbezeichner gehoeren nicht in die
    Produktoberflaeche (UI-Direktive §11), und zwei Sprachen fuer dieselbe
    Sache sind fuer den Anwalt schlicht verwirrend."""
    from datetime import date

    from app.models import Deadline, Draft

    client_obj = Client(name="Statusmandant")
    matter = Matter(client=client_obj, title="Statusakte")
    db_session.add_all([client_obj, matter])
    db_session.flush()
    db_session.add_all([
        Draft(matter_id=matter.id, version=1, status="approved", content="Text"),
        Deadline(matter_id=matter.id, due_date=date.today(), review_status="unreviewed"),
    ])
    db_session.commit()
    # Die `client`-Fixture meldet bereits als Admin an.

    response = client.get(f"/dashboard/matters/{matter.id}")

    assert "freigegeben" in response.text
    assert "ungeprüft" in response.text
    assert ">approved<" not in response.text
    assert ">unreviewed<" not in response.text


def test_matter_document_view_shows_document_text(client: TestClient, db_session: Session) -> None:
    """Aktendokumente (insbesondere Mail-Anhaenge) waren im Produkt
    ueberhaupt nicht zu oeffnen - der Dateiname auf der Aktenseite war
    toter Text, und der einzige Dokument-Viewer setzte eine
    Chat-Unterhaltung voraus."""
    matter = _matter(db_session)
    document = Document(
        matter_id=matter.id,
        file_path="/tmp/bescheid.pdf",
        original_filename="Steuerbescheid.pdf",
        classified_type="Steuerbescheid",
        extracted_text="Festgesetzte Einkommensteuer fuer 2024.",
    )
    db_session.add(document)
    db_session.commit()

    response = client.get(f"/dashboard/matters/{matter.id}/document/{document.id}")

    assert response.status_code == 200
    assert "Steuerbescheid.pdf" in response.text
    assert "Festgesetzte Einkommensteuer" in response.text


def test_matter_document_view_shows_ki_aktionen_buttons(
    client: TestClient, db_session: Session
) -> None:
    """Referenz `28_dokument_vorschau_export.png` (16.09., UI/UX-Sweep) -
    "Dokument analysieren"/"Zusammenfassung erstellen"/"Wichtige Daten
    extrahieren"/"Schriftsatz-Entwurf erstellen" posten an die neue Route
    app/web/chat_router.py::start_conversation_from_document (eigene
    Verhaltenstests dort, siehe tests/test_web_chat.py) - dieser Test
    deckt nur die Sichtbarkeit/Verdrahtung auf dieser Seite ab."""
    matter = _matter(db_session)
    document = Document(
        matter_id=matter.id, file_path="/tmp/a.pdf", original_filename="Anhang.pdf"
    )
    db_session.add(document)
    db_session.commit()

    response = client.get(f"/dashboard/matters/{matter.id}/document/{document.id}")

    assert response.status_code == 200
    assert f'action="/dashboard/chat/from-document/{document.id}"' in response.text


def test_matter_document_ki_aktionen_have_ai_loading_wiring(
    client: TestClient, db_session: Session
) -> None:
    """KI-Waiting-/Buffering-UX (20.09., Owner-Direktive "KI-WAITING-/
    BUFFERING-UX PROJEKTWEIT PRÜFEN UND VERBESSERN"): diese Buttons lösen
    `start_conversation_from_document` aus, das den Claude-Aufruf
    SYNCHRON vor dem Redirect ausführt (app/web/chat_router.py) - ohne
    Feedback sah ein Klick hier wie ein eingefrorenes System aus."""
    matter = _matter(db_session)
    document = Document(
        matter_id=matter.id, file_path="/tmp/a.pdf", original_filename="Anhang.pdf"
    )
    db_session.add(document)
    db_session.commit()

    response = client.get(f"/dashboard/matters/{matter.id}/document/{document.id}")

    assert response.status_code == 200
    assert response.text.count('class="js-ai-form"') >= 4
    assert response.text.count('data-ai-loading-label="Wird gestartet') >= 4
    for label in [
        "Dokument analysieren",
        "Zusammenfassung erstellen",
        "Wichtige Daten extrahieren",
        "Schriftsatz-Entwurf erstellen",
    ]:
        assert label in response.text


def test_matter_document_view_is_linked_from_the_matter_page(
    client: TestClient, db_session: Session
) -> None:
    matter = _matter(db_session)
    document = Document(
        matter_id=matter.id, file_path="/tmp/a.pdf", original_filename="Anhang.pdf"
    )
    db_session.add(document)
    db_session.commit()

    response = client.get(f"/dashboard/matters/{matter.id}")

    assert f"/dashboard/matters/{matter.id}/document/{document.id}" in response.text


def test_matter_document_view_refuses_a_document_from_another_matter(
    client: TestClient, db_session: Session
) -> None:
    """AKTENISOLATION (CLAUDE.md, nicht verhandelbar): eine gueltige
    Dokument-ID darf NICHT ueber eine fremde Akte abrufbar sein. Ohne die
    matter_id-Bedingung in der Abfrage waere die Aktenzugehoerigkeit reine
    Dekoration in der URL."""
    matter_a = _matter(db_session, title="Akte A")
    matter_b = _matter(db_session, title="Akte B", reference_number="M-2")
    document = Document(
        matter_id=matter_b.id,
        file_path="/tmp/fremd.pdf",
        original_filename="Fremddokument.pdf",
        extracted_text="Inhalt der fremden Akte.",
    )
    db_session.add(document)
    db_session.commit()

    response = client.get(f"/dashboard/matters/{matter_a.id}/document/{document.id}")

    assert response.status_code == 404
    assert "Fremddokument.pdf" not in response.text
    assert "Inhalt der fremden Akte" not in response.text


def test_matter_document_view_returns_404_for_unknown_document(
    client: TestClient, db_session: Session
) -> None:
    matter = _matter(db_session)
    response = client.get(f"/dashboard/matters/{matter.id}/document/does-not-exist")
    assert response.status_code == 404


# --- Dokument-Download (16.09., UI/UX-Sweep - Referenz
# `02_chat_dokumentkontext.png`/`28_dokument_vorschau_export.png` zeigen
# "Herunterladen" als Aktion) ---


def test_matter_document_download_returns_the_real_file(
    client: TestClient, db_session: Session, tmp_path
) -> None:
    matter = _matter(db_session)
    real_file = tmp_path / "original.pdf"
    real_file.write_bytes(b"%PDF-1.4 Testinhalt")
    document = Document(
        matter_id=matter.id,
        file_path=str(real_file),
        original_filename="Steuerbescheid.pdf",
        mime_type="application/pdf",
    )
    db_session.add(document)
    db_session.commit()

    response = client.get(
        f"/dashboard/matters/{matter.id}/document/{document.id}/download"
    )

    assert response.status_code == 200
    assert response.content == b"%PDF-1.4 Testinhalt"
    assert response.headers["content-type"] == "application/pdf"
    assert "Steuerbescheid.pdf" in response.headers["content-disposition"]


def test_matter_document_download_refuses_a_document_from_another_matter(
    client: TestClient, db_session: Session, tmp_path
) -> None:
    """Dieselbe Aktenisolations-Pruefung wie die Vorschau-Route."""
    matter_a = _matter(db_session, title="Akte A")
    matter_b = _matter(db_session, title="Akte B", reference_number="M-3")
    real_file = tmp_path / "fremd.pdf"
    real_file.write_bytes(b"fremder Inhalt")
    document = Document(
        matter_id=matter_b.id, file_path=str(real_file), original_filename="Fremd.pdf"
    )
    db_session.add(document)
    db_session.commit()

    response = client.get(
        f"/dashboard/matters/{matter_a.id}/document/{document.id}/download"
    )

    assert response.status_code == 404


def test_matter_document_download_returns_404_when_file_missing_on_disk(
    client: TestClient, db_session: Session, tmp_path
) -> None:
    """Ehrlicher Fehler statt Absturz, wenn die Datenbankzeile existiert,
    die Datei aber (z. B. nach manueller Bereinigung) nicht mehr auf der
    Platte liegt."""
    matter = _matter(db_session)
    document = Document(
        matter_id=matter.id,
        file_path=str(tmp_path / "nie-geschrieben.pdf"),
        original_filename="Weg.pdf",
    )
    db_session.add(document)
    db_session.commit()

    response = client.get(
        f"/dashboard/matters/{matter.id}/document/{document.id}/download"
    )

    assert response.status_code == 404


# --- Dokument loeschen (20.09., Owner-Direktive "WORKSTREAM A — DOKUMENTE
# LOESCHBAR") - Soft-Delete, siehe app/documents/lifecycle.py. Voller
# Lifecycle: Upload -> Loeschen -> Liste aktualisiert -> erneuter
# Oeffnungsversuch (Viewer/Download/Seitenbild) -> Wiederherstellen. ---


def test_delete_document_marks_it_as_deleted_and_redirects_to_matter(
    client: TestClient, db_session: Session
) -> None:
    matter = _matter(db_session)
    document = Document(
        matter_id=matter.id, file_path="/tmp/a.pdf", original_filename="Anhang.pdf"
    )
    db_session.add(document)
    db_session.commit()
    document_id = document.id

    response = client.post(
        f"/dashboard/matters/{matter.id}/document/{document_id}/delete",
        data={"csrf_token": extract_csrf(client.get(f"/dashboard/matters/{matter.id}").text)},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == f"/dashboard/matters/{matter.id}"
    db_session.expire_all()
    reloaded = db_session.get(Document, document_id)
    assert reloaded.deleted_at is not None
    # Physische Datei-Referenz (file_path) bleibt unangetastet.
    assert reloaded.file_path == "/tmp/a.pdf"


def test_deleted_document_disappears_from_matter_document_list(
    client: TestClient, db_session: Session
) -> None:
    matter = _matter(db_session)
    document = Document(
        matter_id=matter.id, file_path="/tmp/a.pdf", original_filename="Anhang.pdf"
    )
    db_session.add(document)
    db_session.commit()
    csrf = extract_csrf(client.get(f"/dashboard/matters/{matter.id}").text)

    client.post(
        f"/dashboard/matters/{matter.id}/document/{document.id}/delete",
        data={"csrf_token": csrf},
    )
    response = client.get(f"/dashboard/matters/{matter.id}")

    assert "Anhang.pdf" not in response.text
    assert "1 gelöschte(s) Dokument(e) anzeigen" in response.text


def test_show_deleted_query_param_reveals_deleted_document_with_restore_action(
    client: TestClient, db_session: Session
) -> None:
    matter = _matter(db_session)
    document = Document(
        matter_id=matter.id, file_path="/tmp/a.pdf", original_filename="Anhang.pdf"
    )
    db_session.add(document)
    db_session.commit()
    csrf = extract_csrf(client.get(f"/dashboard/matters/{matter.id}").text)
    client.post(
        f"/dashboard/matters/{matter.id}/document/{document.id}/delete",
        data={"csrf_token": csrf},
    )

    response = client.get(f"/dashboard/matters/{matter.id}?show_deleted=1")

    assert "Anhang.pdf" in response.text
    assert f"/document/{document.id}/restore" in response.text


def test_deleted_document_returns_404_on_reopen_attempt(
    client: TestClient, db_session: Session
) -> None:
    """Voller Lifecycle-Punkt "ERNEUTEN ÖFFNUNGSVERSUCH": Viewer, Download
    UND die Seitenbild-Route muessen fuer ein geloeschtes Dokument
    konsistent 404 liefern - identisches Verhalten wie ein tatsaechlich
    entferntes Dokument, kein Unterschied von aussen erkennbar."""
    matter = _matter(db_session)
    document = Document(
        matter_id=matter.id, file_path="/tmp/a.pdf", original_filename="Anhang.pdf"
    )
    db_session.add(document)
    db_session.commit()
    csrf = extract_csrf(client.get(f"/dashboard/matters/{matter.id}").text)
    client.post(
        f"/dashboard/matters/{matter.id}/document/{document.id}/delete",
        data={"csrf_token": csrf},
    )

    view_response = client.get(f"/dashboard/matters/{matter.id}/document/{document.id}")
    download_response = client.get(
        f"/dashboard/matters/{matter.id}/document/{document.id}/download"
    )
    page_response = client.get(
        f"/dashboard/matters/{matter.id}/document/{document.id}/page/1.png"
    )

    assert view_response.status_code == 404
    assert download_response.status_code == 404
    assert page_response.status_code == 404


def test_delete_document_refuses_a_document_from_another_matter(
    client: TestClient, db_session: Session
) -> None:
    """AKTENISOLATION/IDOR-Schutz (CLAUDE.md): eine gueltige Dokument-ID
    darf nicht ueber eine fremde Akten-URL geloescht werden."""
    matter_a = _matter(db_session, title="Akte A")
    matter_b = _matter(db_session, title="Akte B", reference_number="M-2")
    document = Document(
        matter_id=matter_b.id, file_path="/tmp/fremd.pdf", original_filename="Fremd.pdf"
    )
    db_session.add(document)
    db_session.commit()
    document_id = document.id
    csrf = extract_csrf(client.get(f"/dashboard/matters/{matter_a.id}").text)

    response = client.post(
        f"/dashboard/matters/{matter_a.id}/document/{document_id}/delete",
        data={"csrf_token": csrf},
    )

    assert response.status_code == 404
    db_session.expire_all()
    reloaded = db_session.get(Document, document_id)
    assert reloaded.deleted_at is None


def test_delete_document_requires_csrf(client: TestClient, db_session: Session) -> None:
    matter = _matter(db_session)
    document = Document(
        matter_id=matter.id, file_path="/tmp/a.pdf", original_filename="Anhang.pdf"
    )
    db_session.add(document)
    db_session.commit()
    document_id = document.id

    response = client.post(
        f"/dashboard/matters/{matter.id}/document/{document_id}/delete",
        data={"csrf_token": "invalid"},
    )

    assert response.status_code == 403
    db_session.expire_all()
    reloaded = db_session.get(Document, document_id)
    assert reloaded.deleted_at is None


def test_deleting_an_already_deleted_document_returns_404(
    client: TestClient, db_session: Session
) -> None:
    """Doppeltes Loeschen (z. B. per direktem zweiten API-Aufruf) darf
    nicht stillschweigend erneut "erfolgreich" sein - das Dokument ist
    bereits aus der aktiven Sicht verschwunden, ein zweiter Versuch trifft
    dieselbe `deleted_at IS NULL`-Bedingung wie jedes andere bereits
    entfernte Dokument."""
    matter = _matter(db_session)
    document = Document(
        matter_id=matter.id, file_path="/tmp/a.pdf", original_filename="Anhang.pdf"
    )
    db_session.add(document)
    db_session.commit()
    csrf = extract_csrf(client.get(f"/dashboard/matters/{matter.id}").text)
    client.post(
        f"/dashboard/matters/{matter.id}/document/{document.id}/delete",
        data={"csrf_token": csrf},
    )

    second_attempt = client.post(
        f"/dashboard/matters/{matter.id}/document/{document.id}/delete",
        data={"csrf_token": csrf},
    )

    assert second_attempt.status_code == 404


def test_restore_document_makes_it_visible_and_reopenable_again(
    client: TestClient, db_session: Session, tmp_path
) -> None:
    matter = _matter(db_session)
    real_file = tmp_path / "original.pdf"
    real_file.write_bytes(b"%PDF-1.4 Testinhalt")
    document = Document(
        matter_id=matter.id, file_path=str(real_file), original_filename="Anhang.pdf"
    )
    db_session.add(document)
    db_session.commit()
    csrf = extract_csrf(client.get(f"/dashboard/matters/{matter.id}").text)
    client.post(
        f"/dashboard/matters/{matter.id}/document/{document.id}/delete",
        data={"csrf_token": csrf},
    )

    restore_response = client.post(
        f"/dashboard/matters/{matter.id}/document/{document.id}/restore",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )

    assert restore_response.status_code == 303
    db_session.expire_all()
    reloaded = db_session.get(Document, document.id)
    assert reloaded.deleted_at is None

    view_response = client.get(f"/dashboard/matters/{matter.id}/document/{document.id}")
    download_response = client.get(
        f"/dashboard/matters/{matter.id}/document/{document.id}/download"
    )
    assert view_response.status_code == 200
    assert download_response.status_code == 200


def test_delete_document_writes_an_audit_event_visible_in_matter_history(
    client: TestClient, db_session: Session
) -> None:
    matter = _matter(db_session)
    document = Document(
        matter_id=matter.id, file_path="/tmp/a.pdf", original_filename="Anhang.pdf"
    )
    db_session.add(document)
    db_session.commit()
    csrf = extract_csrf(client.get(f"/dashboard/matters/{matter.id}").text)

    client.post(
        f"/dashboard/matters/{matter.id}/document/{document.id}/delete",
        data={"csrf_token": csrf},
    )
    response = client.get(f"/dashboard/matters/{matter.id}?show_deleted=1")

    assert "document_deleted" in response.text


# --- "Erkannte Fristen" auf der Dokumentseite (16.09., UI/UX-Sweep -
# Referenz `24_dokument_editor_ki_assistent.png`) ---


def test_matter_document_view_shows_no_deadline_section_when_none_found(
    client: TestClient, db_session: Session
) -> None:
    matter = _matter(db_session)
    document = Document(
        matter_id=matter.id, file_path="/tmp/a.pdf", original_filename="Anhang.pdf"
    )
    db_session.add(document)
    db_session.commit()

    response = client.get(f"/dashboard/matters/{matter.id}/document/{document.id}")

    assert response.status_code == 200
    assert "Erkannte Fristen" in response.text
    assert "Keine Fristen in diesem Dokument erkannt." in response.text


def test_matter_document_view_shows_deadlines_found_in_this_document(
    client: TestClient, db_session: Session
) -> None:
    matter = _matter(db_session)
    document = Document(
        matter_id=matter.id, file_path="/tmp/a.pdf", original_filename="Anhang.pdf"
    )
    db_session.add(document)
    db_session.commit()
    deadline = Deadline(
        matter_id=matter.id,
        document_id=document.id,
        source_text="Frist zur Stellungnahme bis zum 15.03.2027",
        due_date=date(2027, 3, 15),
        confidence=0.5,
    )
    db_session.add(deadline)
    db_session.commit()

    response = client.get(f"/dashboard/matters/{matter.id}/document/{document.id}")

    assert response.status_code == 200
    assert "Frist zur Stellungnahme bis zum 15.03.2027" in response.text
    assert "15.03.2027" in response.text


def test_matter_document_view_only_shows_deadlines_from_this_document(
    client: TestClient, db_session: Session
) -> None:
    """Zwei Dokumente derselben Akte duerfen sich ihre Fristen nicht
    gegenseitig anzeigen - genau wie bei den personenbezogenen Daten
    bleibt jede Dokumentseite auf IHR EIGENES Dokument beschraenkt."""
    matter = _matter(db_session)
    document_a = Document(
        matter_id=matter.id, file_path="/tmp/a.pdf", original_filename="A.pdf"
    )
    document_b = Document(
        matter_id=matter.id, file_path="/tmp/b.pdf", original_filename="B.pdf"
    )
    db_session.add_all([document_a, document_b])
    db_session.commit()
    db_session.add(
        Deadline(
            matter_id=matter.id,
            document_id=document_b.id,
            source_text="Frist nur in Dokument B",
            due_date=date(2027, 1, 1),
        )
    )
    db_session.commit()

    response = client.get(f"/dashboard/matters/{matter.id}/document/{document_a.id}")

    assert response.status_code == 200
    assert "Frist nur in Dokument B" not in response.text
    assert "Keine Fristen in diesem Dokument erkannt." in response.text


def test_matter_document_view_links_to_download(
    client: TestClient, db_session: Session
) -> None:
    matter = _matter(db_session)
    document = Document(
        matter_id=matter.id, file_path="/tmp/a.pdf", original_filename="Anhang.pdf"
    )
    db_session.add(document)
    db_session.commit()

    response = client.get(f"/dashboard/matters/{matter.id}/document/{document.id}")

    assert (
        f'href="/dashboard/matters/{matter.id}/document/{document.id}/download"'
        in response.text
    )


def test_matter_document_view_states_honestly_when_no_text_was_extracted(
    client: TestClient, db_session: Session
) -> None:
    """Kein Text darf nicht wie ein leeres Dokument aussehen - der Grund
    (Texterkennung steht aus / fehlgeschlagen / Format nicht unterstuetzt)
    gehoert sichtbar in die Oberflaeche."""
    matter = _matter(db_session)
    document = Document(
        matter_id=matter.id,
        file_path="/tmp/scan.pdf",
        original_filename="Scan.pdf",
        extracted_text=None,
        ocr_status="pending",
    )
    db_session.add(document)
    db_session.commit()

    response = client.get(f"/dashboard/matters/{matter.id}/document/{document.id}")

    assert response.status_code == 200
    assert "noch nicht ausgelesen" in response.text


def _matter_csrf(client: TestClient, matter_id: str) -> str:
    page = client.get(f"/dashboard/matters/{matter_id}")
    return extract_csrf(page.text)


def test_matter_detail_page_shows_edit_form(client: TestClient, db_session: Session) -> None:
    matter = _matter(db_session, title="Alte Bezeichnung")

    response = client.get(f"/dashboard/matters/{matter.id}")

    assert response.status_code == 200
    assert "Bearbeiten" in response.text
    assert 'value="Alte Bezeichnung"' in response.text


def test_update_matter_persists_changes(client: TestClient, db_session: Session) -> None:
    matter = _matter(db_session, title="Alte Bezeichnung", reference_number="M-OLD")
    csrf = _matter_csrf(client, matter.id)

    response = client.post(
        f"/dashboard/matters/{matter.id}/update",
        data={
            "csrf_token": csrf,
            "title": "Neue Bezeichnung",
            "reference_number": "M-NEW",
            "practice_area": "Steuerrecht",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    db_session.refresh(matter)
    assert matter.title == "Neue Bezeichnung"
    assert matter.reference_number == "M-NEW"
    assert matter.practice_area == "Steuerrecht"

    event = (
        db_session.query(AuditEvent)
        .filter_by(entity_type="Matter", entity_id=matter.id, event_type="matter_updated")
        .first()
    )
    assert event is not None


def test_update_matter_with_blank_title_is_rejected(
    client: TestClient, db_session: Session
) -> None:
    matter = _matter(db_session, title="Bleibt erhalten")
    csrf = _matter_csrf(client, matter.id)

    response = client.post(
        f"/dashboard/matters/{matter.id}/update",
        data={"csrf_token": csrf, "title": "   ", "reference_number": "", "practice_area": ""},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "error=" in response.headers["location"]
    db_session.refresh(matter)
    assert matter.title == "Bleibt erhalten"


def test_update_matter_with_duplicate_reference_number_is_rejected(
    client: TestClient, db_session: Session
) -> None:
    _matter(db_session, title="Andere Akte", reference_number="M-TAKEN")
    matter = _matter(db_session, title="Diese Akte", reference_number="M-FREE")
    csrf = _matter_csrf(client, matter.id)

    response = client.post(
        f"/dashboard/matters/{matter.id}/update",
        data={
            "csrf_token": csrf,
            "title": "Diese Akte",
            "reference_number": "M-TAKEN",
            "practice_area": "",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "error=" in response.headers["location"]
    db_session.refresh(matter)
    assert matter.reference_number == "M-FREE"


def test_archive_and_reopen_matter_toggles_status(
    client: TestClient, db_session: Session
) -> None:
    matter = _matter(db_session)
    assert matter.status == "open"
    csrf = _matter_csrf(client, matter.id)

    archive_response = client.post(
        f"/dashboard/matters/{matter.id}/archive",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )
    assert archive_response.status_code == 303
    db_session.refresh(matter)
    assert matter.status == "closed"

    csrf2 = _matter_csrf(client, matter.id)
    reopen_response = client.post(
        f"/dashboard/matters/{matter.id}/reopen",
        data={"csrf_token": csrf2},
        follow_redirects=False,
    )
    assert reopen_response.status_code == 303
    db_session.refresh(matter)
    assert matter.status == "open"

    events = (
        db_session.query(AuditEvent)
        .filter_by(entity_type="Matter", entity_id=matter.id)
        .filter(AuditEvent.event_type.in_(["matter_archived", "matter_reopened"]))
        .all()
    )
    assert {e.event_type for e in events} == {"matter_archived", "matter_reopened"}


def test_update_matter_requires_a_valid_csrf_token(
    client: TestClient, db_session: Session
) -> None:
    matter = _matter(db_session, title="Bleibt erhalten")

    response = client.post(
        f"/dashboard/matters/{matter.id}/update",
        data={"csrf_token": "invalid", "title": "Geändert", "reference_number": "", "practice_area": ""},
    )

    assert response.status_code == 403
    db_session.refresh(matter)
    assert matter.title == "Bleibt erhalten"


def test_matter_detail_page_shows_audit_trail(client: TestClient, db_session: Session) -> None:
    """ECHTER FUND (18.09.): `AuditLogService.list_events_for_matter`
    existierte bereits vollstaendig, war auf der Akte-Detailseite selbst
    aber nirgends sichtbar."""
    matter = _matter(db_session)
    csrf = extract_csrf(client.get(f"/dashboard/matters/{matter.id}").text)
    client.post(
        f"/dashboard/matters/{matter.id}/update",
        data={"csrf_token": csrf, "title": "Geänderter Titel", "reference_number": "", "practice_area": ""},
    )

    response = client.get(f"/dashboard/matters/{matter.id}")

    assert "Verlauf" in response.text
    assert "matter_updated" in response.text


def test_matter_detail_audit_trail_includes_party_events(
    client: TestClient, db_session: Session
) -> None:
    """Schliesst den Kreis zum echten Fund in app/audit/service.py:
    `Party` fehlte in `_MATTER_SCOPED_MODELS` - Beteiligte-Aenderungen
    waeren sonst im aktenweiten Verlauf unsichtbar geblieben."""
    matter = _matter(db_session)
    csrf = extract_csrf(client.get(f"/dashboard/matters/{matter.id}").text)
    client.post(
        f"/dashboard/matters/{matter.id}/parties",
        data={"csrf_token": csrf, "name": "Vermieter Schmidt GmbH", "role": "Gegner", "email": "", "phone": ""},
    )

    response = client.get(f"/dashboard/matters/{matter.id}")

    assert "party_added" in response.text


def test_matter_detail_page_shows_empty_audit_trail_honestly(
    client: TestClient, db_session: Session
) -> None:
    matter = _matter(db_session)

    response = client.get(f"/dashboard/matters/{matter.id}")

    assert "Noch keine Ereignisse in dieser Akte." in response.text


def test_matter_detail_page_shows_tab_navigation(client: TestClient, db_session: Session) -> None:
    """ECHTER FUND (19.09., UI/UX-Referenzabgleich mehrerer Akte-Detail-
    Referenzbilder): die Akte-Detailseite war eine lange Einzelseite statt
    der in den Referenzen konsistent gezeigten Tab-Struktur. Alle
    Tab-Panels bleiben vollstaendig im DOM (nur per `hidden` umgeschaltet),
    damit diese und alle bereits bestehenden Tests unveraendert gueltig
    bleiben."""
    matter = _matter(db_session)

    response = client.get(f"/dashboard/matters/{matter.id}")

    for tab_name in ["uebersicht", "dokumente", "kommunikation", "aufgaben", "beteiligte", "notizen", "verlauf"]:
        assert f'data-tab="{tab_name}"' in response.text
        assert f'id="tab-{tab_name}"' in response.text


def test_matter_detail_page_shows_empty_notes_honestly(client: TestClient, db_session: Session) -> None:
    matter = _matter(db_session)

    response = client.get(f"/dashboard/matters/{matter.id}")

    assert "Noch keine Notizen in dieser Akte." in response.text


def test_create_note_persists_it_and_shows_it_on_the_matter_page(
    client: TestClient, db_session: Session
) -> None:
    """ECHTER FUND (19.09., UI/UX-Referenzabgleich, siehe app/models/note.py):
    bisher gab es projektweit keine Moeglichkeit, eine freie Notiz an einer
    Akte zu hinterlegen."""
    matter = _matter(db_session)
    csrf = extract_csrf(client.get(f"/dashboard/matters/{matter.id}").text)

    response = client.post(
        f"/dashboard/matters/{matter.id}/notes",
        data={"csrf_token": csrf, "text": "Telefonat mit Mandant: Fristverlängerung erbeten."},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"].endswith(f"/dashboard/matters/{matter.id}#notizen")

    detail = client.get(f"/dashboard/matters/{matter.id}")
    assert "Telefonat mit Mandant: Fristverlängerung erbeten." in detail.text
    assert "Noch keine Notizen in dieser Akte." not in detail.text


def test_create_note_with_blank_text_is_rejected(client: TestClient, db_session: Session) -> None:
    matter = _matter(db_session)
    csrf = extract_csrf(client.get(f"/dashboard/matters/{matter.id}").text)

    response = client.post(
        f"/dashboard/matters/{matter.id}/notes",
        data={"csrf_token": csrf, "text": "   "},
    )

    assert response.status_code == 400
    assert db_session.query(Note).count() == 0


def test_create_note_requires_a_valid_csrf_token(client: TestClient, db_session: Session) -> None:
    matter = _matter(db_session)

    response = client.post(
        f"/dashboard/matters/{matter.id}/notes",
        data={"csrf_token": "invalid", "text": "Sollte nicht gespeichert werden."},
    )

    assert response.status_code == 403
    assert db_session.query(Note).count() == 0


def test_matter_detail_audit_trail_includes_note_events(client: TestClient, db_session: Session) -> None:
    """Schliesst den Kreis zum echten Fund in app/audit/service.py:
    `Note` fehlte in `_MATTER_SCOPED_MODELS` - Notiz-Ereignisse waeren
    sonst im aktenweiten Verlauf unsichtbar geblieben (identisches Muster
    wie zuvor bei `Party`)."""
    matter = _matter(db_session)
    csrf = extract_csrf(client.get(f"/dashboard/matters/{matter.id}").text)
    client.post(
        f"/dashboard/matters/{matter.id}/notes",
        data={"csrf_token": csrf, "text": "Erste Notiz."},
    )

    response = client.get(f"/dashboard/matters/{matter.id}")

    assert "note_added" in response.text


# --- Echter Dokumentviewer (20.09., Owner-Direktive
# "PRIORITAETSERGAENZUNG: ECHTER DOKUMENTVIEWER") - echtes Seiten-
# Rendering von PDF/DOCX ueber app/documents/rendering.py (PyMuPDF), STRIKT
# getrennt von der bereits bestehenden Textextraktions-/PII-Vorschau. ---


def _real_multipage_pdf(tmp_path, pages: int = 2):
    import pymupdf

    path = tmp_path / "original.pdf"
    doc = pymupdf.open()
    for i in range(pages):
        page = doc.new_page()
        page.insert_text((72, 72), f"Testseite {i + 1}")
    doc.save(path)
    doc.close()
    return path


def test_matter_document_view_renders_real_pdf_pages_in_viewer(
    client: TestClient, db_session: Session, tmp_path
) -> None:
    """Kernanforderung der Direktive: kein Fake-Viewer, keine reine
    Textextraktion als Ersatz - das tatsaechlich gerenderte Seitenbild der
    ECHTEN gespeicherten Datei muss im HTML verlinkt sein."""
    matter = _matter(db_session)
    real_file = _real_multipage_pdf(tmp_path, pages=2)
    document = Document(
        matter_id=matter.id,
        file_path=str(real_file),
        original_filename="Steuerbescheid.pdf",
        mime_type="application/pdf",
    )
    db_session.add(document)
    db_session.commit()

    response = client.get(f"/dashboard/matters/{matter.id}/document/{document.id}")

    assert response.status_code == 200
    assert 'data-page-count="2"' in response.text
    assert f"/document/{document.id}/page/1.png?dpi=150" in response.text
    assert f"/document/{document.id}/page/2.png?dpi=45" in response.text
    assert "app_document_viewer.js" in response.text


def test_matter_document_page_image_returns_real_png_for_pdf(
    client: TestClient, db_session: Session, tmp_path
) -> None:
    matter = _matter(db_session)
    real_file = _real_multipage_pdf(tmp_path, pages=1)
    document = Document(matter_id=matter.id, file_path=str(real_file), original_filename="a.pdf")
    db_session.add(document)
    db_session.commit()

    response = client.get(
        f"/dashboard/matters/{matter.id}/document/{document.id}/page/1.png"
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_matter_document_page_image_works_for_real_docx(
    client: TestClient, db_session: Session, tmp_path
) -> None:
    from docx import Document as DocxDocument

    real_file = tmp_path / "original.docx"
    docx_document = DocxDocument()
    docx_document.add_heading("Testdokument", level=1)
    docx_document.add_paragraph("Ein Absatz.")
    docx_document.save(str(real_file))

    matter = _matter(db_session)
    document = Document(matter_id=matter.id, file_path=str(real_file), original_filename="a.docx")
    db_session.add(document)
    db_session.commit()

    response = client.get(
        f"/dashboard/matters/{matter.id}/document/{document.id}/page/1.png"
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_matter_document_page_image_refuses_a_document_from_another_matter(
    client: TestClient, db_session: Session, tmp_path
) -> None:
    """AKTENISOLATION (CLAUDE.md): identische Anforderung wie beim
    Download - eine gueltige Dokument-ID darf nicht ueber eine fremde Akte
    abrufbar sein."""
    matter_a = _matter(db_session, title="Akte A")
    matter_b = _matter(db_session, title="Akte B", reference_number="M-2")
    real_file = _real_multipage_pdf(tmp_path, pages=1)
    document = Document(matter_id=matter_b.id, file_path=str(real_file), original_filename="fremd.pdf")
    db_session.add(document)
    db_session.commit()

    response = client.get(
        f"/dashboard/matters/{matter_a.id}/document/{document.id}/page/1.png"
    )

    assert response.status_code == 404


def test_matter_document_page_image_returns_404_for_out_of_range_page(
    client: TestClient, db_session: Session, tmp_path
) -> None:
    matter = _matter(db_session)
    real_file = _real_multipage_pdf(tmp_path, pages=1)
    document = Document(matter_id=matter.id, file_path=str(real_file), original_filename="a.pdf")
    db_session.add(document)
    db_session.commit()

    response = client.get(
        f"/dashboard/matters/{matter.id}/document/{document.id}/page/5.png"
    )

    assert response.status_code == 404


def test_matter_document_view_shows_direct_image_for_image_document(
    client: TestClient, db_session: Session, tmp_path
) -> None:
    matter = _matter(db_session)
    document = Document(
        matter_id=matter.id,
        file_path=str(tmp_path / "foto.png"),
        original_filename="Foto.png",
    )
    db_session.add(document)
    db_session.commit()

    response = client.get(f"/dashboard/matters/{matter.id}/document/{document.id}")

    assert response.status_code == 200
    assert f'src="/dashboard/matters/{matter.id}/document/{document.id}/download"' in response.text


def test_matter_document_view_shows_text_content_for_txt_document(
    client: TestClient, db_session: Session
) -> None:
    matter = _matter(db_session)
    document = Document(
        matter_id=matter.id,
        file_path="/tmp/notiz.txt",
        original_filename="Notiz.txt",
        extracted_text="Ein einfacher Notiztext mit Umlauten: äöüß.",
    )
    db_session.add(document)
    db_session.commit()

    response = client.get(f"/dashboard/matters/{matter.id}/document/{document.id}")

    assert response.status_code == 200
    assert "document-viewer__text-content" in response.text
    assert "Ein einfacher Notiztext mit Umlauten" in response.text


def test_matter_document_view_shows_honest_fallback_for_unsupported_format(
    client: TestClient, db_session: Session
) -> None:
    """Kein Fake-Viewer fuer ein Format, das weder gerastert noch als Bild
    noch als Text angezeigt werden kann - ehrlicher Hinweis + echter
    Download-Link statt eines leeren/kaputten Viewers."""
    matter = _matter(db_session)
    document = Document(
        matter_id=matter.id,
        file_path="/tmp/tabelle.xlsx",
        original_filename="Tabelle.xlsx",
    )
    db_session.add(document)
    db_session.commit()

    response = client.get(f"/dashboard/matters/{matter.id}/document/{document.id}")

    assert response.status_code == 200
    assert "keine visuelle Vorschau" in response.text
    assert f'href="/dashboard/matters/{matter.id}/document/{document.id}/download"' in response.text


def test_matter_document_view_shows_honest_fallback_when_pdf_file_missing_on_disk(
    client: TestClient, db_session: Session, tmp_path
) -> None:
    """Datei laut Endung ein PDF, aber tatsaechlich nicht mehr auf der
    Platte - darf die Seite nicht zum Absturz bringen (500), sondern muss
    denselben ehrlichen Fallback zeigen wie ein sonstiges unterstuetztes
    Format."""
    matter = _matter(db_session)
    document = Document(
        matter_id=matter.id,
        file_path=str(tmp_path / "geloescht.pdf"),
        original_filename="Geloescht.pdf",
    )
    db_session.add(document)
    db_session.commit()

    response = client.get(f"/dashboard/matters/{matter.id}/document/{document.id}")

    assert response.status_code == 200
    assert "keine visuelle Vorschau" in response.text
