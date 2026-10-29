# Lizenzen, Mitarbeiterrechte, Vier-Augen-Prinzip – Build, Installation, QA (28.10.2026)

Architektur und Entscheidungen: `MODULARCHITEKTUR.md`, Abschnitte 8–11.

**Keine Freigabe für echte Mandantendaten**; keine Steuer-, GoBD-, DATEV- oder ELSTER-Freigabe; keine Übermittlung.

## 1. Bestand

- **Git:** Branch `feature/feedback-diagnose`, Ausgangs-HEAD `1f615b6` = `origin` (QA-Bericht
  `module-lizenzen-20261028/README.md` geprüft).
- **Fremde Änderungen unverändert:** `PRIVACY_REVIEW_ARCHITECTURE.md`, zwei gelöschte Logos, nicht versionierte
  `VORSCHLAG*`-Dateien und weitere fremde Evidenzordner.
- **Code-Commit:** `7ce54d7` (Rechte, Vier-Augen, Produktplanung, Doku). Dieser Ordner kommt mit einem eigenen
  Evidenz-Commit dazu.

## 2. Umgesetzte Entscheidungen

| Thema | Umsetzung |
|---|---|
| Preise (vorläufig, nicht live) | `app/modules/plans.py`: Kanzlei 99 €/Monat, Finance +149 €, Steuern +299 €; kombinierbar; keine Einrichtungsgebühr; reine Planung, nirgends als Preis angezeigt, schaltet nichts frei |
| Lizenzmodell | Module + Laufzeit schalten frei; `plan`/`term` nur informativ; neue Module, Bundles und Zeiträume ohne Formatänderung möglich |
| Produktiver Schlüssel | **nicht erzeugt** (wie vorgegeben). `TRUSTED_PUBLIC_KEYS` leer → Zusatzmodule in echten Installationen gesperrt. Anleitung zu Erzeugung und Verwahrung: `MODULARCHITEKTUR.md` §10. Im Repository liegt kein Schlüssel; Tests erzeugen gekennzeichnete Wegwerf-Schlüssel zur Laufzeit |
| Mitarbeiter in Finanzen | erfassen und offene Buchungen bearbeiten, Belege hochladen und zuordnen, Aufgaben – **nur für zugewiesene Mandanten**. Buchen, Stornieren, Konten, Stammdaten, DATEV und Zugriffsverwaltung nur Admin/Anwalt |
| Mitarbeiter in Steuern | vorbereiten (anlegen, Anlagen, Werte, zur Prüfung geben), nur zugewiesene Mandanten. Nie freigeben oder übermitteln |
| Vier-Augen-Prinzip | Freigabe nur mit `PERM_TAX_APPROVE` (Admin/Anwalt) **und** nur durch eine Person, die weder erstellt noch Teile oder Werte geändert hat (ermittelt aus dem Audit-Log). Abgelehnte Selbstfreigabe wird als `tax_approval_refused` protokolliert |
| Mandantenzuweisung | neue Tabelle `client_user_access` (Migration `schritt3_042`, ohne Vorbelegung). Deny by default; Pflege durch Admin/Anwalt unter *FiBu → Einstellungen → Mitarbeiterzugriff*; Audit `client_access_changed`. Admin/Anwalt behalten Zugriff auf alle Mandanten; das Basispaket ist unverändert |
| ELSTER/ERiC | Registrierungsablauf und Formular auf elster.de geprüft (`MODULARCHITEKTUR.md` §11). **Nicht registriert**, keine Hersteller-ID, keine ERiC-Bibliothek, keine Übermittlung. Nächste Schritte dort |

## 3. Tests

**Gezielt** (`test_staff_rights.py`, `test_modules.py`, `test_fibu.py`, `test_migrations.py`): **93 bestanden**.

Neu in `tests/test_staff_rights.py` (10 Tests):

- **Rechte:** Rechte-Matrix je Rolle; Zuweisung mit deny by default, Verwaltung nur durch Admin/Anwalt, nicht an
  Admin/Anwalt, Entzug, Audit.
- **Mitarbeiter in Finanzen, per HTTP:**
  - erlaubt: erfassen, bearbeiten, Beleg, Aufgabe;
  - 403: buchen, stornieren, `save_post`, Konto, Stammdaten, Zugriffsverwaltung, DATEV;
  - 404: fremder Mandant für Seite, Buchung und Beleg;
  - Audit-Akteur ist der Mitarbeiter.
- **Nicht zugewiesener Mitarbeiter:** kein Mandant sichtbar, Schreiben 404.
- **Zugriffsverwaltung im Einstellungs-Tab:** Anwalt pflegt den Zugriff, ungültige Zuweisung → 400.
- **Steuern:**
  - Mitarbeiter bereitet vor, zweite Person gibt frei; Mitarbeiter-Freigabe und -Übermittlung → 403;
  - Selbstfreigabe durch Anwalt bzw. Admin abgelehnt und protokolliert;
  - wer Werte geändert hat, darf nicht freigeben;
  - Mandantentrennung für Mitarbeiter.
- **Angepasst** (gewollte Verhaltensänderung): FiBu-Test für Mitarbeiter (vorher nur lesend, jetzt Zuweisung +
  Erfassen); Steuertest (vorher keine Mitarbeiterrechte); Übermittlungstest (Freigabe jetzt durch zweite Person).
- **Produktplanung:** Preise, Kombinationen, keine Einrichtungsgebühr, `plan` in der Lizenz schaltet nichts frei.

**Regression (177 Testdateien):**

- **Ergebnis:** **5 071 bestanden**, 7 xfailed, 10 übersprungen, **4 fehlgeschlagen**.
- **Abgleich mit dem Erwartungswert:** Erwartet waren 5 059. Die Differenz von +12 sind genau die neuen Tests
  (+10 `test_staff_rights`, +2 `test_modules`). Es gibt keine weiteren Abweichungen.
- **Bekannte Fehler, separat:** Die 4 Fehler sind ausschließlich die bekannten Logo-Tests in `test_design_refresh.py`:
  - `test_full_logo_lockup_assets_have_a_real_alpha_channel`
  - `test_dark_logo_icon_is_byte_identical_to_the_light_logo_icon`
  - `test_dark_logo_has_identical_overall_geometry_to_light_logo`
  - `test_dark_logo_wordmark_is_recolored_to_white`

  Ursache: Die Logo-Dateien sind im Arbeitsbaum fremd gelöscht. Sie wurden bewusst **nicht** wiederhergestellt; der
  Build verwendet sie nicht.
- **Ausführung:** im Vordergrund in Abschnitten; die langsamen Dateien (s76–s98, Schriftsatz, Security, Rechte,
  Update) dateiweise.

## 4. Quellstand-QA (`quellstand/`, isolierte Instanz, QA-Wegwerfschlüssel)

**Aufnahmen** bei 1920×1080, 1280×720 und 960×600:

| Aufnahme | Inhalt |
|---|---|
| `01` | Mitarbeiter sieht nur den zugewiesenen Mandanten |
| `02` | Buchungsformular nur mit „Als offen speichern“, ohne „Speichern und buchen“ |
| `03` | Anwalt: Mitarbeiterzugriff |
| `1280x720-05` | Mitarbeiter ohne Zuweisung: kein Mandant |
| `1280x720-06` | Lizenz-Tab |

Kein Querscrollen, keine JS-Fehler.

**HTTP-Prüfungen am laufenden Server** (`quellstand/messwerte.json`):

| Prüfung | Ergebnis |
|---|---|
| Mitarbeiter ohne Zuweisung | 404, keine Mandanten |
| Zuweisung durch Anwalt | ok; danach Mandant A 200, Mandant B 404 |
| Mitarbeiter: Buchung offen erfassen / Beleg hochladen | 200 / 200 |
| Mitarbeiter: „Speichern und buchen“, buchen, stornieren, Konto, Zugriff verwalten, DATEV | 403 |
| Mitarbeiter: Buchung bei fremdem Mandanten | 404 |
| Anwalt bucht die Mitarbeiterbuchung | 200 |

**Audit:** `fibu_booking_created` mit Akteur Mitarbeiter, `fibu_booking_posted` mit Akteur Anwalt;
`client_access_changed` „zugewiesen: qa-mitarbeiter@…“.

## 5. Sicherung, Build, Installation

**Sicherung:** `C:\Users\<Benutzer>\Lexono_backup_20261028_pre_install_module_rechte\`

- Daten 920/920 Dateien, App 1019/1019 Dateien, jeweils per SHA-256 verglichen, 0 fehlend, 0 abweichend.
- DB-Kopie bytegleich, `quick_check` ok, Schema `schritt3_037`; Installationslog liegt dort.

**Build** (`build_7ce54d7_auszug.log`):

| | |
|---|---|
| Commit | `7ce54d7` |
| Version | 0.1.0 (unverändert) |
| Zeitpunkt | 28.10.2026 21:32:01 – 21:39:17 |
| Exitcodes | PyInstaller 0, Inno Setup 0 |
| Installer | `dist\installer\Lexono_Setup.exe`, 556 795 528 B |
| SHA-256 Installer | `197C55A5F4D199227BE3C0E1BCDD256BFB4AD1BE9E55BE18109425054BF1B3A9` |
| EXE | `dist\Lexono\Lexono.exe`, 35 182 229 B |
| SHA-256 Lexono.exe (gebaut = installiert) | `28F9C043D4227FAE21BC31FCC5A0DA515CC3BD138B7EC077BB9F8B047254AA41` |
| Vorher installiert | `EC1CF455…D1796E92` (64e1300) |

**Bundle gegen HEAD:**

- App-Module: 317/317 identisch.
- Templates, Static, Migrationen: 148/148 identisch, darunter `schritt3_040`–`042`, `fibu.html`, `module_locked.html`.
- **Nicht gebündelt**, weil zur Laufzeit nicht importiert: `app.tax.service`, `app.tax.catalog`
  (keine Steuer-Oberfläche) und `app.modules.plans` (Preise nur Planung). Das Steuer-Datenmodell selbst ist enthalten.

**Installation:**

- still, Exitcode 0, 76 s, „Installation process succeeded“; installierte EXE hashgleich mit dem Build.
- „Process exit code 2147747880“ stammt vom WebView2-Bootstrapper, weil die Runtime schon vorhanden ist; bekannt aus
  früheren Installationen.

## 6. Installierte Desktop-App (`installiert/`, PrintWindow aus dem WebView2-Fenster)

**Testaufbau:** isolierte Testablage (Port 8067), synthetische Konten (Admin, Mitarbeiter) und ein synthetischer
Mandant.

**Läufe:**

- `125prozent/`: maximiert 1540×844, 1280×720, 960×600;
- `dpr1/`: 1920×1055, 1280×720.

**Ergebnisse** (`installiert/*/native.json`):

| Prüfung | Ergebnis |
|---|---|
| Migration der Testablage durch die installierte EXE | `schritt3_042`; `quick_check` ok; alle neuen Tabellen vorhanden; 0 Lizenzzeilen |
| Ausgelieferte `app.css` | byte-gleich zu HEAD |
| Lizenzstatus | „Keine Lizenz importiert“ und Hinweis „noch kein Hersteller-Prüfschlüssel hinterlegt“ |
| Modulsperre | Navigation ohne Finanzbuchhaltung; FiBu-Seite für Admin und Mitarbeiter 403 (Hinweisseite); POST 403; DATEV-Export 403 |
| Lizenz mit fremdem Schlüssel | abgelehnt („nicht mit einem bekannten Lexono-Herstellerschlüssel signiert“), FiBu bleibt 403 |
| Lizenzimport durch Mitarbeiter | 403 |
| Basispaket | Chat, Mandanten, Akten, Aufgaben, Posteingang, Kanzleiwissen: 200 |
| JS-Fehler / Tracebacks / ERROR-Zeilen im App-Log | 0 / 0 / 0 |

**Grenze dieser Prüfung:** Die installierte EXE enthält absichtlich **keinen** Prüfschlüssel; ein QA-Schlüssel lässt
sich ohne Hintertür nicht einspielen.

- Finanzbuchhaltung, Mitarbeiterrechte und Vier-Augen-Prinzip im **freigeschalteten** Zustand sind deshalb am
  **Quellstand** geprüft (Abschnitt 4 und Tests), nicht in der installierten App.
- Die installierte App zeigt den korrekt gesperrten Auslieferungszustand.

**Produktive DB** `C:\ProgramData\Lexono\data\kanzlei_ai.db`:

- SHA-256 `DFD2B763…87EFDAB4` vor der Sicherung, nach der Installation und nach allen Läufen identisch; letzte
  Änderung 26.10.2026. Es wurde nichts verändert oder gelöscht.
- Beim nächsten Start der App durch den Nutzer migriert sie von `schritt3_037` bis `schritt3_042` (nur neue Tabellen
  bzw. Spalten). Das war nicht Teil dieser QA.

## 7. Offene Punkte und Risiken

1. **Produktives Schlüsselpaar** erzeugen (Anleitung §10). Bis dahin ist kein Zusatzmodul freischaltbar, auch nicht
   für die Pilotkanzlei.
2. **Shop:** Preise sind Planung. Offen sind USt-Ausweis, Lizenzpflicht für das Basispaket, Kulanzzeit,
   automatische Lizenzausstellung und Laufzeit je Lizenzdatei (empfohlen: kurz).
3. **Kanzlei mit nur einer freigabeberechtigten Person** kann eigene Steuervorgänge nicht freigeben (gewollt); eine
   Sonderregel wäre eine fachliche Entscheidung.
4. **Mandantenzuweisung** gilt nur für die Zusatzmodule. Im Basispaket sehen Mitarbeiter weiterhin alle Mandanten.
5. **ELSTER:** Registrierung, Hersteller-ID, Prüfung der ERiC-Lizenz, Vertretungsmodell und Prototyp mit Testmerker
   stehen aus.
6. **Ohne Lizenzserver:** kein Widerruf einer Lizenz vor ihrem Ablauf.
