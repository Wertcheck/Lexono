# Modularchitektur: Lexono Kanzlei, Finanzen und Steuern (Stand 28.10.2026)

Gilt zusätzlich zu `ARCHITECTURE.md`. **Keine steuerliche, GoBD-, DATEV- oder ELSTER-Freigabe.** Aussagen zu ERiC
beruhen auf einer Recherche vom 28.10.2026 (Abschnitt 5) und sind vor einer Umsetzung erneut gegen die dann gültige
amtliche Dokumentation zu prüfen.

## 1. Produktstruktur

| Modul | Schlüssel | Lizenz | Inhalt |
|---|---|---|---|
| **Lexono Kanzlei** | `kanzlei` | Basispaket, immer aktiv | KI-Chat, Dokumentenanalyse, Pseudonymisierung, Schriftsätze, Mandanten und Akten, Kanzlei-Workflows |
| **Lexono Finanzen** | `finanzen` | optional, kostenpflichtig | Finanzbuchhaltung (`app/fibu`): Buchungen, Belege, Konten, Auswertungen |
| **Lexono Steuern** | `steuern` | optional, Premium | Steuervorgänge mit Anlagen, Prüfung und Freigabe (`app/tax`). Elektronische Übermittlung über ELSTER/ERiC ist **konzipiert, nicht implementiert** |

**Unabhängigkeit der Module:**

- Das Basispaket hängt von keinem Zusatzmodul ab.
- Finanzen und Steuern sind unabhängig voneinander freischaltbar. Eine Kanzlei ohne Buchhaltung kann also Steuern
  nutzen, und umgekehrt.
- Finanzdaten fließen später nur optional in Steuervorgänge: Feldwerte mit Herkunft `fibu`.

## 2. Gemeinsame Architektur

**Eine Codebasis, eine Datenbank, ein Rechtesystem.** Es gibt keine Parallelanwendung. Die Module nutzen dieselben
Bausteine:

- Mandanten (`clients`), Akten (`matters`; ein Steuervorgang kann einer Akte zugeordnet sein);
- Rollen und Rechte (`PERMISSION_MATRIX`), Audit-Log (`AuditEvent`, append-only);
- Design, Navigation, Migrationen (Alembic).

**Modulkatalog `app/modules/catalog.py`:** die einzige Stelle, die die Module kennt. Ein neues Modul wird dort
eingetragen.

**Freischaltung `app/modules/licensing.py`:**

- **Lizenzformat:** Signierte Lizenzdatei (Ed25519). Lexono enthält nur öffentliche Schlüssel
  (`TRUSTED_PUBLIC_KEYS`). Die Lizenz nennt Kanzlei, Module, Gültigkeit und Lizenz-ID.
- **Prüfung:** bei **jeder** Abfrage neu. Die Datenbank ist keine Vertrauensquelle: Eine dort manipulierte Lizenz
  wird abgelehnt.
- **Fail-closed:** Unbekannter Schlüssel, falsche Signatur, kaputtes Format, Ablauf oder ein noch nicht begonnener
  Zeitraum bedeuten jeweils: Modul gesperrt.

**Durchsetzung auf drei Ebenen**, jeweils serverseitig:

1. **Routen:** `require_module("finanzen")` als Router-Dependency auf allen `/dashboard/fibu`-Routen. Sie greift für
   jede Rolle, auch für Admins. Ohne Lizenz:
   - Seite: 403 mit Hinweisseite;
   - Formular, Download, Export: 403.
2. **Service:** `ensure_module(db, "steuern")` in jeder Steuerfunktion. Steuern hat noch keine Web-Routen.
3. **Rechte:** zusätzlich die Rollenrechte (`PERM_FIBU_MANAGE`, `PERM_TAX_PREPARE/APPROVE/SUBMIT`).

**Navigation:** „Finanzbuchhaltung“ erscheint nur bei freigeschaltetem Modul. Allgemeine Kanzleien sehen
unverändert die sechs Kernbereiche. Die Anzeige allein schützt nichts; maßgeblich sind die Ebenen 1 und 2.

**Datenerhalt:** Ablauf oder Entfernen einer Lizenz sperrt das Modul, löscht aber keine Daten. Eine erneute
Freischaltung macht alles wieder sichtbar.

**Rückwärtskompatibilität:**

- Die Migrationen `schritt3_040` (FiBu) und `schritt3_041` (Lizenzen, Steuern) legen nur neue Tabellen an.
- Bestehende Nutzer und Rollen sind unverändert. Die neuen Rechte erhalten nur Admin und Anwalt (Standard minimal).

### Lizenzablauf (Hersteller)

1. **Schlüsselpaar erzeugen:** einmalig, sicher, **außerhalb** des Repositorys:
   `python scripts/lexono_license_tool.py keygen --out-dir <sicherer Ordner>`. Mit
   `LEXONO_LICENSE_KEY_PASSWORD` wird der Schlüssel verschlüsselt.
2. **Öffentlichen Schlüssel eintragen:** Die ausgegebene Zeile kommt in `TRUSTED_PUBLIC_KEYS`. Danach ist ein neuer
   Build nötig.
3. **Lizenz signieren:** je Kanzlei
   `sign --key … --licensee "…" --modules finanzen,steuern --valid-until JJJJ-MM-TT --out kanzlei.lexono-license`.
4. **Lizenz importieren:** Der Kanzlei-Admin importiert sie unter *Einstellungen → Lizenz*. Jeder Import und jede
   Deaktivierung steht im Audit-Log.

**Stand:** `TRUSTED_PUBLIC_KEYS` ist **leer**; das Schlüsselpaar erzeugt der Owner später. Bis dahin lässt sich in
echten Installationen **kein** Zusatzmodul freischalten. Das ist gewollt und gilt auch für die Pilotkanzlei.

## 3. Datenmodell Lexono Steuern

Allgemein gehalten, nicht auf die Umsatzsteuer-Voranmeldung beschränkt (`app/models/tax.py`):

| Tabelle | Zweck |
|---|---|
| `tax_filings` | Vorgang eines Mandanten. Felder: Steuerart (Katalog `app/tax/catalog.py`), Vorgangsart (Erklärung, Anmeldung, Voranmeldung, Nachricht wie Einspruch/Fristverlängerung, Bilanz), Jahr, Zeitraum (Jahr/Quartal/Monat/keiner), optional Akte und Berichtigungsbezug, Formularversion, Status, Freigabe (wer/wann) |
| `tax_filing_parts` | Hauptvordruck, Anlagen und ergänzende Formulare, mehrfach je Art (`instance_no`) |
| `tax_field_values` | Feldwerte je Teil: Schlüssel, Typ (Text, Betrag in Cent, Ganzzahl, Datum, ja/nein), Herkunft (manuell, Finanzbuchhaltung, Import) |
| `tax_submissions` | vorbereitet für Übermittlungsversuche (Test/Produktion, Transferticket, Protokoll, Fehler); **derzeit ungenutzt** |

**Feldschlüssel** sind bewusst frei. Amtliche Kennzahlen und Feldkennungen je Datenart und Jahrgang werden erst mit
ERiC eingeführt und gegen die Dokumentation geprüft; es werden keine erfunden.

## 4. Workflow (geplant) und heutiger Umfang

| Schritt | Ziel | Heute |
|---|---|---|
| 1. Erfassung | Vorgang, Anlagen, Werte (manuell, aus FiBu, Import) | **umgesetzt** (Service, ohne Oberfläche) |
| 2. Berechnung | Steuerberechnung je Steuerart | **nicht umgesetzt**; keine eigene Steuerberechnung ohne fachliche Prüfung |
| 3. Prüfung | interne Prüfung + ERiC-Plausibilitätsprüfung (Pflicht vor jedem Versand) | Status „In Prüfung“ umgesetzt; ERiC-Prüfung nicht |
| 4. Freigabe | ausdrückliche Freigabe durch Berechtigte; danach keine Änderung ohne protokollierte Rücknahme (§ 87d AO: Daten nach Zustimmung unverändert übermitteln) | **umgesetzt** (`PERM_TAX_APPROVE`, Grundpflicht bei Rücknahme, Audit) |
| 5. Übermittlung | ERiC (Test mit Testmerker, dann Produktion), Transferticket, Fehlerbehandlung | **nicht umgesetzt**: `submit_filing` lehnt immer ab, es wird nichts übermittelt |
| 6. Dokumentation | Übermittlungsprotokoll/PDF, Audit, Aufbewahrung | Audit umgesetzt; Protokoll/PDF folgen mit ERiC |

**Statusmodell:**

- Entwurf → In Prüfung → Freigegeben → (Übermittelt → Angenommen/Abgewiesen).
- Zurück von „In Prüfung“ und „Freigegeben“ nur mit Grund; Stornieren mit Grund.
- Werte sind nur im Entwurf änderbar.

**Rechte (Standard):**

- Vorbereiten, Freigeben und Übermitteln dürfen nur Admin und Anwalt.
- Mitarbeiter haben keine Steuerrechte.
- Eine Übermittlung ist nie allein durch eine Rolle ohne ausdrückliches Recht möglich (`PERM_TAX_SUBMIT`).

## 5. ERiC/ELSTER – Machbarkeit (Recherche 28.10.2026)

**Hauptquelle:** ERiC-Entwicklerhandbuch 43.3.2.0 (© 2025 Bayerisches Landesamt für Steuern). Es ist öffentlich nur
über eine Drittseite abrufbar (instantview.org); der Entwicklerbereich auf elster.de erfordert einen Login. Angaben
mit „HB“ beziehen sich auf dieses Handbuch.

**Herstellerzugang:**

- Registrierung als Entwickler unter https://www.elster.de/elsterweb/infoseite/entwickler.
- Das BayLfSt prüft die Herstellungsabsicht; die Zugangsdaten kommen nach einigen Tagen.
- Danach Download von ERiC und Antrag auf eine **Hersteller-ID**, die je Produkt gilt.
- ERiC ist laut ELSTER kostenlos.
- Eine Zertifizierung oder Abnahme der Software: **nicht verifiziert**.
- Ob für erste Tests eine eigene ID nötig ist, ist widersprüchlich: **klären**.

**Lizenz:** `lizenz.pdf` liegt jedem ERiC-Paket bei. Nach einer Zweitquelle (Scopevisio-Hilfe, Release 41) erlaubt
sie die Integration und den Vertrieb in eigenen Produkten. Pflichten laut dieser Zweitquelle:

- Datenschutzhinweise;
- Mindestversion einhalten, eigener Endnutzer-Support;
- Logdateien nur mit Einwilligung übermitteln.

Der aktuelle Wortlaut ist **nicht verifiziert** und muss rechtlich geprüft werden.

**Technik:**

- ERiC ist eine C-Bibliothek (`ericapi.dll` plus Plugins je Datenart-Version) für Windows x64, Linux und macOS
  [HB S. 21 ff.].
- Eine Python-Anbindung über ctypes ist praktisch belegt (DigitalService „erica“), aber nicht amtlich.
- Die DLL-Signaturen dürfen nicht verändert werden [HB S. 132].
- Benötigt werden das VC++ Redistributable sowie ein beschreibbares Log-Verzeichnis.
- **Konsequenz für Lexono:** ERiC als eigenes Verzeichnis per Inno Setup installieren, nicht im PyInstaller-Paket;
  UPX und jede Nachbearbeitung der DLLs abschalten.

**Updates:**

- Zwei Releases pro Jahr (Mai und November).
- Die Mindestversion wird jeweils im April angehoben; ältere Versionen weist der Server ab [HB S. 20, 142].
- Lexono braucht deshalb einen verlässlichen jährlichen Update-Prozess. Das ist dauerhafter Wartungsaufwand.

**Verfahren:** Im Echtbetrieb werden laut [HB S. 29–32] unter anderem unterstützt:

- Erklärungen: ESt, USt, GewSt, KSt, Feststellung der Einkünfte, EÜR, Erbschaft- und Schenkungsteuer;
- Anmeldungen: UStVA, Lohnsteuer-Anmeldung;
- E-Bilanz;
- im Verfahren ElsterNachricht: Einspruch und Fristverlängerung [HB S. 348].

**Validierung:**

- Die Plausibilitätsprüfung in ERiC ist vor dem Versand Pflicht.
- Es gibt XML-Schemas je Datenart-Version.
- Für Druck und Freizeichnung erzeugt ERiC PDFs; Testfälle tragen den Stempel „Testfall“ [HB S. 104 ff., 152 f.].

**Authentifizierung und Vertretung:**

- Zertifikate: Portalzertifikat als Datei, Stick oder Karte; dazu Organisationszertifikate für Kanzleien
  [HB S. 111 ff.].
- Übermittlung für Dritte: § 87d AO, mit Identifizierung und Zustimmung des Auftraggebers.
- Vollmachten: Vollmachtsdatenbank (für Rechtsanwälte über die Kammern bzw. BRAK) oder ELSTER-Vollmacht.
- Vertretungsmodell: **fachlich zu entscheiden**.

**Test:**

- Testmerker, Teststeuernummern (Bayern) und amtliche Testzertifikate, die nur mit Testmerker gelten
  [HB S. 76, 175 ff.].
- **In Lexono-Tests: niemals echte Erklärungen, niemals Produktivübermittlung.**

**Aufwand (eigene Schätzung, nicht verifiziert):**

- ctypes-Anbindung mit Validierung, Testversand und PDF: etwa 3–6 Wochen.
- Erste Datenart mit Erfassung, Freigabe und Audit: weitere 4–8 Wochen.
- Danach laufende Pflege je Datenart und Jahrgang. Das ist der eigentliche Hauptaufwand.

## 6. Pilotkanzlei und Prioritäten

**Profil:** Steuerfachanwalt. Häufigste Themen laut Owner (28.10.2026): Einkommensteuer, Unternehmenssteuern,
Umsatzsteuer, Einspruch/Steuerstreit.

**Abgeleitete Reihenfolge** (`pilot_priority` im Katalog). Sie steuert nur die Reihenfolge der Umsetzung; das
Basispaket bleibt allgemein.

1. **Einspruch und Fristverlängerung** (ElsterNachricht):
   - Passt direkt zur anwaltlichen Arbeit im bestehenden Basispaket: Akten, Fristen, Schriftsätze.
   - Datenarmes Verfahren und daher ein guter erster ERiC-Durchstich.
   - Hinweistexte müssen angezeigt werden [HB S. 348].
2. **ESt, USt und UStVA:** häufige Verfahren der Pilotkanzlei; Werte aus Lexono Finanzen lassen sich nutzen
   (UStVA aus Buchungen).
3. **GewSt, KSt und Feststellung**, danach **E-Bilanz** und Lohnsteuer-Anmeldung.

## 7. Offene Entscheidungen und Risiken

| Thema | Status |
|---|---|
| Produktives Lizenz-Schlüsselpaar | Owner erzeugt es; bis dahin sind die Zusatzmodule in echten Installationen gesperrt |
| Preise und Lizenzlaufzeiten, Verlängerung, Kulanzzeit nach Ablauf | offen; heute: harte Sperre ab dem Tag nach `valid_until`, Daten bleiben erhalten |
| Mitarbeiterrechte für FiBu/Steuern | offen; heute nur lesend (FiBu) bzw. ohne Rechte (Steuern) |
| Vier-Augen-Prinzip (Freigeber ≠ Bearbeiter) | offen; heute nicht erzwungen (kleine Kanzleien) |
| Herstellerregistrierung ELSTER, Hersteller-ID, Prüfung der ERiC-Lizenz | Owner/Hersteller muss handeln |
| Vertretungsmodell (Vollmachtsdatenbank vs. ELSTER-Vollmacht, Organisationszertifikat) | fachlich offen |
| Steuerberechnung | bewusst nicht umgesetzt; nur mit fachlicher Prüfung |
| ERiC-Pflichtupdates (jährlich, harte Abschaltung) | dauerhafter Betriebsaufwand, Update-Kanal nötig |
| Offline-Lizenz ohne Server | kein Widerruf vor Ablauf möglich; kurze Laufzeiten empfohlen |
