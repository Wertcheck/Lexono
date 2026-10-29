"""Modulkatalog - die einzige Stelle, an der Lexono seine Produktmodule kennt.

- **kanzlei** (Basispaket): KI-Chat, Dokumentenanalyse, Pseudonymisierung, Schriftsaetze, Mandanten/Akten,
  Kanzlei-Workflows. Immer aktiv, braucht keine Lizenz und haengt von keinem Zusatzmodul ab.
- **finanzen** (optional, kostenpflichtig): Finanzbuchhaltung (app/fibu).
- **steuern** (optional, Premium): Steuererklaerungen, Anlagen, Pruefung, Freigabe; elektronische Uebermittlung ueber
  ELSTER/ERiC ist konzipiert, aber NICHT implementiert (siehe MODULARCHITEKTUR.md).

Zusatzmodule sind voneinander unabhaengig freischaltbar (eine Steuerkanzlei kann Steuern ohne Finanzbuchhaltung
nutzen). Neue Module werden hier eingetragen; alles andere (Lizenzpruefung, Sperre, Navigation) liest diesen Katalog.
"""

from __future__ import annotations

from dataclasses import dataclass

MODULE_KANZLEI = "kanzlei"
MODULE_FINANZEN = "finanzen"
MODULE_STEUERN = "steuern"


@dataclass(frozen=True)
class Module:
    key: str
    name: str
    description: str
    licensed: bool  # True = nur mit gueltiger Lizenz nutzbar


CATALOG: dict[str, Module] = {
    MODULE_KANZLEI: Module(MODULE_KANZLEI, "Lexono Kanzlei",
                           "KI-Chat, Dokumentenanalyse, Pseudonymisierung, Schriftsätze, Mandanten- und "
                           "Aktenverwaltung, Kanzlei-Workflows", licensed=False),
    MODULE_FINANZEN: Module(MODULE_FINANZEN, "Lexono Finanzen",
                            "Finanzbuchhaltung: Buchungen, Belege, Konten, Auswertungen", licensed=True),
    MODULE_STEUERN: Module(MODULE_STEUERN, "Lexono Steuern",
                           "Steuererklärungen mit Anlagen, Prüfung und Freigabe; ELSTER/ERiC-Übermittlung in "
                           "Vorbereitung", licensed=True),
}

LICENSED_MODULES = frozenset(k for k, m in CATALOG.items() if m.licensed)
BASE_MODULES = frozenset(k for k, m in CATALOG.items() if not m.licensed)
