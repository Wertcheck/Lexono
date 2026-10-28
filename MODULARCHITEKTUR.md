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

**Rechte und Vier-Augen-Prinzip** (Owner-Entscheidung 28.10.2026, siehe Abschnitt 8):

- Vorbereiten (anlegen, Anlagen, Werte, zur Prüfung geben) dürfen auch Mitarbeiter, aber nur für ihnen zugewiesene
  Mandanten.
- Freigeben dürfen nur Admin und Anwalt (`PERM_TAX_APPROVE`), und nur, wer den Vorgang **weder erstellt noch Teile oder
  Werte geändert** hat. Abgelehnte Selbstfreigaben werden protokolliert.
- Übermitteln (`PERM_TAX_SUBMIT`) nie durch Mitarbeiter; die Übermittlung selbst ist ohnehin noch nicht implementiert.

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
| Preise und Lizenzlaufzeiten | vorläufige Planung festgelegt (Abschnitt 9), nicht live |
| Verlängerung, Kulanzzeit nach Ablauf | offen; heute: harte Sperre ab dem Tag nach `valid_until`, Daten bleiben erhalten |
| Mitarbeiterrechte für FiBu/Steuern | **entschieden 28.10.2026**, umgesetzt (Abschnitt 8) |
| Vier-Augen-Prinzip (Freigeber ≠ Bearbeiter) | **entschieden 28.10.2026**, umgesetzt (Abschnitt 8) |
| Kanzlei mit nur einer freigabeberechtigten Person | Freigabe eigener Vorgänge dann nicht möglich (gewollt); Sonderregel für Einzelkanzleien offen |
| Lizenzpflicht für das Basispaket | offen; heute läuft das Basispaket ohne Lizenz (keine Aussperrung bestehender Installationen) |
| Herstellerregistrierung ELSTER, Hersteller-ID, Prüfung der ERiC-Lizenz | Owner/Hersteller muss handeln |
| Vertretungsmodell (Vollmachtsdatenbank vs. ELSTER-Vollmacht, Organisationszertifikat) | fachlich offen |
| Steuerberechnung | bewusst nicht umgesetzt; nur mit fachlicher Prüfung |
| ERiC-Pflichtupdates (jährlich, harte Abschaltung) | dauerhafter Betriebsaufwand, Update-Kanal nötig |
| Offline-Lizenz ohne Server | kein Widerruf vor Ablauf möglich; kurze Laufzeiten empfohlen |


## 8. Mitarbeiterrechte und Mandantenzuweisung (28.10.2026)

| Rolle | Lexono Finanzen | Lexono Steuern |
|---|---|---|
| Admin, Anwalt | alle Mandanten; erfassen, bearbeiten, buchen, stornieren, Konten, Stammdaten, DATEV-Entwurf, Mitarbeiterzugriff verwalten | alle Mandanten; vorbereiten, freigeben (nie eigene oder selbst bearbeitete Vorgänge), übermitteln (nicht implementiert) |
| Mitarbeiter | **nur zugewiesene Mandanten**; Buchungen erfassen und offene bearbeiten, Belege hochladen und zuordnen, Aufgaben | **nur zugewiesene Mandanten**; vorbereiten und zur Prüfung geben |
| Mitarbeiter ohne Zuweisung | kein Zugriff (deny by default, 404) | kein Zugriff |

**Umsetzung:**

- `PERM_FIBU_CAPTURE` (alle Rollen) bzw. `PERM_FIBU_MANAGE` (Admin, Anwalt).
- `PERM_TAX_PREPARE` (alle Rollen) bzw. `PERM_TAX_APPROVE`/`PERM_TAX_SUBMIT` (Admin, Anwalt).
- **Mandantenzuweisung:** Tabelle `client_user_access` (Migration `schritt3_042`, ohne Vorbelegung). Gepflegt wird sie
  durch Admin oder Anwalt unter *Finanzbuchhaltung → Einstellungen → Mitarbeiterzugriff*, mit Audit-Ereignis
  `client_access_changed`.
- **Durchsetzung:** Jede FiBu-Route und jede Steuerfunktion prüft serverseitig Modul, Recht **und** Mandantenzugriff.
  Die Oberfläche blendet nur zusätzlich aus.
- „Buchen“ (festschreiben) ist auch beim Anlegen (`action=save_post`) serverseitig gesperrt.
- **Vier-Augen-Prinzip:** Wer als Ersteller im Audit-Log steht oder dort Teile bzw. Werte geändert hat
  (`tax_filing_created`, `tax_part_added`, `tax_value_changed`), kann nicht freigeben. Der Versuch wird als
  `tax_approval_refused` protokolliert.
- **Basispaket unverändert:** Mandanten, Akten und Chat sehen alle Rollen wie bisher. Die Zuweisung gilt bewusst nur
  für die Zusatzmodule.

## 9. Lizenzmodell und Preise (vorläufige Produktplanung, nicht live)

Konfiguriert in `app/modules/plans.py`. Die Preise sind dort reine Planung: Lexono zeigt sie nicht als Kaufpreise an
und schaltet anhand von Preisen nichts frei.

| Produkt | Monatlich (netto, vorläufig) | Art |
|---|---|---|
| Lexono Kanzlei | 99 € | Basispaket |
| Lexono Finance (Modul `finanzen`) | +149 € | Zusatzmodul |
| Lexono Steuern (Modul `steuern`) | +299 € | Zusatzmodul |

- **Kombinationen:** Finance und Steuern sind frei kombinierbar.
  - Kanzlei + Finance: 248 €
  - Kanzlei + Steuern: 398 €
  - alle drei: 547 €
- **Kosten und Vertrieb:** Keine Einrichtungsgebühr; Kunden kaufen und installieren selbst.
- **Flexibilität:**
  - Die Lizenz enthält **Module** und **Laufzeit** (`valid_from`/`valid_until`). Nur diese schalten frei.
  - Dazu kommen informativ `plan` (z. B. `kanzlei+finanzen`) und `term` (z. B. `monthly`).
  - Neue Module, Bundles oder Abrechnungszeiträume (z. B. jährlich) lassen sich ergänzen, ohne Lizenzformat oder
    Prüfung zu ändern.
  - Bei monatlicher Abrechnung empfiehlt sich eine kurze Laufzeit je Lizenzdatei, z. B. ein oder zwei Monate mit
    Nachlieferung durch den Shop, weil eine Offline-Lizenz vor Ablauf nicht widerrufen werden kann.
- **Offen:**
  - Ausweisung der Umsatzsteuer;
  - Lizenzpflicht für das Basispaket;
  - Kulanzzeit nach Ablauf;
  - Shop-Anbindung (automatische Signatur nach Zahlung);
  - Lizenz je Installation oder je Nutzerzahl.

## 10. Produktiver Lizenzschlüssel – sichere Erzeugung und Verwahrung (Anleitung, noch NICHT ausgeführt)

**Stand 28.10.2026:** Es gibt **kein** produktives Schlüsselpaar.

- `TRUSTED_PUBLIC_KEYS` ist leer, deshalb sind Zusatzmodule in echten Installationen gesperrt.
- Tests erzeugen klar gekennzeichnete Wegwerf-Schlüssel zur Laufzeit (`tests/module_license_utils.py`). Im Repository
  liegt kein Schlüssel.

**Schlüssel erzeugen:**

1. **Ort:** ein vertrauenswürdiger, aktueller Rechner des Herstellers, nicht der Build-Rechner und kein
   Kanzleirechner, idealerweise ohne Netz während der Erzeugung.
2. **Passwort setzen:** `LEXONO_LICENSE_KEY_PASSWORD` auf ein langes, zufälliges Passwort, zu verwahren im
   Passwortmanager. Danach erzeugen:
   `python scripts/lexono_license_tool.py keygen --out-dir <verschlüsselter Datenträger>`.
   Das Werkzeug verweigert Ziele im Repository.
3. **Ablage:** Datei `lexono_license_private.pem` mindestens doppelt sichern, verschlüsselt und offline, an getrennten
   Orten (z. B. zwei verschlüsselte USB-Datenträger im Tresor). Später möglichst HSM oder Cloud-KMS, damit der
   private Schlüssel die Signierumgebung nie verlässt.
4. **Eintragen und ausliefern:**
   - Die ausgegebene Zeile (`key_id` und öffentlicher Schlüssel) in `app/modules/licensing.TRUSTED_PUBLIC_KEYS`
     eintragen, committen, bauen und ausliefern.
   - Der öffentliche Schlüssel ist nicht geheim.
   - **Niemals** gehört der private Schlüssel ins Repository, in die App, in den Installer, in Logs, in E-Mails oder
     in Tickets.

**Schlüsselwechsel:** neuen Schlüssel ergänzen (mehrere Einträge sind möglich), Lizenzen mit dem neuen signieren, den
alten nach Auslauf aller damit signierten Lizenzen entfernen.

**Kompromittierung:** Schlüssel sofort aus `TRUSTED_PUBLIC_KEYS` entfernen, Update ausliefern, Lizenzen neu ausstellen.

## 11. ELSTER-Herstellerregistrierung – offiziell bestätigt (Abruf elster.de, 28.10.2026)

**Ablauf** laut https://www.elster.de/elsterweb/infoseite/entwickler:

1. Registrierung als Hersteller/Entwickler.
2. Prüfung durch den IuK-Bereich des Bayerischen Landesamts für Steuern, „ob die Herstellung von Software beabsichtigt
   ist“.
3. Zugang zum Entwicklerbereich; die Zugangsdaten kommen „innerhalb weniger Tage“ per E-Mail.
4. ERiC im Entwicklerbereich herunterladen.
5. Hersteller-ID unter „Anträge und Formulare“ beantragen.
6. ELSTER-Newsletter abonnieren.

ERiC wird „kostenlos zur Integration in die eigenen Steuer-, Finanz- und Lohnbuchhaltungsprogramme zur Verfügung
gestellt“.

**Registrierungsformular** (https://www.elster.de/elsterweb/registrierung-entwickler):

- Pflichtfelder: Anrede, Vor- und Nachname, Firma, E-Mail, Webseite, Telefon, Begründung der Anmeldung.
- „Ein Anspruch auf die Erteilung eines Accounts besteht nicht“; erteilte Zugänge können wieder gelöscht werden.

Die technischen ERiC-Seiten (`/elsterweb/entwickler/infoseite/eric`) sind nur nach Anmeldung lesbar. Die technischen
Details in Abschnitt 5 stammen daher aus dem Entwicklerhandbuch 43.3 (Drittquelle) und sind nach der Registrierung
gegen die Originale zu prüfen.

**Status:** **nicht registriert**, keine Hersteller-ID, keine ERiC-Bibliothek im Projekt, keine Übermittlung.

**Nächste Schritte** (Owner bzw. Hersteller):

1. **Registrieren:** Firma, Webseite und Begründung, z. B. „Kanzleisoftware Lexono, Modul Steuern:
   Erklärungserstellung und Übermittlung über ERiC“.
2. **Nach dem Zugang:**
   - ERiC herunterladen;
   - `lizenz.pdf` rechtlich prüfen (Bundling, Datenschutz, Logdateien);
   - Hersteller-ID für „Lexono Steuern“ beantragen;
   - den Newsletter abonnieren.
3. **Technischen Prototyp bauen**, ausschließlich mit Testmerker und amtlichen Testzertifikaten:
   - ctypes-Anbindung;
   - Validierung und PDF für Einspruch und Fristverlängerung;
   - ERiC-Verzeichnis per Inno Setup, Signaturen unverändert.
4. **Fachlich entscheiden:** Vertretungsmodell (Vollmachtsdatenbank oder ELSTER-Vollmacht) und Organisationszertifikat
   der Kanzlei.
5. **Update-Prozess** für die jährliche ERiC-Mindestversion festlegen.
