"""Tests für app/laws/gesetze_im_internet.py (13.09., Anbindung der
Gesetzesbibliothek an die offizielle Quelle "Gesetze im Internet",
BMJ/BfJ).

`tests/fixtures/gesetze_im_internet_bgb_sample.xml` ist ein ECHTER,
gekürzter Auszug aus der tatsächlichen offiziellen BGB-XML-Datei
(https://www.gesetze-im-internet.de/bgb/xml.zip, § 558 + § 559) - keine
erfundenen Testdaten, konsistent mit der Grundregel "Niemals
Rechtsquellen erfinden" (CLAUDE.md). Kein echter Netzwerkzugriff in
dieser Datei (siehe Moduldocstring: `fetch_law_xml_zip` bewusst nicht Teil
der automatisierten Suite)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.laws.gesetze_im_internet import (
    GesetzeImInternetError,
    SOURCE_NAME,
    build_source_url,
    extract_xml_from_zip,
    fetch_source_etag,
    import_norm_sections,
    parse_law_xml,
)
from app.models import Law, LawSection
from app.models.base import Base

_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "gesetze_im_internet_bgb_sample.xml"


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


def test_build_source_url_strips_paragraph_symbol() -> None:
    assert build_source_url("bgb", "§ 558") == "https://www.gesetze-im-internet.de/bgb/__558.html"


def test_build_source_url_handles_alphanumeric_suffix() -> None:
    """Real gegen die Live-Seite verifiziert (13.09.): auch ein
    Paragraph mit Buchstaben-Suffix wie "§ 556a" folgt demselben Muster
    (https://www.gesetze-im-internet.de/bgb/__556a.html, HTTP 200)."""
    assert build_source_url("bgb", "§ 556a") == "https://www.gesetze-im-internet.de/bgb/__556a.html"


def test_build_source_url_handles_article_based_laws() -> None:
    """ECHTER FUND (13.09., beim Import des GG entdeckt): Artikel-basierte
    Gesetze folgen einem ANDEREN Deep-Link-Schema als Paragraphen-basierte
    - real gegen die Live-Seite verifiziert (HTTP 200):
    https://www.gesetze-im-internet.de/gg/art_1.html und .../art_12a.html."""
    assert build_source_url("gg", "Art 1") == "https://www.gesetze-im-internet.de/gg/art_1.html"
    assert build_source_url("gg", "Art 12a") == "https://www.gesetze-im-internet.de/gg/art_12a.html"


def test_build_source_url_returns_none_for_entries_without_a_real_number() -> None:
    """"Eingangsformel"/"Präambel" (echte enbez-Werte im GG-XML) haben
    KEINE zitierfaehige Paragraphen-/Artikelnummer - kein erfundener Link,
    siehe CLAUDE.md "Niemals Rechtsquellen ... erfinden"."""
    assert build_source_url("gg", "Eingangsformel") is None
    assert build_source_url("gg", "Präambel") is None


def test_parse_law_xml_extracts_real_sections_from_official_sample() -> None:
    xml_bytes = _FIXTURE_PATH.read_bytes()

    sections = parse_law_xml(xml_bytes, law_code="BGB")

    assert len(sections) == 2
    first = sections[0]
    assert first.section_number == "§ 558"
    assert first.title == "Mieterhöhung bis zur ortsüblichen Vergleichsmiete"
    assert first.doknr == "BJNR001950896BJNE056206360"
    assert "ortsüblichen Vergleichsmiete" in first.text_content
    # Absatzgliederung ("(1)"/"(2)"/...) bleibt im Fliesstext erhalten -
    # keine Vermischung mehrerer Absätze zu einem einzigen Block.
    assert "(1)" in first.text_content
    assert "(6)" in first.text_content


def test_parse_law_xml_second_real_section() -> None:
    xml_bytes = _FIXTURE_PATH.read_bytes()

    sections = parse_law_xml(xml_bytes, law_code="BGB")

    assert sections[1].section_number == "§ 559"
    assert "Modernisierungsmaßnahmen" in sections[1].title


def test_parse_law_xml_rejects_invalid_xml() -> None:
    with pytest.raises(GesetzeImInternetError):
        parse_law_xml(b"<not valid xml", law_code="BGB")


_SGB12_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "gesetze_im_internet_sgb12_sample.xml"


def test_parse_law_xml_skips_non_citable_structural_blocks() -> None:
    """ECHTER FUND (14.09., beim SGB-XII-Import entdeckt): "Anlage"-
    Bloecke (Tabellenanhaenge) haben KEINE echte Paragraphen-/
    Artikelnummer in <enbez> - werden jetzt korrekt uebersprungen (waeren
    sonst ueber den Chat-Fast-Path ohnehin nie zitierbar UND wuerden bei
    identischem enbez-Text den UNIQUE-Constraint verletzen, siehe
    test_import_norm_sections_handles_duplicate_non_citable_enbez_safely
    unten fuer den real reproduzierten Fehlerfall)."""
    xml_bytes = _SGB12_FIXTURE_PATH.read_bytes()

    sections = parse_law_xml(xml_bytes, law_code="SGBXII")

    assert len(sections) == 1
    assert sections[0].section_number == "§ 1"


def test_import_norm_sections_handles_duplicate_non_citable_enbez_safely(
    db_session: Session,
) -> None:
    """Real reproduzierter Fehlerfall (14.09.): SGB XII hat ZWEI echte
    Anlagen mit identischem enbez "Anlage" (zu unterschiedlichen
    Paragraphen) - ohne den Fix in parse_law_xml wuerde der zweite Import
    einen echten UNIQUE-Constraint-Fehler (law_code, section_number)
    auslösen. Mit dem Fix werden beide Anlagen bereits beim Parsen
    übersprungen - nur § 1 wird importiert, kein Fehler."""
    xml_bytes = _SGB12_FIXTURE_PATH.read_bytes()
    sections = parse_law_xml(xml_bytes, law_code="SGBXII")

    result = import_norm_sections(
        db_session, law_code="SGBXII", law_title="Sozialgesetzbuch Zwölftes Buch",
        law_slug="sgb_12", sections=sections,
    )

    assert result.sections_created == 1
    assert db_session.query(LawSection).filter_by(law_code="SGBXII").count() == 1


_RealHttpxClient = httpx.Client


def _mock_client_factory(handler):
    """Baut einen echten `httpx.Client`, dessen Transport durch
    `httpx.MockTransport` ersetzt ist - KEIN echter Netzwerkzugriff, aber
    auch KEIN gemocktes `fetch_source_etag` selbst (die HTTP-Verarbeitung
    wird dadurch tatsächlich durchlaufen, nur die Transportschicht ist
    kontrolliert). `httpx.MockTransport` ist Teil von httpx selbst (bereits
    Projektabhängigkeit), keine neue Testabhängigkeit nötig. Nutzt bewusst
    die VOR dem Patchen gesicherte `_RealHttpxClient`-Referenz, da
    `app.laws.gesetze_im_internet.httpx` dasselbe Modulobjekt wie das hier
    importierte `httpx` ist - ein Aufruf von `httpx.Client(...)` INNERHALB
    dieser Factory würde sonst die eigene Patch-Version erneut treffen
    (Endlosrekursion)."""

    def factory(*args, **kwargs):
        return _RealHttpxClient(transport=httpx.MockTransport(handler))

    return factory


def test_fetch_source_etag_returns_the_real_header_value(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "HEAD"
        assert request.url == "https://www.gesetze-im-internet.de/bgb/xml.zip"
        return httpx.Response(200, headers={"ETag": '"abc123"'})

    monkeypatch.setattr(
        "app.laws.gesetze_im_internet.httpx.Client", _mock_client_factory(handler)
    )

    assert fetch_source_etag("bgb") == '"abc123"'


def test_fetch_source_etag_returns_none_when_source_has_no_etag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Direktive Phase B: eine Quelle ohne belastbaren Versionsmarker darf
    NIE zu einem erfundenen "unverändert" führen - das obliegt dem
    Aufrufer (`None` zurückgeben ist hier die korrekte, ehrliche
    Antwort)."""
    monkeypatch.setattr(
        "app.laws.gesetze_im_internet.httpx.Client",
        _mock_client_factory(lambda request: httpx.Response(200, headers={})),
    )

    assert fetch_source_etag("bgb") is None


def test_fetch_source_etag_retries_once_before_raising(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        raise httpx.ConnectError("boom", request=request)

    monkeypatch.setattr(
        "app.laws.gesetze_im_internet.httpx.Client", _mock_client_factory(handler)
    )
    monkeypatch.setattr("app.laws.gesetze_im_internet.time.sleep", lambda _: None)

    with pytest.raises(GesetzeImInternetError):
        fetch_source_etag("bgb", max_attempts=2)

    assert len(attempts) == 2


def test_fetch_source_etag_succeeds_on_second_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) == 1:
            raise httpx.ConnectError("boom", request=request)
        return httpx.Response(200, headers={"ETag": '"ok"'})

    monkeypatch.setattr(
        "app.laws.gesetze_im_internet.httpx.Client", _mock_client_factory(handler)
    )
    monkeypatch.setattr("app.laws.gesetze_im_internet.time.sleep", lambda _: None)

    assert fetch_source_etag("bgb", max_attempts=2) == '"ok"'
    assert len(attempts) == 2


def test_extract_xml_from_zip_rejects_zip_without_xml() -> None:
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("readme.txt", "kein XML hier")

    with pytest.raises(GesetzeImInternetError):
        extract_xml_from_zip(buffer.getvalue())


def test_import_norm_sections_creates_law_and_sections_with_real_source_metadata(
    db_session: Session,
) -> None:
    xml_bytes = _FIXTURE_PATH.read_bytes()
    sections = parse_law_xml(xml_bytes, law_code="BGB")

    result = import_norm_sections(
        db_session, law_code="BGB", law_title="Bürgerliches Gesetzbuch", law_slug="bgb", sections=sections
    )

    assert result.law_created is True
    assert result.sections_created == 2
    assert result.sections_updated == 0

    law = db_session.query(Law).filter_by(code="BGB").first()
    assert law is not None
    assert law.title == "Bürgerliches Gesetzbuch"

    section_558 = db_session.query(LawSection).filter_by(law_code="BGB", section_number="§ 558").first()
    assert section_558 is not None
    assert section_558.source_name == SOURCE_NAME
    assert section_558.doknr == "BJNR001950896BJNE056206360"
    assert section_558.source_url == "https://www.gesetze-im-internet.de/bgb/__558.html"


def test_import_norm_sections_is_idempotent_upsert(db_session: Session) -> None:
    """Ein zweiter Import (z. B. regelmäßiger Update-Lauf) aktualisiert
    bestehende Zeilen statt Duplikate zu erzeugen - gleiches Prinzip wie
    app/laws/service.py::import_law_fixture_data."""
    xml_bytes = _FIXTURE_PATH.read_bytes()
    sections = parse_law_xml(xml_bytes, law_code="BGB")

    import_norm_sections(
        db_session, law_code="BGB", law_title="Bürgerliches Gesetzbuch", law_slug="bgb", sections=sections
    )
    result = import_norm_sections(
        db_session, law_code="BGB", law_title="Bürgerliches Gesetzbuch", law_slug="bgb", sections=sections
    )

    assert result.law_created is False
    assert result.sections_created == 0
    assert result.sections_updated == 2
    assert db_session.query(LawSection).filter_by(law_code="BGB").count() == 2


def test_import_norm_sections_coexists_with_curated_fixture_rows(db_session: Session) -> None:
    """Bereits vorhandene, manuell kuratierte Zeilen (source_name-Default
    "Kuratierte Auswahl") dürfen durch einen Gesetze-im-Internet-Import
    für ANDERE Paragraphen desselben Gesetzes nicht verändert werden."""
    from datetime import date

    from app.laws.service import import_law_fixture_data

    import_law_fixture_data(
        db_session,
        {
            "code": "BGB",
            "title": "Bürgerliches Gesetzbuch",
            "sections": [
                {
                    "section_number": "§ 1",
                    "title": "Beginn der Rechtsfähigkeit",
                    "text_content": "Die Rechtsfähigkeit des Menschen beginnt mit der Vollendung der Geburt.",
                    "last_updated": "2020-01-01",
                }
            ],
        },
    )

    xml_bytes = _FIXTURE_PATH.read_bytes()
    sections = parse_law_xml(xml_bytes, law_code="BGB")
    import_norm_sections(
        db_session, law_code="BGB", law_title="Bürgerliches Gesetzbuch", law_slug="bgb", sections=sections
    )

    curated = db_session.query(LawSection).filter_by(law_code="BGB", section_number="§ 1").first()
    assert curated is not None
    assert curated.source_name == "Kuratierte Auswahl"
    assert curated.source_url is None
    assert db_session.query(LawSection).filter_by(law_code="BGB").count() == 3
