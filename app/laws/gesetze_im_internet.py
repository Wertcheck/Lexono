"""Import echter, vollständiger Gesetzestexte von "Gesetze im Internet"
(gemeinsames Angebot von BMJ/BfJ) - Erweiterung der bisherigen, bewusst
kleinen, manuell kuratierten Fixture-Bibliothek (siehe app/laws/service.py)
um eine ZWEITE, ECHTE Importquelle mit denselben Modellen (`Law`/
`LawSection`, siehe app/models/law_section.py für die dafür ergänzten
Felder `source_name`/`doknr`/`source_url`).

WARUM eine offizielle Quelle statt beliebigem Scraping (ausdrücklicher
Auftrag): "Gesetze im Internet" veröffentlicht amtliche Werke (§ 5 UrhG,
urheberrechtsfrei) als STRUKTURIERTES XML nach einer öffentlich
dokumentierten DTD (gii-norm.dtd) - kein HTML-Parsing/Scraping nötig, die
Struktur (ein <norm> pro Paragraph, <jurabk>/<enbez>/<titel>/<Content>)
ist stabil und amtlich.

ECHTE, VERIFIZIERTE URL-HERLEITUNG (13.09., real gegen die Live-Seite
getestet, nicht angenommen): eine Einzelnorm ist unter
"https://www.gesetze-im-internet.de/{slug}/__{nummer}.html" erreichbar,
wobei {nummer} der alphanumerische Teil von <enbez> ohne "§ " ist (z. B.
"558" für "§ 558", "556a" für "§ 556a" - real gegen
https://www.gesetze-im-internet.de/bgb/__556a.html verifiziert, HTTP 200).
KEINE erfundene URL - wird ausschließlich aus bereits vorhandenen,
amtlichen Metadaten abgeleitet (CLAUDE.md: "Niemals Rechtsquellen ...
erfinden").

Der Download selbst (`fetch_law_xml_zip`) ist bewusst NICHT Teil der
automatisierten Testsuite (echter Netzwerkzugriff) - siehe
scripts/import_gesetze_im_internet.py für den manuell/per Setup
auslösbaren Download+Import. Das reine Parsen (`parse_law_xml`,
`import_norm_sections`) ist vollständig ohne Netzwerk testbar (siehe
tests/fixtures/gesetze_im_internet_bgb_sample.xml - ein echter, aber
gekürzter Auszug aus der tatsächlichen offiziellen BGB-XML-Datei, keine
erfundenen Testdaten)."""

from __future__ import annotations

import io
import re
import time
import zipfile
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

import httpx
from sqlalchemy.orm import Session

from app.laws.service import LawImportResult
from app.models import Law, LawSection

SOURCE_NAME = "Gesetze im Internet"

# Pause zwischen zwei Versuchen einer HEAD-Statusabfrage (03.10., "RELIABLE
# LEGAL KNOWLEDGE UPDATES" §4.3: "Begrenze Timeouts und
# Wiederholungsversuche. Implementiere keine unbegrenzten Retries.") -
# bewusst kurz UND bewusst begrenzt (siehe `fetch_source_etag`:
# `max_attempts`, Standard 2 - also hoechstens EIN Wiederholungsversuch).
_ETAG_RETRY_BACKOFF_SECONDS = 2.0

_PARAGRAPH_NUMBER_RE = re.compile(r"§\s*(\w+)")
# Artikel-basierte Gesetze (z. B. GG) statt Paragraphen-basierter (z. B.
# BGB) - ECHTER FUND (13.09., beim Import des GG entdeckt): "Eingangsformel"/
# "Präambel" haben KEINE echte Artikel-/Paragraphennummer, "Art 12a" hat
# eine. Bewusst als eigenes Muster, da das Deep-Link-Schema selbst
# unterschiedlich ist (siehe build_source_url).
_ARTICLE_NUMBER_RE = re.compile(r"Art(?:ikel)?\s*(\w+)", re.IGNORECASE)


class GesetzeImInternetError(Exception):
    """Download oder Parsing der offiziellen Quelle ist fehlgeschlagen."""


@dataclass(frozen=True)
class ParsedNormSection:
    section_number: str
    title: str
    text_content: str
    doknr: str


def build_source_url(law_slug: str, section_number: str) -> str | None:
    """Leitet die echte Deep-Link-URL zur Einzelnorm ab - NIEMALS erfunden,
    nur aus bereits vorhandenen amtlichen Angaben (Gesetzes-Slug +
    Paragraphen-/Artikelnummer) abgeleitet, beide Muster real gegen die
    Live-Seite verifiziert (13.09.):
    - Paragraphen-basierte Gesetze (z. B. BGB, "§ 558"):
      `.../{slug}/__{nummer}.html` (auch fuer alphanumerische Nummern wie
      "556a" - HTTP 200 real getestet).
    - Artikel-basierte Gesetze (z. B. GG, "Art 12a"): ANDERES Schema -
      `.../{slug}/art_{nummer}.html` (ebenfalls real getestet, inkl.
      alphanumerischer Artikelnummern wie "12a").
    Liefert None fuer Eintraege OHNE echte Paragraphen-/Artikelnummer
    (z. B. "Eingangsformel"/"Präambel" im GG) - hier gibt es keine
    zitierfaehige Einzelnorm-Deep-Link, ein erfundener Link waere falsch."""
    paragraph_match = _PARAGRAPH_NUMBER_RE.search(section_number)
    if paragraph_match:
        return f"https://www.gesetze-im-internet.de/{law_slug}/__{paragraph_match.group(1)}.html"
    article_match = _ARTICLE_NUMBER_RE.search(section_number)
    if article_match:
        return f"https://www.gesetze-im-internet.de/{law_slug}/art_{article_match.group(1)}.html"
    return None


def _extract_text(content_element: ET.Element | None) -> str:
    """Wandelt den strukturierten <Content>-Teil (mehrere <P>-Absätze,
    ggf. mit verschachtelten <DL>/<DT>/<DD>-Aufzählungen) in lesbaren
    Fließtext um - EIN Absatz pro <P>, durch Leerzeile getrennt (erhält
    die im amtlichen Text durch Absatznummern wie "(1)"/"(2)" ohnehin
    vorhandene Gliederung, statt sie durch Markup-Verlust zu vermischen).
    `itertext()` nimmt verschachtelten Text (z. B. in Aufzählungen)
    automatisch mit."""
    if content_element is None:
        return ""
    paragraphs = []
    for p in content_element.findall("P"):
        text = "".join(p.itertext()).strip()
        text = re.sub(r"\s+", " ", text)
        if text:
            paragraphs.append(text)
    return "\n\n".join(paragraphs)


def parse_law_xml(xml_bytes: bytes, *, law_code: str) -> list[ParsedNormSection]:
    """Parst eine einzelne, bereits heruntergeladene Gesetzes-XML-Datei
    (das Format, das in jeder "xml.zip" von gesetze-im-internet.de
    liegt) - liefert NUR echte Einzelnormen (mit <enbez>, d. h. einem
    referenzierbaren Paragraphen/Artikel) zurück; der einleitende
    <norm>-Block ohne <enbez> (Gesetzestitel/Präambel/EU-Richtlinien-
    Liste) wird bewusst übersprungen, da er keine zitierfähige Einzelnorm
    ist.

    ECHTER FUND (14.09., beim SGB-XII-Import entdeckt): manche Gesetze
    haben zusaetzlich zu echten Paragraphen NICHT-nummerierte
    Struktur-Bloecke mit einem <enbez>, das keine echte Paragraphen-/
    Artikelnummer ist (z. B. "Inhaltsübersicht", "Inhaltsverzeichnis",
    "Anlage" fuer Tabellen-Anhaenge) - SGB XII hat sogar ZWEI Anlagen mit
    IDENTISCHEM enbez "Anlage" (zu unterschiedlichen Paragraphen, siehe
    jeweiliges <titel>), was den UNIQUE-Constraint auf (law_code,
    section_number) real verletzte (echter, reproduzierter Fund, kein
    theoretischer Fall). Solche Bloecke sind ohnehin NIE ueber den
    Chat-Fast-Path zitierbar (`_ENBEZ_SECTION_NUMBER_RE` in
    app/chat/service.py verlangt ein echtes "§"/"Art"-Token) - werden
    daher jetzt konsequent bereits hier uebersprungen, nicht nur bei der
    URL-Ableitung (`build_source_url` liefert fuer sie weiterhin `None`,
    falls sie ueber einen anderen Weg doch einmal ankommen sollten)."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise GesetzeImInternetError(f"XML von '{law_code}' nicht parsbar: {exc}") from exc

    results: list[ParsedNormSection] = []
    for norm in root.findall("norm"):
        enbez = norm.findtext("./metadaten/enbez")
        if not enbez or not enbez.strip():
            continue
        if not (_PARAGRAPH_NUMBER_RE.search(enbez) or _ARTICLE_NUMBER_RE.search(enbez)):
            continue
        titel = norm.findtext("./metadaten/titel") or ""
        doknr = norm.get("doknr", "")
        content = norm.find("./textdaten/text/Content")
        text_content = _extract_text(content)
        if not text_content:
            continue
        results.append(
            ParsedNormSection(
                section_number=enbez.strip(),
                title=titel.strip(),
                text_content=text_content,
                doknr=doknr,
            )
        )
    return results


def import_norm_sections(
    db: Session,
    *,
    law_code: str,
    law_title: str,
    law_slug: str,
    sections: list[ParsedNormSection],
    stand: date | None = None,
) -> LawImportResult:
    """UPSERT-Import wie `app/laws/service.py::import_law_fixture_data`,
    aber für real geparste Normen aus der offiziellen Quelle statt aus
    einer manuellen JSON-Fixture - dieselben Ziel-Tabellen (`Law`/
    `LawSection`), damit `law_library.html`/`LegalResearchService`
    (siehe app/research/service.py) ohne Sonderfall funktionieren.
    `source_name`/`doknr`/`source_url` markieren diese Zeilen als aus der
    offiziellen Quelle stammend (nicht kuratiert)."""
    law = db.query(Law).filter_by(code=law_code).first()
    law_created = False
    if law is None:
        law = Law(code=law_code, title=law_title)
        db.add(law)
        db.flush()
        law_created = True
    else:
        law.title = law_title

    effective_stand = stand or date.today()
    sections_created = 0
    sections_updated = 0
    for section in sections:
        existing = (
            db.query(LawSection)
            .filter_by(law_code=law_code, section_number=section.section_number)
            .first()
        )
        source_url = build_source_url(law_slug, section.section_number)
        if existing is None:
            db.add(
                LawSection(
                    law_code=law_code,
                    section_number=section.section_number,
                    title=section.title,
                    text_content=section.text_content,
                    last_updated=effective_stand,
                    source_name=SOURCE_NAME,
                    doknr=section.doknr,
                    source_url=source_url,
                )
            )
            sections_created += 1
        else:
            existing.title = section.title
            existing.text_content = section.text_content
            existing.last_updated = effective_stand
            existing.source_name = SOURCE_NAME
            existing.doknr = section.doknr
            existing.source_url = source_url
            sections_updated += 1

    db.commit()
    return LawImportResult(
        law_code=law_code,
        law_created=law_created,
        sections_created=sections_created,
        sections_updated=sections_updated,
    )


def fetch_law_xml_zip(
    law_slug: str,
    *,
    timeout_seconds: float = 30.0,
    on_progress: Callable[[int, int | None], None] | None = None,
) -> bytes:
    """Lädt die echte "xml.zip" für ein Gesetzeswerk herunter (z. B.
    slug="bgb") - bewusst NICHT automatisiert Teil der Testsuite (echter
    Netzwerkzugriff), siehe scripts/import_gesetze_im_internet.py für den
    tatsächlichen Aufrufer.

    `on_progress` (26.09., Owner-Direktive "KANZLEIWISSEN FINAL PRODUCT
    IMPLEMENTATION" §14: "Der Fortschritt muss aus dem realen Download
    stammen ... Keine künstliche Animation über eine feste Zeit.") wird
    nach jedem empfangenen Chunk mit (bereits_empfangene_bytes,
    gesamt_bytes_oder_None) aufgerufen - `total` ist `None`, wenn der
    Server keinen `Content-Length`-Header liefert (dann kann die Web-UI
    nur "wird heruntergeladen…" ohne Prozentzahl anzeigen, statt eine
    Prozentzahl zu erfinden). Optional (Standard `None`), damit bestehende
    Aufrufer (CLI-Skript) unveraendert funktionieren."""
    url = f"https://www.gesetze-im-internet.de/{law_slug}/xml.zip"
    try:
        with httpx.stream("GET", url, timeout=timeout_seconds, follow_redirects=True) as response:
            response.raise_for_status()
            total = response.headers.get("content-length")
            total_bytes = int(total) if total is not None and total.isdigit() else None
            chunks: list[bytes] = []
            received = 0
            for chunk in response.iter_bytes():
                chunks.append(chunk)
                received += len(chunk)
                if on_progress is not None:
                    on_progress(received, total_bytes)
    except httpx.HTTPError as exc:
        raise GesetzeImInternetError(
            f"Download von '{url}' fehlgeschlagen: {type(exc).__name__}"
        ) from exc
    return b"".join(chunks)


def fetch_source_etag(
    law_slug: str,
    *,
    timeout_seconds: float = 15.0,
    max_attempts: int = 2,
) -> str | None:
    """Fragt NUR den HTTP-Header der offiziellen "xml.zip" per HEAD ab -
    laedt NICHT den eigentlichen Gesetzesinhalt (03.10., Owner-Direktive
    "RELIABLE LEGAL KNOWLEDGE UPDATES" §Phase B: "Kann eine Änderung
    effizient erkannt werden, ohne jedes Mal alle Inhalte unnötig
    herunterzuladen?").

    ECHT VERIFIZIERT (03.10., real gegen die Live-Quelle getestet, nicht
    angenommen): gesetze-im-internet.de liefert auf `HEAD .../xml.zip`
    einen echten, starken `ETag` (z. B. `"72156-65c6835fce6bb"`) sowie
    `Last-Modified`/`Content-Length` und unterstuetzt bedingte GET-
    Anfragen (`If-None-Match` -> HTTP 304 bei unverändertem Inhalt, real
    getestet). `robots.txt` der Domain erlaubt automatisierten Zugriff
    uneingeschraenkt (`User-agent: *` / `Disallow:` leer).

    Liefert `None`, wenn die Antwort KEINEN `ETag`-Header enthaelt - der
    Aufrufer (app/laws/install_service.py::check_law_for_update) MUSS das
    als "Aenderung nicht ueberpruefbar" behandeln, NIEMALS als
    "unveraendert" (Direktive: "Eine fehlgeschlagene Pruefung darf niemals
    als 'keine Aenderungen vorhanden' ausgegeben werden.").

    Bewusst kleiner, begrenzter Retry (Standard: ein Wiederholungsversuch)
    fuer kurzzeitige Netzwerkaussetzer - KEIN unbegrenzter Retry."""
    url = f"https://www.gesetze-im-internet.de/{law_slug}/xml.zip"
    last_exc: httpx.HTTPError | None = None
    for attempt in range(max_attempts):
        try:
            with httpx.Client(timeout=timeout_seconds, follow_redirects=True) as client:
                response = client.head(url)
                response.raise_for_status()
            return response.headers.get("etag")
        except httpx.HTTPError as exc:
            last_exc = exc
            if attempt + 1 < max_attempts:
                time.sleep(_ETAG_RETRY_BACKOFF_SECONDS)
    raise GesetzeImInternetError(
        f"Status-Abfrage von '{url}' fehlgeschlagen: {type(last_exc).__name__}"
    ) from last_exc


def extract_xml_from_zip(zip_bytes: bytes) -> bytes:
    """Entpackt die (genau eine) XML-Datei aus der heruntergeladenen ZIP -
    gesetze-im-internet.de liefert je Gesetzeswerk immer genau eine
    XML-Datei pro ZIP."""
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
        xml_names = [name for name in archive.namelist() if name.lower().endswith(".xml")]
        if not xml_names:
            raise GesetzeImInternetError("ZIP enthält keine XML-Datei")
        return archive.read(xml_names[0])
