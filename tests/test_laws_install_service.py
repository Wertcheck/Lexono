"""Tests für app/laws/install_service.py (26.09., Owner-Direktive
"KANZLEIWISSEN FINAL PRODUCT IMPLEMENTATION") - die Hintergrund-
Installations-Orchestrierung hinter dem Kanzleiwissen-Toggle.

KEIN echter Netzwerkzugriff hier (`fetch_law_xml_zip` wird gemockt, exakt
wie bei den bestehenden app/laws/gesetze_im_internet.py-Tests) - die reale
Download-/Parse-Logik selbst ist dort bereits getestet, hier geht es um
die Orchestrierung (Fortschritt, Statuswechsel, Fehlerbehandlung,
Hintergrund-Thread) drumherum."""

from __future__ import annotations

import time
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.laws.install_service as install_service
from app.laws.gesetze_im_internet import GesetzeImInternetError, ParsedNormSection
from app.laws.install_service import (
    CHECK_FAILED,
    CHECK_UNCHANGED,
    CHECK_UNREACHABLE,
    CHECK_UPDATE_AVAILABLE,
    CHECK_UPDATED,
    STATUS_DOWNLOADING,
    STATUS_ERROR,
    InstallProgress,
    check_law_for_update,
    get_progress,
    is_install_running,
    start_install,
)
from app.models import Law, LawSection
from app.models.base import Base

_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "gesetze_im_internet_bgb_sample.xml"


def _real_bgb_zip_bytes() -> bytes:
    """Verpackt die echte, bereits vorhandene BGB-Test-XML (§ 558/§ 559,
    ein realer, gekürzter Auszug - siehe test_laws_gesetze_im_internet.py)
    als ZIP, damit `_run_install` sie wie eine echte Serverantwort
    verarbeiten kann."""
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("bgb.xml", _FIXTURE_PATH.read_bytes())
    return buffer.getvalue()


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


@pytest.fixture(autouse=True)
def _use_test_session_local(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    """`_run_install` läuft in einem Hintergrund-Thread und öffnet dort
    bewusst eine EIGENE Session via `SessionLocal()` (siehe Moduldocstring)
    - für den Test wird das auf dieselbe In-Memory-DB umgeleitet, sonst
    würde der Thread gegen die echte, in Tests nie gewünschte Produktions-
    DB schreiben."""
    monkeypatch.setattr(install_service, "SessionLocal", lambda: db_session)


@pytest.fixture(autouse=True)
def _clear_progress_state() -> None:
    """Der Fortschritt lebt in einem Modul-globalen Dict (siehe
    Moduldocstring: bewusst kein DB-Tabelle) - zwischen Tests zurücksetzen,
    sonst könnte ein Test vom Zustand eines vorherigen beeinflusst werden."""
    install_service._progress.clear()
    yield
    install_service._progress.clear()


def _wait_until_terminal(law_code: str, *, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not is_install_running(law_code):
            return
        time.sleep(0.02)
    raise AssertionError(f"Installation von {law_code!r} wurde nicht rechtzeitig terminal.")


def test_start_install_returns_false_for_unknown_catalog_code() -> None:
    assert start_install("DEFINITIV_KEIN_GESETZ") is False


def test_successful_install_creates_real_law_and_sections(db_session: Session) -> None:
    with patch(
        "app.laws.install_service.fetch_law_xml_zip",
        return_value=_real_bgb_zip_bytes(),
    ):
        assert start_install("BGB") is True
        _wait_until_terminal("BGB")

    law = db_session.query(Law).filter_by(code="BGB").first()
    assert law is not None
    assert law.is_active is True
    assert law.source_size_bytes == len(_real_bgb_zip_bytes())
    assert len(law.sections) == 2
    # Nach erfolgreichem Abschluss gibt es keinen Zwischen-Fortschritt mehr
    # (Direktive §13: die DB-Zeile selbst ist ab jetzt die Wahrheit).
    assert get_progress("BGB") is None


def test_install_reports_real_download_progress(db_session: Session) -> None:
    """Direktive §14: "Der Fortschritt muss aus dem realen Download
    stammen." - `on_progress` wird tatsächlich mit realen Byte-Zahlen
    aufgerufen, nicht mit einer erfundenen Zeitanimation."""
    zip_bytes = _real_bgb_zip_bytes()
    seen_progress: list[tuple[int, int | None]] = []

    def fake_fetch(slug: str, *, on_progress=None, **kwargs):
        if on_progress is not None:
            on_progress(len(zip_bytes) // 2, len(zip_bytes))
            seen_progress.append((len(zip_bytes) // 2, len(zip_bytes)))
        return zip_bytes

    with patch("app.laws.install_service.fetch_law_xml_zip", side_effect=fake_fetch):
        start_install("STGB")
        _wait_until_terminal("STGB")

    assert seen_progress == [(len(zip_bytes) // 2, len(zip_bytes))]


def test_failed_download_sets_error_status_and_does_not_create_law(db_session: Session) -> None:
    """Direktive §6/§15: "Die Anwendung darf niemals behaupten
    'Installiert', wenn tatsächlich nichts installiert wurde." + "Wenn
    Download scheitert -> nicht 'Installiert'."."""
    with patch(
        "app.laws.install_service.fetch_law_xml_zip",
        side_effect=GesetzeImInternetError("Download fehlgeschlagen: ConnectError"),
    ):
        start_install("HGB")
        _wait_until_terminal("HGB")

    assert db_session.query(Law).filter_by(code="HGB").first() is None
    progress = get_progress("HGB")
    assert progress is not None
    assert progress.status == STATUS_ERROR
    assert "fehlgeschlagen" in (progress.error_message or "")


def test_retry_after_error_can_succeed(db_session: Session) -> None:
    """Direktive §15: "Mögliche Aktion: Erneut versuchen." - ein
    fehlgeschlagener Versuch darf einen ZWEITEN, erfolgreichen Versuch für
    denselben Code nicht blockieren."""
    with patch(
        "app.laws.install_service.fetch_law_xml_zip",
        side_effect=GesetzeImInternetError("Download fehlgeschlagen: TimeoutError"),
    ):
        start_install("ZPO")
        _wait_until_terminal("ZPO")
    assert get_progress("ZPO").status == STATUS_ERROR

    with patch(
        "app.laws.install_service.fetch_law_xml_zip",
        return_value=_real_bgb_zip_bytes(),
    ):
        start_install("ZPO")
        _wait_until_terminal("ZPO")

    law = db_session.query(Law).filter_by(code="ZPO").first()
    assert law is not None
    assert law.is_active is True
    assert get_progress("ZPO") is None


def test_is_install_running_true_only_while_downloading_or_installing(db_session: Session) -> None:
    assert is_install_running("BGB") is False
    with patch(
        "app.laws.install_service.fetch_law_xml_zip",
        return_value=_real_bgb_zip_bytes(),
    ):
        start_install("BGB")
        _wait_until_terminal("BGB")
    assert is_install_running("BGB") is False


# --- Automatisierte Aktualisierung (03.10., Owner-Direktive "RELIABLE
# LEGAL KNOWLEDGE UPDATES") - `check_law_for_update` (leichtgewichtige
# ETag-Pruefung) und die um Validierung/Nachvollziehbarkeit erweiterte
# `_run_install` (echte Uebernahme).


def _install_bgb_with_etag(db_session: Session, etag: str = '"v1"') -> Law:
    """Installiert BGB real (echte Beispiel-XML, echter `_run_install`-
    Durchlauf) und setzt anschliessend einen bekannten Baseline-ETag -
    simuliert den Zustand "bereits installiert, Version bekannt", ohne die
    interne Importlogik zu duplizieren."""
    with patch(
        "app.laws.install_service.fetch_law_xml_zip", return_value=_real_bgb_zip_bytes()
    ), patch("app.laws.install_service.fetch_source_etag", return_value=etag):
        start_install("BGB")
        _wait_until_terminal("BGB")
    law = db_session.query(Law).filter_by(code="BGB").first()
    assert law is not None
    assert law.source_etag == etag
    return law


def test_check_for_update_returns_unreachable_for_an_uninstalled_law(db_session: Session) -> None:
    result = check_law_for_update(db_session, "BGB")
    assert result.status == CHECK_UNREACHABLE


def test_check_for_update_returns_unreachable_for_an_unknown_catalog_code(
    db_session: Session,
) -> None:
    law = Law(code="UNBEKANNT", title="Kein Katalogeintrag")
    db_session.add(law)
    db_session.commit()

    result = check_law_for_update(db_session, "UNBEKANNT")

    assert result.status == CHECK_UNREACHABLE


def test_check_for_update_returns_unreachable_while_install_is_running(
    db_session: Session,
) -> None:
    """Direktive §4.3: "Verhindere konkurrierende Update-Läufe für
    dasselbe Gesetz" - eine Pruefung waehrend eines bereits laufenden
    Downloads/Updates desselben Gesetzes darf weder den Lauf stoeren noch
    `last_checked_at` veraendern."""
    _install_bgb_with_etag(db_session)
    install_service._set_progress("BGB", InstallProgress(status=STATUS_DOWNLOADING))

    result = check_law_for_update(db_session, "BGB")

    assert result.status == CHECK_UNREACHABLE
    install_service._set_progress("BGB", None)


def test_check_for_update_returns_unreachable_when_source_has_no_etag(
    db_session: Session,
) -> None:
    _install_bgb_with_etag(db_session)

    with patch("app.laws.install_service.fetch_source_etag", return_value=None):
        result = check_law_for_update(db_session, "BGB")

    assert result.status == CHECK_UNREACHABLE
    law = db_session.query(Law).filter_by(code="BGB").first()
    assert law.last_check_status == CHECK_UNREACHABLE
    assert law.last_checked_at is not None


def test_check_for_update_returns_unreachable_on_network_error_not_unchanged(
    db_session: Session,
) -> None:
    """Direktive Phase B: "Eine fehlgeschlagene Prüfung darf niemals als
    'keine Änderungen vorhanden' ausgegeben werden." """
    _install_bgb_with_etag(db_session)

    with patch(
        "app.laws.install_service.fetch_source_etag",
        side_effect=GesetzeImInternetError("Status-Abfrage fehlgeschlagen: ConnectError"),
    ):
        result = check_law_for_update(db_session, "BGB")

    assert result.status == CHECK_UNREACHABLE
    assert result.status != CHECK_UNCHANGED
    law = db_session.query(Law).filter_by(code="BGB").first()
    assert law.last_check_status == CHECK_UNREACHABLE
    assert "fehlgeschlagen" in (law.last_check_error or "")


def test_check_for_update_returns_unchanged_when_etag_matches(db_session: Session) -> None:
    _install_bgb_with_etag(db_session, etag='"same"')

    with patch("app.laws.install_service.fetch_source_etag", return_value='"same"'):
        result = check_law_for_update(db_session, "BGB")

    assert result.status == CHECK_UNCHANGED
    law = db_session.query(Law).filter_by(code="BGB").first()
    assert law.last_check_status == CHECK_UNCHANGED
    assert law.last_check_error is None


def test_check_for_update_returns_update_available_when_etag_differs(
    db_session: Session,
) -> None:
    _install_bgb_with_etag(db_session, etag='"old"')

    with patch("app.laws.install_service.fetch_source_etag", return_value='"new"'):
        result = check_law_for_update(db_session, "BGB")

    assert result.status == CHECK_UPDATE_AVAILABLE
    law = db_session.query(Law).filter_by(code="BGB").first()
    assert law.last_check_status == CHECK_UPDATE_AVAILABLE


def test_successful_update_records_etag_and_last_source_update_timestamp(
    db_session: Session,
) -> None:
    """Direktive §4.4: nachvollziehbare Zeitpunkte fuer die letzte
    PRUEFUNG und die letzte ERFOLGREICHE inhaltliche Aktualisierung."""
    law = _install_bgb_with_etag(db_session, etag='"old"')
    first_update_timestamp = law.last_source_update_at
    assert first_update_timestamp is not None

    with patch(
        "app.laws.install_service.fetch_law_xml_zip", return_value=_real_bgb_zip_bytes()
    ), patch("app.laws.install_service.fetch_source_etag", return_value='"new"'):
        start_install("BGB")
        _wait_until_terminal("BGB")

    law = db_session.query(Law).filter_by(code="BGB").first()
    assert law.source_etag == '"new"'
    assert law.last_check_status == CHECK_UPDATED
    assert law.last_check_error is None
    assert law.last_source_update_at is not None
    assert law.last_source_update_at >= first_update_timestamp


def test_update_with_duplicate_section_numbers_is_rejected_and_old_sections_survive(
    db_session: Session,
) -> None:
    """Direktive §4.2/§4.3: eine unplausible neue Fassung darf den
    produktiven Bestand nicht ersetzen - die zuletzt gueltige Fassung
    bleibt vollstaendig erhalten."""
    _install_bgb_with_etag(db_session)
    original_count = db_session.query(LawSection).filter_by(law_code="BGB").count()
    assert original_count == 2

    duplicate_sections = [
        ParsedNormSection(section_number="§ 1", title="A", text_content="Text A", doknr="X1"),
        ParsedNormSection(section_number="§ 1", title="B", text_content="Text B", doknr="X2"),
    ]
    with patch(
        "app.laws.install_service.parse_law_xml", return_value=duplicate_sections
    ), patch("app.laws.install_service.fetch_law_xml_zip", return_value=_real_bgb_zip_bytes()):
        start_install("BGB")
        _wait_until_terminal("BGB")

    law = db_session.query(Law).filter_by(code="BGB").first()
    assert law.last_check_status == CHECK_FAILED
    assert "mehrfach" in (law.last_check_error or "")
    assert db_session.query(LawSection).filter_by(law_code="BGB").count() == original_count
    # Die urspruenglichen Paragraphen (§ 558/§ 559) sind unveraendert da -
    # NICHT durch "§ 1" ersetzt.
    assert db_session.query(LawSection).filter_by(law_code="BGB", section_number="§ 558").first() is not None


def test_update_with_drastic_section_count_drop_is_rejected_and_old_sections_survive(
    db_session: Session,
) -> None:
    """Direktive §4.2: kein fixer Normen-Schwellenwert, sondern ein
    RELATIVER Vergleich mit dem bisherigen Bestand desselben Gesetzes -
    hier real mit einem echten, groesseren Gesetz (STGB-Fixture-Analogon:
    wir verwenden dieselbe kleine Beispiel-XML fuer die Erstinstallation,
    dann simulieren wir fuer das Update eine fast leere Antwort)."""
    _install_bgb_with_etag(db_session)
    # BGB hat nur 2 Testnormen (< _MIN_PREVIOUS_COUNT_FOR_RATIO_CHECK=5) -
    # fuer diesen Test wird der bisherige Bestand direkt auf eine
    # realistischere Groessenordnung gesetzt, um die relative Pruefung
    # tatsaechlich auszuloesen.
    for i in range(10):
        db_session.add(
            LawSection(
                law_code="BGB",
                section_number=f"§ {900 + i}",
                title=f"Zusatznorm {i}",
                text_content="Text",
                last_updated=date.today(),
            )
        )
    db_session.commit()
    original_count = db_session.query(LawSection).filter_by(law_code="BGB").count()
    assert original_count == 12

    tiny_new_sections = [
        ParsedNormSection(section_number="§ 1", title="Nur eine Norm", text_content="Text", doknr="X1"),
    ]
    with patch(
        "app.laws.install_service.parse_law_xml", return_value=tiny_new_sections
    ), patch("app.laws.install_service.fetch_law_xml_zip", return_value=_real_bgb_zip_bytes()):
        start_install("BGB")
        _wait_until_terminal("BGB")

    law = db_session.query(Law).filter_by(code="BGB").first()
    assert law.last_check_status == CHECK_FAILED
    assert "unvollständige" in (law.last_check_error or "") or "unplausible" in (law.last_check_error or "")
    assert db_session.query(LawSection).filter_by(law_code="BGB").count() == original_count


def test_failed_update_on_existing_law_preserves_sections_and_records_failed_check(
    db_session: Session,
) -> None:
    """Direktive Phase E, Fall 5: "Fehler beim Abruf: bisherige Fassung
    bleibt erhalten." - diesmal fuer ein BEREITS installiertes Gesetz
    (Update-Fall), nicht nur die Erstinstallation."""
    _install_bgb_with_etag(db_session)
    original_count = db_session.query(LawSection).filter_by(law_code="BGB").count()

    with patch(
        "app.laws.install_service.fetch_law_xml_zip",
        side_effect=GesetzeImInternetError("Download fehlgeschlagen: TimeoutError"),
    ):
        start_install("BGB")
        _wait_until_terminal("BGB")

    law = db_session.query(Law).filter_by(code="BGB").first()
    assert law is not None
    assert law.last_check_status == CHECK_FAILED
    assert "fehlgeschlagen" in (law.last_check_error or "")
    assert db_session.query(LawSection).filter_by(law_code="BGB").count() == original_count


def test_repeated_successful_install_does_not_duplicate_sections(db_session: Session) -> None:
    with patch(
        "app.laws.install_service.fetch_law_xml_zip", return_value=_real_bgb_zip_bytes()
    ), patch("app.laws.install_service.fetch_source_etag", return_value='"v1"'):
        start_install("BGB")
        _wait_until_terminal("BGB")
        start_install("BGB")
        _wait_until_terminal("BGB")

    assert db_session.query(LawSection).filter_by(law_code="BGB").count() == 2
