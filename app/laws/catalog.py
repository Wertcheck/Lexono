"""Katalog der bei "Gesetze im Internet" (BMJ/BfJ) real verfügbaren
Gesetzeswerke (26.09., Owner-Direktive "KANZLEIWISSEN FINAL PRODUCT
IMPLEMENTATION" §3/§6/§7).

Das ist der "Server-Katalog" im Sinne der Direktive - KEIN Lexono-eigener
Distributionsserver (den gibt es nicht und wird hier nicht erfunden),
sondern die bereits bestehende, real gegen die offizielle Inhaltsübersicht
(https://www.gesetze-im-internet.de/gii-toc.xml) verifizierte Slug/Titel-
Zuordnung, die bisher NUR `scripts/import_gesetze_im_internet.py` kannte.
Hierher verschoben, damit `app/web/laws_router.py` (Kanzleiwissen-UI) und
das CLI-Skript DIESELBE Liste verwenden - kein zweiter, abweichender
Katalog (Direktive §32).

`code` ist der lokale `Law.code` (Chat-Fast-Path-kompatibel, siehe
app/chat/service.py::_normalize_law_code), `slug` der amtliche Pfad-
Bestandteil bei gesetze-im-internet.de, `title` der amtliche Titel.
"Installiert" bedeutet: ein `Law`-Datensatz mit diesem `code` existiert
bereits lokal (siehe app/laws/service.py::get_installed_law_codes) - das
ist vollständig unabhängig von dieser Liste hier, die nur beschreibt, WAS
grundsätzlich abrufbar wäre."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CatalogEntry:
    code: str
    slug: str
    title: str


# Slug -> Titel (Komfort-Vorbelegung, siehe scripts/import_gesetze_im_internet.py
# für die urspruengliche Herkunft/Historie dieser Liste).
KNOWN_TITLES: dict[str, str] = {
    "bgb": "Bürgerliches Gesetzbuch",
    "stgb": "Strafgesetzbuch",
    "gg": "Grundgesetz für die Bundesrepublik Deutschland",
    "zpo": "Zivilprozessordnung",
    "ao_1977": "Abgabenordnung",
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
    # Erweiterung (26.09., Owner-Direktive "KANZLEIWISSEN FINAL PRODUCT
    # IMPLEMENTATION"): zwei weitere, real gegen die offizielle Quelle
    # verifizierte Gesetzeswerke (HTTP 200 auf .../xml.zip, echter
    # Test-Download gegen die Live-Seite durchgefuehrt, 26.09.) - bewusst
    # als ECHTE, noch nicht importierte Katalogeintraege gewaehlt, damit
    # der "nicht installiert -> Download -> installiert"-Fluss der neuen
    # Kanzleiwissen-Seite an echten, bisher ungenutzten Daten (nicht nur
    # in Tests mit gemocktem Download) durchlaufen werden kann.
    "urhg": "Gesetz über Urheberrecht und verwandte Schutzrechte",
    "bdsg_2018": "Bundesdatenschutzgesetz",
}

# Slug -> abweichender `Law.code` (siehe app/models/law_section.py-
# Moduldocstring und app/chat/service.py::_normalize_law_code fuer den
# Grund: der Fast-Path erwartet ein reines Grossbuchstaben-Kuerzel ohne
# Jahres-/Ziffern-Suffix aus dem Slug).
CODE_OVERRIDES: dict[str, str] = {
    "ao_1977": "AO",
    "ustg_1980": "USTG",
    "kstg_1977": "KSTG",
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
    "bdsg_2018": "BDSG",
}


def _code_for_slug(slug: str) -> str:
    return CODE_OVERRIDES.get(slug, slug.upper())


def get_catalog() -> list[CatalogEntry]:
    """Der vollständige, real verifizierte Katalog - sortiert nach Titel
    (dieselbe Reihenfolge, die ein Anwalt in der Kanzleiwissen-Tabelle
    erwarten würde, nicht nach dem technischen Slug)."""
    return sorted(
        (
            CatalogEntry(code=_code_for_slug(slug), slug=slug, title=title)
            for slug, title in KNOWN_TITLES.items()
        ),
        key=lambda entry: entry.title,
    )


def get_catalog_entry_for_code(code: str) -> CatalogEntry | None:
    for entry in get_catalog():
        if entry.code == code:
            return entry
    return None
