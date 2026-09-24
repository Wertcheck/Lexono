"""Setup-Skript: lädt EIN Gesetzeswerk als echtes, vollständiges,
strukturiertes XML von der offiziellen Quelle "Gesetze im Internet"
(BMJ/BfJ) herunter und importiert es in dieselbe digitale
Gesetzesbibliothek wie die bisherigen kuratierten Fixtures (siehe
app/laws/gesetze_im_internet.py für die Herleitung von URL-Schema/
Datenmodell-Erweiterung).

    python scripts/import_gesetze_im_internet.py bgb
    python scripts/import_gesetze_im_internet.py bgb "Bürgerliches Gesetzbuch"

Macht einen ECHTEN Netzwerkzugriff (https://www.gesetze-im-internet.de/
{slug}/xml.zip) - bewusst NICHT automatisch bei jedem App-Start/Dashboard-
Aufruf ausgeführt (anders als die reinen JSON-Fixtures), sondern ein
expliziter, manuell/administrativ ausgelöster Schritt, konsistent mit dem
Auftrag ("offizieller Download/Sync" als eigener Architekturschritt, kein
automatisches Live-Scraping bei jeder Chat-Anfrage).

Idempotent (Upsert nach law_code+section_number) - beliebig oft erneut
ausführbar, um ein Gesetzeswerk auf den aktuellen amtlichen Stand zu
bringen."""

from __future__ import annotations

import sys

from app.db.session import SessionLocal
from app.laws.gesetze_im_internet import (
    GesetzeImInternetError,
    extract_xml_from_zip,
    fetch_law_xml_zip,
    import_norm_sections,
    parse_law_xml,
)

# Bekannte, auf gesetze-im-internet.de real verifizierte Slug->Titel-
# Zuordnung fuer die haeufigsten Gesetzeswerke - ein Titel kann beim
# Aufruf jederzeit explizit ueberschrieben werden (zweites Argument),
# dies ist nur eine Komfort-Vorbelegung, keine abschliessende Liste.
_KNOWN_TITLES = {
    "bgb": "Bürgerliches Gesetzbuch",
    "stgb": "Strafgesetzbuch",
    "gg": "Grundgesetz für die Bundesrepublik Deutschland",
    "zpo": "Zivilprozessordnung",
    "ao_1977": "Abgabenordnung",
    # Erweiterung (13.09., Auftrag "AUTONOMOUS PRODUCT COMPLETION MASTER
    # DIRECTIVE" §16/§39: BGB allein ist KEINE vollstaendige
    # Gesetzesbibliothek) - alle Slugs real gegen die offizielle
    # Inhaltsuebersicht (https://www.gesetze-im-internet.de/gii-toc.xml)
    # verifiziert, keine erfundenen Zuordnungen.
    "stpo": "Strafprozessordnung",
    "hgb": "Handelsgesetzbuch",
    "inso": "Insolvenzordnung",
    "gmbhg": "Gesetz betreffend die Gesellschaften mit beschränkter Haftung",
    "rvg": "Gesetz über die Vergütung der Rechtsanwältinnen und Rechtsanwälte",
    "brao": "Bundesrechtsanwaltsordnung",
    "vwgo": "Verwaltungsgerichtsordnung",
    "vwvfg": "Verwaltungsverfahrensgesetz",
    "kschg": "Kündigungsschutzgesetz",
    "arbzg": "Arbeitszeitgesetz",
    "tzbfg": "Gesetz über Teilzeitarbeit und befristete Arbeitsverträge",
    "betrvg": "Betriebsverfassungsgesetz",
    "milog": "Gesetz zur Regelung eines allgemeinen Mindestlohns",
    "estg": "Einkommensteuergesetz",
    "ustg_1980": "Umsatzsteuergesetz",
    "kstg_1977": "Körperschaftsteuergesetz",
    "gewstg": "Gewerbesteuergesetz",
    # Erweiterung (14.09., Auftrag "SGB LEGAL KNOWLEDGE"): Sozialrecht-Kern
    # (SGB I-XII). Slugs real gegen die TOC verifiziert. ECHTER FUND dabei:
    # fuer SGB I/IV existieren ZUSAETZLICH die Slugs "sgbat"/"sgbsvvs" -
    # real per Download geprueft (14.09.): beide sind veraltete
    # VORGAENGER-Fassungen (Kuerzel "SGBAT" statt "SGB 1", ganz ueberwiegend
    # "(weggefallen)", nur 12-14 statt 94+ echte Normen) - bewusst NICHT
    # importiert, um keine "Vermischung verschiedener Fassungen" zu
    # riskieren. Fuer SGB IX existiert ebenfalls eine aeltere Fassung unter
    # dem Slug "sgb_9" (vor der Neufassung 2018) - bewusst "sgb_9_2018"
    # (die aktuelle, seit 2018 geltende Fassung) verwendet. Fuer SGB X
    # existieren zusaetzlich zwei aeltere, in Kapitel aufgeteilte Slugs
    # ("sgb_10_kap1_2" zuletzt 2023, "sgb_10_kap3" zuletzt 2013) - "sgb_10"
    # (zuletzt 2026 aktualisiert, vollstaendig) ist die aktuelle,
    # zusammengefasste Fassung und wird daher allein verwendet.
    "sgb_1": "Sozialgesetzbuch (SGB) Erstes Buch (I) - Allgemeiner Teil",
    "sgb_2": "Sozialgesetzbuch (SGB) Zweites Buch (II) - Grundsicherung für Arbeitsuchende",
    "sgb_3": "Sozialgesetzbuch (SGB) Drittes Buch (III) - Arbeitsförderung",
    "sgb_4": (
        "Sozialgesetzbuch (SGB) Viertes Buch (IV) - Gemeinsame Vorschriften "
        "für die Sozialversicherung"
    ),
    "sgb_5": "Sozialgesetzbuch (SGB) Fünftes Buch (V) - Gesetzliche Krankenversicherung",
    "sgb_6": "Sozialgesetzbuch (SGB) Sechstes Buch (VI) - Gesetzliche Rentenversicherung",
    "sgb_7": "Sozialgesetzbuch (SGB) Siebtes Buch (VII) - Gesetzliche Unfallversicherung",
    "sgb_8": "Sozialgesetzbuch (SGB) Achtes Buch (VIII) - Kinder- und Jugendhilfe",
    "sgb_9_2018": (
        "Sozialgesetzbuch (SGB) Neuntes Buch (IX) - Rehabilitation und "
        "Teilhabe von Menschen mit Behinderungen"
    ),
    "sgb_10": (
        "Sozialgesetzbuch (SGB) Zehntes Buch (X) - Sozialverwaltungsverfahren "
        "und Sozialdatenschutz"
    ),
    "sgb_11": "Sozialgesetzbuch (SGB) Elftes Buch (XI) - Soziale Pflegeversicherung",
    "sgb_12": "Sozialgesetzbuch (SGB) Zwölftes Buch (XII) - Sozialhilfe",
}

# ECHTER FUND (13.09., beim Import der priorisierten Gesetzesliste
# entdeckt): einige offizielle Slugs tragen ein Jahres-Suffix
# (z. B. "ao_1977", "ustg_1980", "kstg_1977") - naiv als `law_code` per
# `slug.upper()` uebernommen, wuerde das die im Chat erwartete natuerliche
# Abkuerzung ("AO", "USTG", "KSTG", siehe app/chat/service.py::
# _looks_like_pure_norm_question, IMMER Grossbuchstaben OHNE Jahreszahl)
# NIE treffen - der Fast Path faende diese Paragraphen dann trotz
# erfolgtem Import nie. Explizite Ausnahmeliste statt einer generischen
# "Suffix abschneiden"-Heuristik, da nicht jedes "_YYYY"-Suffix garantiert
# ein reines Jahres-Artefakt ist.
_CODE_OVERRIDES = {
    "ao_1977": "AO",
    "ustg_1980": "USTG",
    "kstg_1977": "KSTG",
    # SGB-Buecher (14.09.): der Slug enthaelt eine arabische Ziffer bzw.
    # ein Jahres-Suffix ("sgb_1", "sgb_9_2018", "sgb_10") - der gespeicherte
    # `law_code` muss aber "SGB<roemisch>" sein (reine Buchstaben, ohne
    # Leerzeichen/Ziffern), konsistent mit app/chat/service.py::
    # _normalize_law_code (dort wird "SGB I"/"SGB 1" aus einer Chat-Nachricht
    # auf genau diese Form normalisiert, bevor nachgeschlagen wird).
    "sgb_1": "SGBI",
    "sgb_2": "SGBII",
    "sgb_3": "SGBIII",
    "sgb_4": "SGBIV",
    "sgb_5": "SGBV",
    "sgb_6": "SGBVI",
    "sgb_7": "SGBVII",
    "sgb_8": "SGBVIII",
    "sgb_9_2018": "SGBIX",
    "sgb_10": "SGBX",
    "sgb_11": "SGBXI",
    "sgb_12": "SGBXII",
}


def main(argv: list[str]) -> int:
    if not argv:
        print("Nutzung: python scripts/import_gesetze_im_internet.py <slug> [titel]")
        print(f"Bekannte Slugs (Komfort-Vorbelegung des Titels): {', '.join(sorted(_KNOWN_TITLES))}")
        return 1

    slug = argv[0].strip().lower()
    law_code = _CODE_OVERRIDES.get(slug, slug.upper())
    title = argv[1] if len(argv) > 1 else _KNOWN_TITLES.get(slug)
    if not title:
        print(f"Kein bekannter Titel für Slug '{slug}' - bitte als zweites Argument angeben.")
        return 1

    print(f"Lade '{slug}' von gesetze-im-internet.de ...")
    try:
        zip_bytes = fetch_law_xml_zip(slug)
        xml_bytes = extract_xml_from_zip(zip_bytes)
        sections = parse_law_xml(xml_bytes, law_code=law_code)
    except GesetzeImInternetError as exc:
        print(f"FEHLER: {exc}")
        return 1

    if not sections:
        print(f"WARNUNG: keine zitierfähigen Einzelnormen in '{slug}' gefunden - kein Import.")
        return 1

    db = SessionLocal()
    try:
        result = import_norm_sections(
            db, law_code=law_code, law_title=title, law_slug=slug, sections=sections
        )
        status = "neu angelegt" if result.law_created else "aktualisiert"
        print(
            f"{result.law_code}: Gesetzeswerk {status}, "
            f"{result.sections_created} Paragraph(en) neu, "
            f"{result.sections_updated} aktualisiert "
            f"(Quelle: gesetze-im-internet.de, {len(sections)} Normen insgesamt geparst)."
        )
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
