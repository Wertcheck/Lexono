# Modularisierung: Lexono Kanzlei, Finanzen, Steuern – zentrale Lizenzfreischaltung (28.10.2026)

Architektur, ERiC-Machbarkeit, Workflow und offene Entscheidungen stehen in `MODULARCHITEKTUR.md` im
Repository-Wurzelverzeichnis.

**Keine steuerliche, GoBD-, DATEV- oder ELSTER-Freigabe; keine Freigabe für echte Mandantendaten.**

## Was umgesetzt ist

| Bereich | Umsetzung |
|---|---|
| Modulkatalog | `app/modules/catalog.py`: `kanzlei` (Basispaket, immer aktiv), `finanzen` und `steuern` (lizenzpflichtig, voneinander unabhängig) |
| Lizenzprüfung | `app/modules/licensing.py`: signierte Lizenzdatei (Ed25519); Prüfung bei jeder Anfrage; fail-closed; die Datenbank ist keine Vertrauensquelle |
| Serverseitige Sperre | Alle `/dashboard/fibu`-Routen über `require_module("finanzen")`, für jede Rolle. Seiten: 403 mit Hinweisseite. POST, Download, Export: 403. Steuer-Service: `ensure_module("steuern")` |
| Navigation | „Finanzbuchhaltung“ nur bei freigeschaltetem Modul; allgemeine Kanzleien sehen die sechs Kernbereiche |
| Lizenz-Tab | Einstellungen → Lizenz: Modulstatus, Lizenzdaten, Import (nur Admin, CSRF), Deaktivieren (Daten bleiben erhalten), Audit |
| Herstellerwerkzeug | `scripts/lexono_license_tool.py` (`keygen`, `sign`, `verify`); `keygen` verweigert Ziele im Repository |
| Steuern | Datenmodell `app/models/tax.py` und Service `app/tax/service.py`: Vorgänge je Steuerart und Zeitraum, Anlagen (mehrfach), Feldwerte mit Typ und Herkunft, Statusmodell mit Freigabe und protokollierter Rücknahme. Übermittlung: **lehnt immer ab** (kein ERiC). Keine Oberfläche |
| Rechte | `PERM_TAX_PREPARE`, `PERM_TAX_APPROVE`, `PERM_TAX_SUBMIT`; nur Admin und Anwalt (Standard minimal), Mitarbeiter ohne Steuerrechte |
| Migration | `schritt3_041`: nur neue Tabellen (`module_licenses`, `tax_filings`, `tax_filing_parts`, `tax_field_values`, `tax_submissions`); keine Lizenz vorbelegt; FiBu-Daten bleiben unverändert |
| Abhängigkeit | `cryptography>=42` ausdrücklich in `pyproject.toml` (war bisher nur indirekt über Presidio installiert) |

**Auslieferungszustand:** `TRUSTED_PUBLIC_KEYS` ist **leer**; das produktive Schlüsselpaar erzeugt der Owner. In
echten Installationen ist deshalb **kein** Zusatzmodul freischaltbar. Das gilt auch für die bisher ungesperrte
Finanzbuchhaltung aus `ece9673`: Sie ist ab dieser Version gesperrt, bis eine gültige Lizenz importiert ist. In den
Einstellungen weist ein Hinweis darauf hin.

## QA (Quellstand, synthetische Daten, `aufnahmen/`)

**Aufbau der Testinstanz:**

- Isolierte Instanz per Migration, Headless Edge, DPR 1.
- Skripte: `modules_shots.py`, Start über `qa_serve_with_key.py`.
- **QA-Schlüssel:** Der Start trägt einen zur Laufzeit erzeugten **Wegwerf-Prüfschlüssel** in
  `TRUSTED_PUBLIC_KEYS` ein. Die App selbst hat dafür bewusst keinen Mechanismus (kein Umgebungs- oder
  Konfigurationsschalter), damit niemand eigene Lizenzen signieren kann. Ein Schlüssel wurde nicht gespeichert.

**Phase A – Basispaket ohne Lizenz** (1920×1080, 1280×720, 960×600):

| Aufnahme | Inhalt |
|---|---|
| `A1` | Navigation ohne Finanzbuchhaltung |
| `A2` | Hinweisseite „Lexono Finanzen ist nicht freigeschaltet“ (Admin mit Link zum Lizenz-Tab) |
| `A3` | Lizenz-Tab: alle Zusatzmodule „Nicht freigeschaltet“ |

**Phase B – nach Import einer QA-Lizenz für Finanzen und Steuern:**

| Aufnahme | Inhalt |
|---|---|
| `B1` | Lizenz-Tab „Freigeschaltet“ mit Lizenzdaten |
| `B2` | Navigation mit „Finanzbuchhaltung“ |
| `B3` | Finanzbuchhaltung nutzbar |
| `B4` | Mitarbeiter: Finanzen nur lesend (1280×720) |

**Phase C – nach Deaktivierung:**

| Aufnahme | Inhalt |
|---|---|
| `C1` | Navigation wieder ohne Finanzbuchhaltung |
| `C2` | Mitarbeiter: gesperrt |

**Messungen:** 21 Zustände, kein Querscrollen, keine JS-Fehler.

**HTTP-Prüfungen gegen den laufenden Server** (`aufnahmen/messwerte.json`):

| Prüfung | Ergebnis |
|---|---|
| Basispaket ohne Lizenz: Chat, Mandanten, Akten, Aufgaben, Posteingang, Kanzleiwissen | 200 |
| FiBu ohne Lizenz: Seite Admin / Seite Mitarbeiter / POST | 403 / 403 / 403 |
| Lizenz mit unbekanntem Schlüssel | abgelehnt: „nicht mit einem bekannten Lexono-Herstellerschlüssel signiert“ |
| Lizenzimport durch Mitarbeiter | 403 |
| Gültige Lizenz importieren | „Lizenz übernommen: Lexono Finanzen, Lexono Steuern“; FiBu 200, Startkonten anlegen 200 |
| Lizenz deaktivieren | FiBu wieder 403; **9 FiBu-Konten erhalten**; Audit `module_license_imported` und `module_license_removed`; Lizenzzeile inaktiv, nicht gelöscht |

**Fund aus der QA, behoben:** Am echten Server lieferte die Hinweisseite zunächst **500** statt 403.

- **Ursache:** Die Datenbanksitzung ist beim Fehlerhandler bereits geschlossen; das Nachladen von `user.role`
  scheiterte (DetachedInstanceError). Die Unit-Tests hatten das nicht erkannt, weil der TestClient die Sitzung offen
  hält.
- **Korrektur:** Die Rolle wird vor dem Sperren geladen. Der Handler fällt bei Problemen sicher auf ein schlichtes 403
  zurück, nie 500.
- **Absicherung:** Neuer Test `test_locked_page_renders_with_per_request_sessions_like_production` mit echten, je
  Anfrage geschlossenen Sitzungen. Danach lieferte der Server 403.

## Tests

- **Neu, `tests/test_modules.py`** (37 Tests):
  - **Lizenzprüfung:** gültig; ohne vertrauenswürdigen Schlüssel; 8 Manipulations- und Formatfälle (Module oder
    Laufzeit nachträglich geändert, falsche Signatur, fremde Schlüssel-ID, Format, kaputt, leer, zu groß); fremder
    Signierschlüssel; Laufzeit (abgelaufen, noch nicht gültig, unbefristet).
  - **Lizenzverwaltung:** Import, Ersetzen, Entfernen mit Audit; Neuprüfung bei jeder Abfrage, auch bei Ablauf oder
    Manipulation in der Datenbank.
  - **Herstellerwerkzeug:** verweigert den Repository-Ordner.
  - **Web:** Basispaket für alle drei Rollen ohne FiBu-Navigation; FiBu-Sperre für Seite, POST und Export;
    Anmeldung vor der Sperre; Hinweisseite mit produktionsgleichen Sitzungen; Freischaltung von Navigation und
    Routen; Import und Deaktivierung im Lizenz-Tab; Import nur durch Admin.
  - **Steuern:**
    - Modulsperre (auch Finanzen allein schaltet Steuern nicht frei); keine Steuerrechte für Mitarbeiter;
      Validierung (5 Fälle).
    - Mehrere Steuer- und Vorgangsarten (ESt, UStVA, Einspruch, GewSt, KSt, E-Bilanz); mehrfache Anlagen.
    - Werte, Freigabe-Workflow und Audit (Werte nach Freigabe gesperrt, Rücknahme nur mit Grund).
    - Übermittlung nie ausgeführt (auch „production“), keine `tax_submissions`-Zeile.
    - Mandantentrennung (Vorgang, Teil, Wert, Status, Akte, Berichtigungsbezug).
  - **Migration 040 → 041:** nur neue Tabellen; DDL unverändert; nichts freigeschaltet; FiBu-Daten erhalten;
    Downgrade.
- **Angepasst, `tests/test_fibu.py`:** Die Web-Tests aktivieren das Modul mit einer zur Laufzeit signierten
  Testlizenz (`tests/module_license_utils.py`). Alle 37 Tests bestehen.
- **Regression (176 Testdateien):** Web/UI, Chat, Datenschutz/Privacy, Pop-up, Freigabe, Migrationen, Rechte,
  Update, Einstellungen, FiBu und Module.
  - **Ergebnis:** **5 059 bestanden**, 7 xfailed, 10 übersprungen, **4 fehlgeschlagen**.
  - **Bekannte Fehler:** Die 4 Fehler sind ausschließlich die bekannten Logo-Tests in `test_design_refresh.py`. Die
    Logo-Dateien sind im Arbeitsbaum fremd gelöscht und wurden bewusst nicht wiederhergestellt.
  - **Ausführung:** im Vordergrund in Abschnitten; zwei frühere Hintergrundläufe hatte Claude Code wegen
    Speichermangels beendet. Die langsamen Blöcke 10 und 11 liefen dateiweise, `test_s80_benchmark_driver.py`
    einzeln (30 bestanden, 6 min).
  - **Keine neuen Fehler.** Der in der vorigen Runde einmal instabile Chat-Trace-Test bestand diesmal.

## Offene Punkte

1. **Produktives Lizenz-Schlüsselpaar** (Owner). Bis dahin sind Zusatzmodule in echten Installationen gesperrt.
2. **Preise und Laufzeiten**, Verlängerung, Kulanz nach Ablauf (heute harte Sperre).
3. **Mitarbeiterrechte** für Finanzen und Steuern, **Vier-Augen-Prinzip** bei der Steuerfreigabe.
4. **ELSTER:** Registrierung als Hersteller, Hersteller-ID, rechtliche Prüfung der ERiC-Lizenz, Vertretungsmodell.
   Danach ein ERiC-Prototyp mit Testmerker, zuerst für Einspruch und Fristverlängerung, dann ESt, USt und UStVA.
5. **Steuer-Oberfläche** und **Steuerberechnung** folgen erst nach fachlicher Klärung.
6. **Nicht installiert:** Die Desktop-App enthält diesen Stand erst nach Neubau und Installation. Die produktive DB
   migriert dann auf `schritt3_041`.
