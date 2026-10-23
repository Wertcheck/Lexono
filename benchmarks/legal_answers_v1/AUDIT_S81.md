# §81 Sicherheitsprüfung und Freigabevorbereitung des Antwort-Benchmarks

Stand 19.10.2026, Ausgangsstand `82e5d28` (§80), Branch `feature/feedback-diagnose`; `main` unverändert (`5735e5b`).

**Grenzen dieser Prüfung:**
- Verwendet wurden nur synthetische Konfigurationen in `tmp_path` (keine Freigabe), Mock-Provider ohne Netzwerk und
  simulierte Usage-Daten.
- **Externe Provideraufrufe: 0. Kosten: 0 EUR.** Die produktive Datenbank wurde nicht verwendet; Gateway-URL und echte
  Schlüssel kamen nicht vor.
- Dieser Bericht behauptet keine vollständige Sicherheit. J1–J6 und C-1–C-17 bleiben **OFFEN**.

| Bereich | Befund vorher | Ergebnis |
|---|---|---|
| 1 Freigaben | 10 von 15 Manipulationen freigegeben, darunter juristische Freigaben trotz offener Spezifikationen | **PASS** (alle abgelehnt); Signatur-Lücke dokumentiert |
| 2 Kosten (C-7) | unendliches Budget bzw. unendlicher Preis gültig; unplausible Usage gezählt; Abrunden möglich | **PASS** |
| 3 Ablage (C-11) | Lauf-ID `..` hätte aus der Basis geführt; erweiterte ACL unerkannt; halb geschriebener Zustand unerkannt | **PASS**; Verschlüsselung **nicht umgesetzt**, Entscheidungsvorlage |
| 4 Absturz/Wiederanlauf | keine Klärung unklarer Aufrufe; ein früherer Datenschutzbefund wurde beim Wiederanlauf **nicht** fortgeführt | **PASS** |
| Volle Suite | – | **PASS** bis auf die 4 bekannten, fremden Logo-Tests |

## 1 Freigaben

**Probe** (`audit/s81_freigaben_kosten_vorher.txt` und `…_nachher.txt`, Skript siehe „Reproduktion“): 15
Manipulationen plus Positivkontrolle.

**Vorher freigegeben:**
- Datum in der Zukunft;
- Datum vor der letzten Neusperre der Referenzen (veraltet);
- unmögliches Datum `2026-02-30`;
- **juristische Freigabe der Spezifikationen, obwohl alle drei den Status `entwurf_juristische_freigabe_offen` haben**;
- **Freigabe der Schwellen, obwohl die Rubrik `thresholds`/`weights = null` hat**;
- eine Owner-Freigabe, die für einen anderen Plan kopiert wurde (ohne Planbindung);
- ein Zusatzfeld `signatur`, das Sicherheit nur vortäuscht.

**Härtung** (`scripts/legal_bench/answer_run.py`):
- **Feldprüfung:** Eine Freigabe hat genau die erlaubten Felder (`person`, `rolle`, `datum`, `entscheidung`, keine
  Zusatzfelder). Das Datum muss als Kalenderdatum gültig sein; es darf weder in der Zukunft noch vor der letzten
  Neusperre (LOCK-Historie) liegen.
- **Planbindung der Owner-Freigabe** (`owner_freigabe_lauf_und_kosten`): Sie enthält `plan_fingerprint` und gilt nur
  für genau diesen Plan. Ein neuer Commit, eine geänderte Sperre oder ein geänderter Umfang macht sie ungültig.
- **Widersprüche** (`legal_state()`): Eine juristische Freigabe bei offener Spezifikation bzw. bei Rubrik ohne
  Schwellen ist ein Verstoß.
- **Folge:** Mit dem heutigen Repository-Stand kann **keine** Konfiguration freigegeben werden. Das ist korrekt und als
  echter CLI-Prozess getestet.
- **Tests:** Positivkontrollen brauchen deshalb den Test-Fixture `synthetic_legal_state` (`tests/conftest.py`). Er
  ersetzt nur im Test den juristischen Stand; die Dateien bleiben offen.

**Ohne Git erkannt:** fehlende, unvollständige, formal falsche, zukünftige, veraltete, planfremde und widersprüchliche
Freigaben; Änderung der Konfiguration während des Laufs (Datei-Hash); neuer Commit; geänderte Sperre oder Plan.

**NICHT erkannt (Lücke):**
- **wer** einen Eintrag geschrieben hat;
- ein rückdatierter, sonst stimmiger Eintrag;
- eine Person, die ohne Befugnis „freigegeben“ einträgt.

**Signaturen – nicht implementiert:** Eine selbst erzeugte Signatur ohne Vertrauensanker wäre nur Schein. Belastbar
wäre sie erst mit:
- einem Vertrauensanker, also festgelegten öffentlichen Schlüsseln je Rolle, die außerhalb dieser Konfiguration
  gepflegt werden;
- einer Schlüsselverwaltung bei den Freigebenden (Hardware-Token, Smartcard oder z. B. signierte Git-Commits bzw.
  `git tag -s` durch die zuständigen Personen);
- Regeln für Rotation und Widerruf;
- festgelegtem Fehlerverhalten: unbekannter Schlüssel = Verstoß.

**Owner-Entscheidung F-1:**
- Soll die Freigabe kryptografisch nachgewiesen werden? Wenn ja: mit welchem Verfahren (Empfehlung: signierte
  Git-Commits/Tags der Freigabekonfiguration durch die zuständigen Personen, Prüfung gegen hinterlegte Schlüssel) und
  wer verwaltet die Schlüssel?
- Gilt das Vier-Augen-Prinzip, also unterschiedliche Personen für Owner, Datenschutz und juristische Freigabe?
  Technisch derzeit **nicht** erzwungen.

## 2 Kosten – C-7

**Härtung:**
- **Konfiguration:** Nur endliche Zahlen sind zulässig; `inf` als Budget oder Preis ist jetzt ein Verstoß, `NaN`, Text,
  bool und negative Werte waren es schon. Neu ist der Widerspruch „Schätzung je Aufruf < `max_tokens` ×
  Ausgabepreis“, denn die Schätzung läge dann unter der maximalen Ausgabe.
- **Usage** (`answer_exec.actual_cost`): Nur ganze Zahlen ≥ 0 werden akzeptiert. Mehr Ausgabe-Tokens als `max_tokens`
  sind unplausibel und gelten als UNBEKANNT; der Lauf stoppt dann.
- **Rundung:** auf 1/1.000.000 EUR **auf**, sodass ein Kleinstbetrag nie auf 0 fällt.
- **Unverändert seit §80:** Schätzung und tatsächliche Kosten werden getrennt geführt. Unbekannte Kosten oder ein
  Providerfehler/Timeout blockieren jeden weiteren Aufruf. Das Restbudget wird vor jedem Aufruf geprüft.

**Tests:**
- 7 ungültige Preis- bzw. Budgetwerte;
- Widerspruch der Schätzung;
- 8 unzuverlässige Usage-Varianten;
- Rundung;
- Lauf mit unplausibler Usage (Stopp nach 1 Aufruf);
- dazu §80: fehlende Usage, Budget, Providerfehler, Timeout.

**C-7: OFFEN.** Für die Freigabe fehlen konkret:
1. Modell-ID und genaue Modellversion (C-1), denn sie bestimmen den Preis;
2. Preis je 1 Mio. Eingabe- und Ausgabe-Tokens mit Quelle (Preisliste, Datum), Währung (EUR oder USD mit Kurs und
   Stichtag), netto oder brutto (USt.);
3. ob Prompt-Caching, Batch-Rabatte, Web-Suche oder sonstige Zusatzgebühren anfallen, denn sie sind im Kostenbuch
   **nicht** abgebildet;
4. die Schätzung je Aufruf (mindestens `max_tokens` × Ausgabepreis plus erwartete Eingabe) und `max_cost_eur`;
5. der Abgleich mit der echten Abrechnung nach dem Lauf: Wer gleicht ab, und bis wann?

## 3 Antwortablage – C-11

**Härtung** (`scripts/legal_bench/answer_store.py`):
- **IDs:** Lauf- und Aufruf-IDs müssen mit einem Buchstaben oder einer Ziffer beginnen, dürfen kein `..` enthalten und
  höchstens 128 Zeichen lang sein. Vorher hätten `..` bzw. `.` aus der Basis herausgeführt.
- **Zustand:** `verify` meldet zusätzlich einen halb geschriebenen Zustand (`state.json.tmp`) und **erweiterte
  Zugriffsrechte**, also mehr als einen ACL-Eintrag auf Basis, Laufverzeichnis oder Zustand.
- **Ablageort:** Eine Junction ins Repository wird über `resolve()` erkannt und abgelehnt (unter Windows getestet).

**Tests** (jeweils 0 Anfragen):
- 8 ungültige IDs;
- Junction;
- erweiterte ACL („Jeder: Lesen“), danach Stopp;
- `state.json` ungültig, abgeschnitten bzw. mit `.tmp`-Rest;
- dazu §80: Ablageort, ACL auf genau 1 Konto, Hashes, 4 Schadensarten, kontrollierte Löschung, kein Überschreiben.

**Verschlüsselung – bewertet, nicht umgesetzt:**

| Option | Schlüsselverwaltung | Wiederherstellung | Bewertung |
|---|---|---|---|
| A DPAPI (Windows, kontogebunden, über `ctypes`) | durch das Betriebssystem, an das Benutzerkonto gebunden | Daten bei Verlust des Kontos bzw. Profils unwiederbringlich verloren; kein zweiter Schlüssel | lokal testbar, aber Datenverlust-Risiko und Bindung an *ein* Konto sind eine Owner-Entscheidung |
| B EFS (`cipher /e` auf das Ablageverzeichnis) | Windows-Zertifikat des Kontos; Wiederherstellungsagent (DRA) per Richtlinie | über DRA, falls eingerichtet | ohne Code-Änderung nutzbar; DRA erfordert IT-Administration |
| C BitLocker für das Laufwerk | Betriebssystem/TPM; Wiederherstellungsschlüssel | Wiederherstellungsschlüssel (Ablage regeln) | schützt bei Geräteverlust, nicht gegen andere angemeldete Prozesse desselben Kontos |
| D Anwendungsseitig (`cryptography`/Fernet) | Schlüssel bräuchte eigene, sichere Ablage (Credential Manager, HSM) | Schlüsselsicherung nötig | **nicht** ohne Entscheidung über Schlüsselort und -sicherung; fest codierte oder neben den Daten liegende Schlüssel wären unsicher |

**Bewertung:**
- Eine Verschlüsselung im Code erst dann bauen, wenn Schlüsselort, Wiederherstellung und Verantwortliche festgelegt
  sind. Bis dahin sind die Daten in der Ablage synthetisch und pseudonymisiert (§80: 0 geschützte Werte), und der
  Schutz beruht auf ACL und Hashes.
- **NICHT VERIFIZIERT:** Schutz gegen Administratoren bzw. das eigene Konto sowie Schutz im Ruhezustand.

**C-11: OFFEN.** Entscheidungsvorlage:
1. **Speicherort:** lokaler Pfad außerhalb von Repository und Lexono-Daten, zum Beispiel ein eigenes Verzeichnis je
   Lauf. Netzlaufwerk ja oder nein?
2. **Verschlüsselung:** A, B, C, D oder Kombination (Empfehlung: C als Grundschutz, dazu B mit DRA, falls die IT das
   betreibt).
3. **Zugriff:** nur das ausführende Konto (heute erzwungen) oder zusätzlich namentlich benannte Bewertende? Bewertende
   erhalten heute nur den blinden Prüfbogen.
4. **Aufbewahrung:** Dauer (`storage.retention`), Ende festlegen, z. B. nach Abschluss der Bewertung plus X Wochen.
5. **Löschung:** wer löscht (`RunStore.delete` mit Bestätigung), und wird die Löschung protokolliert bzw. bestätigt?
6. **Sicherung:** Werden Laufdaten gesichert? Wenn ja, gilt die Verschlüsselung auch dort.

## 4 Absturz und Wiederanlauf (E4)

**Befunde:**
1. Für Aufrufe im Zustand `in_bearbeitung` gab es keine Klärung; der Lauf blieb dauerhaft stehen.
2. **Lücke:** Ein Lauf, der wegen eines Datenschutzbefunds abgebrochen war (`abgebrochen_vor_versand`, `blockiert`,
   `datenschutzbefund_antwort`), hätte beim Wiederanlauf mit den übrigen Aufrufen weitergemacht, denn der Wächter
   kannte den früheren Befund nicht mehr.

**Behebung** (`scripts/legal_bench/answer_driver.py`):
- **Datenschutzbefund:** Er beendet den Lauf **dauerhaft**. Der Wiederanlauf stoppt mit `gestoppt_datenschutz_frueher`;
  danach ist ein neuer Lauf mit neuer `run_id` nötig, nachdem die Ursache geklärt ist.
- **`resolve_unclear(store, run_id, call_id, …)`:** nie automatisch. Voraussetzungen: intakte Ablage, Aufruf im Zustand
  `in_bearbeitung`, Person, Rolle, Begründung (≥ 20 Zeichen) und Beleg (≥ 10 Zeichen). Jede Klärung wird mit
  Zeitstempel unter `klaerungen` und `events` im Zustand protokolliert und ist hash-gesichert.
  - **`als_gesendet_werten`:** Der Aufruf zählt als gesendet und fehlgeschlagen und wird **nie** wiederholt. Kosten nur
    mit belegtem Betrag; ohne Betrag bleiben sie UNBEKANNT, und der Lauf bleibt nach der Abbruchregel gestoppt.
  - **`nicht_gesendet_belegt`:** nur mit Beleg, dass keine Anfrage den Provider erreichte. Der Aufruf wird beim nächsten
    Lauf **genau einmal** ausgeführt, mit allen Prüfungen.
- **Nach jeder Klärung:** Der nächste Lauf prüft Konfiguration, Commit, Plan, Sperre und Freigaben neu. Eine Änderung
  führt zum Stopp; das ist getestet.

**Tests:**
- Absturz **vor** dem Aufruf: kein unklarer Zustand, normale Fortsetzung, 2 Payloads.
- Absturz **während** des Aufrufs, als gesendet geklärt mit Kosten: nur der fehlende Aufruf, Status ehrlich
  `unvollstaendig`.
- Absturz während des Aufrufs, als gesendet geklärt ohne Kosten: Stopp, 0 Payloads.
- Absturz zwischen „in Bearbeitung“ und Provider, als nicht gesendet belegt: genau einmal ausgeführt, kein Doppelaufruf
  bei erneutem Start.
- Absturz **nach** der Provider-Antwort, vor dem Ablegen: unklar, nicht erneut gesendet.
- Klärung: 6 Fälle mit unvollständiger Eingabe; Klärung eines erledigten Aufrufs bzw. bei beschädigter Ablage
  verweigert.
- Datenschutzbefund dauerhaft.
- Beschädigte Zustandsdaten (3 Arten).

**Grenze:** Ob ein Aufruf beim Provider ankam, kann der Treiber selbst nicht feststellen. Der Beleg kommt von außen,
etwa aus dem Provider-Protokoll oder der Abrechnung, und dessen Richtigkeit wird nicht technisch geprüft.

## C-7, C-11, C-14

| Punkt | Bewertung | Begründung |
|---|---|---|
| **C-7** Kosten | **OFFEN** | technische Prüfung vorbereitet und getestet; Preise, Währung, Zusatzgebühren und Abgleich fehlen (Liste in Abschnitt 2) |
| **C-11** Ablage | **OFFEN** | ACL, Hashes, Orte und Löschung getestet; Verschlüsselung, Ort, Aufbewahrung und Verantwortliche fehlen (Vorlage in Abschnitt 3) |
| **C-14** Ausführungsschritt | **OFFEN** | Wächter, Treiber, Ablage, Kostenbuch, Wiederanlauf und Klärung mit Mock/Recorder geprüft; echter Endpunkt gesperrt (`RealCloudDisabled`); zusätzlich offen: F-1 (Freigabenachweis), C-7, C-11, J1–J6 |

**Entscheidungsliste C-14** (vor einem echten Lauf, in dieser Reihenfolge):
1. J1–J6 entscheiden und die Spezifikationen bzw. die Rubrik bewusst anpassen (Status, Schwellen), danach begründet neu
   sperren.
2. F-1: Verfahren für den Freigabenachweis und das Vier-Augen-Prinzip.
3. C-7 und C-11 nach den Listen oben.
4. Code-Änderung zum Aufheben der Sperre `RealCloudDisabled` – getrennt reviewen, nur für den freigegebenen Plan.
5. Leitungsprüfung (`tests/test_s78_wire_privacy.py`, §80-Recorder-Lauf) unmittelbar davor erneut laufen lassen.

## Tests und Reproduktion

| Test | Ergebnis |
|---|---|
| `tests/test_s81_benchmark_hardening.py` (neu) | 56 bestanden |
| Angepasste Tests | `test_s78_benchmark_gate.py` (plangebundene Owner-Freigabe `bind_owner`, Datum = heute, Fixture; der CLI-Test weist jetzt den echten Widerspruch nach), `test_s79_benchmark_exec_guard.py` und `test_s80_benchmark_driver.py` (Fixture, Planbindung) |
| §78–§81-Dateien gesamt | 198 bestanden |
| Volle Suite in 6 Teilen | 5626 bestanden; 4 FAILED nur in `tests/test_design_refresh.py` (Logo-Tests, fremde Asset-Änderungen, bekannt) |
| Repository nach den Tests | unverändert; LOCK gültig; Junction-Test entfernt nur die Verknüpfung |

**Reproduktion:**
```
.venv\Scripts\python.exe -m pytest tests/test_s81_benchmark_hardening.py
```

Die Probe vorher bzw. nachher steht unter `audit/s81_freigaben_kosten_*.txt`. Sie zeigt je Fall „FREIGEGEBEN“ oder
„abgelehnt“ mit Grund.

## Restrisiken

- **Freigabe-Einträge:** Sie bleiben überprüfbare Angaben ohne Urhebernachweis (F-1).
- **Unklare Aufrufe:** Die Klärung stützt sich auf einen externen Beleg, dessen Richtigkeit nicht technisch geprüft
  wird.
- **Ablage:** Sie ist nicht verschlüsselt; ACL und Hashes schützen nicht gegen Administratoren oder das eigene Konto
  mit Absicht.
- **Kostenbuch:** Zusatzgebühren und Wechselkurse sind nicht abgebildet; die Abrechnung kann abweichen.
- **Juristischer Stand:** Die Erkennung „offen“ beruht auf dem Status-Text der Spezifikationen („entwurf“ oder
  „offen“). Die Bezeichnung eines freigegebenen Status ist Teil von J1–J6.
