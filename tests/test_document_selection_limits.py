"""§64 P2: Dokumentauswahl bei mehr als 30 bzw. mehr als 120 Dokumenten - nur fuer den AKTUELLEN Textstand freigegebene
Dokumente, keine Verdraengung aelterer freigegebener Dokumente, lokale Hinweise, die nie in die Cloud-Anfrage gelangen.
Nur synthetische Daten; Fake-Writer bzw. lokaler Aufzeichnungsserver (Loopback), keine echten Cloud-Aufrufe."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.documents import release
from app.models import ChatConversation, Client, Document, Matter, User
from app.models.document_release import DocumentRelease
from tests.test_document_release import CLEAN, UNCERTAIN, _mode
from tests.test_document_release_defaults import MARKER, _context, _many, _payload_text
from tests.test_upload_without_ai import client, db_session, recorder, writer  # noqa: F401  (Fixtures)

NOTICE_PHRASES = ("Nicht berücksichtigt", "Höchstzahl", "Suchgrenze", "Freigabe bzw. Prüfung", ".txt")


def _matter(db: Session, title: str = "Gemischte Akte") -> Matter:
    matter = Matter(client=Client(name="Synthetische Mandantin"), title=title)
    db.add(matter)
    db.flush()
    return matter


def _add(db: Session, matter: Matter, name: str, text: str, minute: int) -> Document:
    document = Document(matter_id=matter.id, file_path="x", original_filename=name, extracted_text=text,
                        ocr_status="not_needed",
                        created_at=datetime(2026, 2, 1, tzinfo=timezone.utc) + timedelta(minutes=minute))
    db.add(document)
    return document


def _manual(db: Session, document: Document) -> None:
    db.add(DocumentRelease(document_id=document.id, text_version=release.text_version_of(document.extracted_text),
                           mode_at_release="manual"))


# --- mehr als 120 Dokumente --------------------------------------------------------------------------------------


def test_released_documents_beyond_the_search_limit_are_never_used(db_session) -> None:
    matter, older, newer = _many(db_session, released=10, unreleased=150)  # 160 Dokumente, 10 aelteste freigegeben
    preparation = _context(db_session, matter)
    assert preparation.included_document_ids == []  # jenseits der Suchgrenze: ungeprueft -> nie verwendet
    assert MARKER not in preparation.sachverhalt and "Freigegebener Vermerk" not in preparation.sachverhalt
    notices = " ".join(preparation.notices)
    assert "Suchgrenze erreicht: 40 weitere(s), ältere(s) Dokument(e)" in notices  # 30 offene + 10 freigegebene
    assert "und 110 weitere" in notices


def test_released_documents_within_the_search_limit_win_over_120_newer_ones(db_session) -> None:
    matter, older, newer = _many(db_session, released=35, unreleased=100)  # 135: 100 offene neuere zuerst
    preparation = _context(db_session, matter)
    included = set(preparation.included_document_ids)
    # durchsucht: die 120 neuesten = 100 offene + 20 freigegebene -> genau diese 20, nichts Ungeprueftes
    newest_released = sorted(older, key=lambda d: d.created_at, reverse=True)[:20]
    assert included == {d.id for d in newest_released}
    notices = " ".join(preparation.notices)
    assert "Suchgrenze erreicht" in notices and "15 weitere" in notices  # 15 aeltere freigegebene ungeprueft
    assert MARKER not in preparation.sachverhalt


def test_exactly_30_released_documents_give_no_limit_notice(db_session) -> None:
    matter, older, newer = _many(db_session, released=30, unreleased=5)
    preparation = _context(db_session, matter)
    assert len(preparation.included_document_ids) == 30
    notices = " ".join(preparation.notices)
    assert "Höchstzahl" not in notices and "Suchgrenze" not in notices
    assert "nicht für die KI-Verarbeitung freigegeben" in notices  # die 5 offenen werden trotzdem benannt


# --- gemischte Freigabestatus in Modus C, fehlgeschlagene Pruefung --------------------------------------------------


def test_mode_c_uses_only_passed_or_released_documents(db_session, monkeypatch) -> None:
    _mode(db_session, release.MODE_AUTO_REVIEW)
    original = release._run_central_check

    def central_check(text, known, decisions):
        if "PRUEFFEHLER" in text:
            return release.CHECK_FAILED, 0
        return original(text, known, decisions)

    monkeypatch.setattr(release, "_run_central_check", central_check)
    matter = _matter(db_session)
    clean = [_add(db_session, matter, f"sauber_{i:02d}.txt", f"{CLEAN} Lfd. Nr. {i}.", i) for i in range(20)]
    uncertain = [_add(db_session, matter, f"auffaellig_{i:02d}.txt", f"{UNCERTAIN} {MARKER} {i}.", 100 + i)
                 for i in range(12)]
    uncertain_released = _add(db_session, matter, "auffaellig_freigegeben.txt", f"{UNCERTAIN} Freigabe-Nr. 7.", 200)
    failed = _add(db_session, matter, "fehler.txt", f"PRUEFFEHLER {MARKER}.", 300)
    db_session.commit()
    _manual(db_session, uncertain_released)  # Entscheidung getroffen: manuell freigegeben
    _manual(db_session, failed)  # Freigabe hilft nicht: fehlgeschlagene Pruefung blockiert in jedem Modus
    db_session.commit()

    preparation = _context(db_session, matter)
    included = set(preparation.included_document_ids)
    assert included == {d.id for d in clean} | {uncertain_released.id}
    assert MARKER not in preparation.sachverhalt and "PRUEFFEHLER" not in preparation.sachverhalt
    notices = " ".join(preparation.notices)
    # 13 zurueckgehalten: die 10 neuesten benannt, der Rest gezaehlt
    assert "fehler.txt" in notices and "auffaellig_11.txt" in notices and "und 3 weitere" in notices
    assert "Höchstzahl" not in notices  # 21 < 30: vollstaendig, nur Zurueckgehaltene benannt


# --- veraltete Pruefungen/Freigaben ----------------------------------------------------------------------------------


def test_a_manual_release_of_an_older_text_version_is_not_used(db_session) -> None:
    _mode(db_session, release.MODE_MANUAL)
    matter = _matter(db_session)
    current = _add(db_session, matter, "aktuell.txt", "Freigegebener Vermerk: Betrag 980,00 EUR.", 1)
    changed = _add(db_session, matter, "geaendert.txt", "Urspruenglicher Vermerk ohne Besonderheiten.", 2)
    db_session.commit()
    _manual(db_session, current)
    _manual(db_session, changed)
    db_session.commit()
    changed.extracted_text = f"Neu erkannter Text: {MARKER}."  # z. B. erneute Texterkennung nach der Freigabe
    db_session.commit()
    preparation = _context(db_session, matter)
    assert preparation.included_document_ids == [current.id]
    assert MARKER not in preparation.sachverhalt and "geaendert.txt" in " ".join(preparation.notices)


def test_a_cached_passed_check_of_an_older_text_version_is_not_used(db_session) -> None:
    _mode(db_session, release.MODE_AUTO_REVIEW)
    matter = _matter(db_session)
    document = _add(db_session, matter, "eingang.txt", CLEAN, 1)
    db_session.commit()
    assert release.check_document(db_session, document)[0] == release.CHECK_PASSED  # alter Stand: unauffaellig
    document.extracted_text = f"{UNCERTAIN} {MARKER}."
    db_session.commit()
    preparation = _context(db_session, matter)
    assert preparation.included_document_ids == [] and MARKER not in preparation.sachverhalt


def test_a_cached_check_from_an_older_context_is_not_used(db_session) -> None:
    from app.privacy.decisions import DECISION_PERSONAL, record_decision

    _mode(db_session, release.MODE_AUTO_REVIEW)
    matter = _matter(db_session)
    document = _add(db_session, matter, "eingang.txt", CLEAN, 1)
    db_session.commit()
    first_key = release._context(db_session, matter)[2]
    release.check_document(db_session, document)
    record_decision(db_session, matter_id=matter.id, conversation_id=None, term="Lindenhof-Sued",
                    decision=DECISION_PERSONAL, user_id=None, actor="qa", manual=True)
    db_session.commit()
    second_key = release._context(db_session, matter)[2]
    assert first_key != second_key  # Kontext geaendert -> das alte Ergebnis passt nicht mehr
    from app.models.document_release import DocumentPrivacyCheck

    keys = {c.context_key for c in db_session.query(DocumentPrivacyCheck).filter_by(document_id=document.id)}
    assert second_key in keys  # fuer den NEUEN Kontext geprueft (Vorab-Pruefung oder beim Versand)
    assert _context(db_session, matter).included_document_ids == [document.id]


# --- Hinweise bleiben lokal: tatsaechlich ausgehende Anfrage (Loopback) -------------------------------------------


def test_selection_notices_never_reach_the_outgoing_request(client, db_session, recorder) -> None:
    from tests.test_upload_without_ai import _ask

    matter, older, newer = _many(db_session, released=33, unreleased=4)
    admin = db_session.query(User).filter_by(email="admin@kanzlei.test").one()
    conversation = ChatConversation(matter_id=matter.id, user_id=admin.id, title="Grosse Akte")
    db_session.add(conversation)
    db_session.commit()
    preparation = _context(db_session, matter)
    assert any("Höchstzahl" in n for n in preparation.notices)  # lokal vorhanden ...
    _ask(client, conversation.id, "Welche Betraege nennen die Vermerke?")
    assert len(recorder.requests) == 1
    sent = recorder.requests[0]["raw"].decode("utf-8")
    assert "Freigegebener Vermerk" in sent  # freigegebene Inhalte gehen hinaus
    for phrase in (MARKER, "XQ9", *NOTICE_PHRASES):
        assert phrase not in sent, phrase  # ... aber nie in der ausgehenden Anfrage


def test_selection_notices_never_reach_the_drafting_payload(db_session) -> None:
    from tests.test_web_chat import FakeClaudeWritingProvider, _working_drafting_service

    matter, older, newer = _many(db_session, released=31, unreleased=3)
    writer = FakeClaudeWritingProvider("Entwurf.")
    _working_drafting_service(writer).create_draft(matter.id, "formulate_draft", db_session)
    sent = _payload_text(writer.received_payloads)
    assert sent and "Freigegebener Vermerk" in sent
    for phrase in (MARKER, "XQ9", *NOTICE_PHRASES):
        assert phrase not in sent, phrase
