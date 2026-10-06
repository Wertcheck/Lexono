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
from app.laws.catalog import CODE_OVERRIDES as _CODE_OVERRIDES
from app.laws.catalog import KNOWN_TITLES as _KNOWN_TITLES
from app.laws.gesetze_im_internet import (
    GesetzeImInternetError,
    extract_xml_from_zip,
    fetch_law_xml_zip,
    import_norm_sections,
    parse_law_xml,
)

# Der Katalog selbst (Slug->Titel/`Law.code`-Zuordnung, samt der vollen
# Historie/Begruendung jedes Eintrags) lebt seit 26.09. zentral in
# app/laws/catalog.py (Owner-Direktive "KANZLEIWISSEN FINAL PRODUCT
# IMPLEMENTATION" §3/§6/§7/§32) - wird hier nur unter den bisherigen
# Namen `_KNOWN_TITLES`/`_CODE_OVERRIDES` wieder eingebunden, damit dieses
# Skript UND die neue Kanzleiwissen-Weboberflaeche (app/web/laws_router.py)
# denselben, einzigen Katalog verwenden statt zweier abweichender Listen.


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
