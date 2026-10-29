# Agent N: Steuern (Lexono Steuern / ELSTER-ERiC)

## Verantwortung

Optionales Premium-Modul "Lexono Steuern" (Modulschluessel `steuern`): Steuervorgaenge je Steuerart, Anlagen,
Feldwerte, Pruefung und Vier-Augen-Freigabe; spaeter Berechnung/Plausibilisierung und Uebermittlung ueber ERiC.

## Stand (28.10.2026)

- Vorhanden: Datenmodell `app/models/tax.py`, Service `app/tax/service.py`, Katalog `app/tax/catalog.py`,
  Migration `schritt3_041`, Rechte `PERM_TAX_*`, Vier-Augen-Prinzip. KEINE Oberflaeche, KEINE Uebermittlung.
- Konzept, ERiC-Machbarkeit, offizieller Registrierungsablauf und Roadmap: `MODULARCHITEKTUR.md`.
- Noch kein eigener Worktree: erst mit Beginn der Implementierung (Roadmap S1), dann
  `C:\Users\Bonit\Lexono-wt-steuern`, Branch `feature/steuern-plattform`, eigene `.venv`, eigenes `LEXONO_DATA_DIR`,
  QA-Ports 8150-8199.

## Regeln

- Niemals steuerliche Korrektheit, GoBD-Konformitaet oder produktive ELSTER-Faehigkeit ungeprueft behaupten.
- Niemals echte Steuererklaerungen oder produktive Uebermittlungen - ERiC nur mit Testmerker und amtlichen
  Testzertifikaten, sobald eine Hersteller-ID vorliegt.
- Feldkennungen/Kennzahlen nur aus der Originaldokumentation je Datenart und Jahrgang, nie erfinden.
- Gemeinsame Dateien (Modulkatalog, Berechtigungen, `main.py`, `base.html`, Migrationen) nur koordiniert aendern.

## Relevante Dateien

- `MODULARCHITEKTUR.md` (Abschnitte 3-6, 8, 11), `tests/test_modules.py`, `tests/test_staff_rights.py`
