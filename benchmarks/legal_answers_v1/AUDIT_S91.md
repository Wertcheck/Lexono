# §91 A3 – Host- und VM-Abnahmeskripte im Trockenlauf

Stand 19.10.2026. Branch `feature/feedback-diagnose`, HEAD `b7e2daa` (unverändert, **nicht committet**); `main` = `5735e5b`.

**Grenzen dieser Arbeit:**
- Nur lokale Code-, Test- und Dokumentationsänderungen.
- Keine Installation, keine VM erstellt oder gestartet.
- Keine Änderung an Windows, Firewall, Netzwerk, Konten oder Schlüsseln; keine echten Schlüssel, keine Passphrase-Abfrage.
- Keine externen Verbindungen und keine Provider-Aufrufe; Listener und Proben nur auf Loopback.
- Die Produktivdatenbank wurde nicht geöffnet.
- Die vier fremden Dateien und die bestehenden uncommitteten Berichte (AUDIT_S88–S90, `PRIVACY_REVIEW_ARCHITECTURE.md`)
  blieben in §91 unangetastet.
- **Keine reale Sicherheitsabnahme:** V1–V13 bleiben NICHT DURCHGEFÜHRT, C-14 bleibt GESPERRT.

## 1 Bestandsaufnahme

- Host- und VM-Skripte gab es **keine**. Vorhanden waren:
  - die Abnahme-Proben und der Regelprüfer aus §86;
  - Belegbündel, Urteil, Gate v2 und Signierwerkzeug aus §90.
- Lücken in §90, die erst durch A3 sichtbar wurden:
  1. Bewertungen (`kriterien: {...: true}`, pytest `"passed"`, Firewall-Export) standen **direkt im Beleg**. Eine
     Bewertung von Hand war damit möglich, wenn auch durch die Signatur des Owners begrenzt.
  2. Es gab keine getrennte Erfassung von Soll und Beobachtung.
  3. Es gab keine Bindung jedes einzelnen Belegs an Lauf und Konfiguration (nur über das Manifest).
  4. Belege konnten bei der Nutzung **bis zu 48 h** alt sein.
  5. Ein Kanarienlog ohne Treffer war nicht von einem nicht funktionierenden Listener zu unterscheiden.
  6. Bündel aus einem Trockenlauf und aus einer echten Abnahme waren nicht unterscheidbar.

## 2 Änderungen

| Datei | Art | Inhalt |
|---|---|---|
| `scripts/legal_bench/isolation_acceptance.py` | neu | Belegteile je Rolle (`EvidencePart`: vm = E0, host = E1, endpunkt = E2), Beobachter-Sammler (`collect`), Bündelung (`assemble`), echter Kanarien-Listener mit Funktionskontrolle, gesperrte echte Befehle (`run_real`, `REAL_COMMANDS`), synthetische Beobachtungen, Trockenlauf (`dry_run`, CLI `trockenlauf`) |
| `scripts/legal_bench/isolation_evidence.py` | geändert | Bewertung **abgeleitet** aus der Beobachtung (`derive`, `KRITERIEN_ABLEITUNG`, `junit_results`, `acl_to_export`); Soll/Beobachtung Pflicht; `lauf_sha256` je Beleg; Zeit/Erzeuger im Beleg = Manifest; Modus `real`/`trockenlauf`; Höchstalter zum Nutzungszeitpunkt; Funktionskontrolle der Kanarien Pflicht |
| `scripts/legal_bench/isolation_gate.py` | geändert | übergibt Nutzungszeitpunkt und Modusregel an das Urteil (Trockenlauf nur im Test-Gate); Prüfwerkzeug-Fingerabdruck umfasst die neuen Dateien |
| `scripts/legal_bench/owner_sign.py` | geändert | Urteil mit Nutzungszeitpunkt; Trockenlauf-Belege nur bei `test_only` |
| `tests/isolation_test_support.py` | geändert | Belege entstehen über denselben Weg wie in der Abnahme (`evidence_content`) |
| `tests/test_s90_owner_gate.py`, `tests/test_s87_isolation_gate.py` | angepasst | Negativfälle verändern jetzt die **Beobachtung** statt der Bewertung; neue Fälle für manuelle Bewertungen |
| `tests/test_s91_acceptance_dry_run.py` | neu | 30 Tests |

### 2.1 Was die Skripte je Test erfassen

| Test | VM (E0) | Host (E1) | Endpunkt (E2) |
|---|---|---|---|
| V1 | – | Port-ACL-Export (`Get-VMNetworkAdapterExtendedAcl`) → `acl_to_export` → Regelprüfer | – |
| V2 | Firewall-Profile, Ausgang des Änderungsversuchs, Gruppen-SIDs des Kontos | Hash der Gastrichtlinie gegen Golden-Image-Soll | – |
| V3 | JUnit der Endpunktprobe | – | Log-Einträge der VM-IP im Zeitfenster, `weitergeleitet` je Eintrag |
| V4–V8 | JUnit der Kanarienproben (tcp, curl, native, udp, dns) | Kanarien-Listener (IPv4/IPv6, TCP/UDP/DNS) mit Funktionskontrolle | – |
| V9 | Netzadapterliste | VM-Adapter, Gastdienste, erweiterte Sitzung, ACL (Abfluss nur zum Endpunkt) | – |
| V10 | JUnit der Export-Prüfung | ACL-Hash vorher und nachher, Erzeuger | – |
| V11 | JUnit der Kurzprüfung | Ausgang des Zurücksetzens, Image-Hash Soll/vorher/nachher | – |
| V12 | – | Kanarienlog (alle Merkmale) und Auswertung auf dem Host | – |
| V13 | JUnit `test_s87`/`test_s90` | Diagnose des Produktiv-Gates mit veralteter Bestätigung | – |

Für jede Kriterien-Anforderung gibt es genau eine Ableitungsfunktion (beim Import geprüft). Eine fehlende oder leere
Beobachtung ergibt nie `true`.

### 2.2 Regeln der Skripte

- **Klassenbindung:** Die Klasse eines Belegs ergibt sich allein aus der Rolle. Eine Rolle kann nur Belege schreiben,
  die die Anforderungen für ihre Klasse vorsehen; die VM kann z. B. keinen E1-Kanarienbeleg erzeugen. `assemble`
  verweigert Teile, deren Rolle oder Erzeuger widerspricht, die eine fremde Klasse beanspruchen, die zu einem fremden
  Lauf gehören, nachträglich veränderte Belege und Dubletten.
- **Kein Überschreiben:** Weder Belege noch Bündel werden überschrieben (exklusives Schreiben).
- **Fehler werden festgehalten:** Wirft ein Beobachter eine Ausnahme oder liefert er kein Objekt, entsteht eine
  Beobachtung `{"fehler": …}`, die zu FAIL führt. Fehlt ein Beobachter, entsteht kein Beleg; das ergibt BLOCKIERT bzw.
  NICHT DURCHGEFÜHRT.
- **Echte Befehle gesperrt:** Sie laufen nur mit `LEXONO_ISOLATION_ACCEPTANCE=1` **und** `real=True`; sonst wird vor
  jedem Prozessstart abgelehnt. Der VM-Name wird gegen Einschleusung geprüft. Die Befehle sind lesend, mit genau einer
  Ausnahme: dem Änderungsversuch in V2, der in der VM **scheitern muss**.
- **Kanarien-Listener:** Ohne Freigabe bindet er nur an Loopback; er empfängt nur und sendet nichts nach außen, außer
  der eigenen Funktionskontrolle an seine eigenen Adressen.
- **Kein Provider:** Das Modul importiert keinen Provider und öffnet keine Verbindung außer zu Loopback-Listenern.

## 3 Trockenlauf – Ergebnisse

`python -m scripts.legal_bench.isolation_acceptance trockenlauf` baut eine synthetische Zielumgebung (Politik-Kopie
„FREIGEGEBEN“, Endpunkt 127.0.0.1:9) und erzeugt die Teile VM, Host und Endpunkt. Dabei laufen **echte** Listener auf
127.0.0.1 und ::1. Daraus entsteht ein Bündel, das mit der §90-Logik bewertet wird.

| Trockenlauf | Ergebnis (synthetisch, kein Nachweis über ein reales System) |
|---|---|
| ohne Leck | Gesamturteil PASS, alle 13 PASS |
| `--leck udp` (lokales Datagramm an einen Kanarien-Listener) | Gesamturteil FAIL: V4–V8 und V12 FAIL („Kanarientreffer aus der VM“), übrige PASS |
| Leck tcp, tcp6, dns (Tests) | jeweils alle Kanarientests FAIL |
| Host-Teil fehlt | V4–V8, V10, V12 BLOCKIERT („Klasse E1 fehlt“), V1 NICHT DURCHGEFÜHRT, Gesamt BLOCKIERT – **keine Netzwerkprüfung ohne Host-Beobachtung als PASS** |
| Endpunkt-Log fehlt | V3 BLOCKIERT („Klasse E2 fehlt“) |
| Beobachter wirft, liefert Unsinn oder einen Fehler | Test FAIL, Fehler steht in der Beobachtung |
| zusätzliche Allow-Regel „Any“ in der ACL | V1 FAIL |
| echte §86-Abnahmetests ohne Zielumgebung (alle übersprungen, echtes JUnit) | V4–V8, V10 FAIL („uneindeutig = FAIL“) |
| Listener ohne Funktionskontrolle | alle Kanarientests FAIL |
| Beleg nach dem Schreiben verändert | `assemble` verweigert; im Bündel: BLOCKIERT („manipuliert“) |
| Bewertung von Hand auf „erfüllt“ gesetzt, Manifest-Hash sogar nachgezogen | BLOCKIERT („widerspricht der Beobachtung“); ohne mitgeschriebene Bewertung: FAIL aus der Beobachtung |
| zusätzliches `"status": "PASS"` im Beleg | wird nicht gelesen; das Urteil bleibt das abgeleitete (fehlender Pflichtbeleg bleibt BLOCKIERT) |
| Trockenlauf-Bündel ohne Trockenlauf-Erlaubnis (Produktivmodus) | BLOCKIERT („Trockenlauf-Belege zählen nicht produktiv“); `owner_sign` würde nicht signieren |

## 4 Nachweisklassen

| Klasse | Erzeuger | Aussage | Pflicht für |
|---|---|---|---|
| E0 | `vm-abnahme` (in der VM) | Verhalten aus Sicht des geprüften Systems | V2, V3–V8, V9, V10, V11, V13 (Proben/Konfiguration) |
| E1 | `host-abnahme` (Host, außerhalb der VM) | Durchsetzung und Beobachtung unabhängig von der VM | V1, V2, V4–V8, V9, V10, V11, V12, V13 |
| E2 | `endpunkt-protokoll` | Beobachtung eines Fremdsystems | V3 |

Eine höhere Klasse erfüllt eine niedrigere Anforderung, umgekehrt nie. Die Herkunft eines E1-Belegs sichert weiterhin
nur die Signatur des Owners auf dem Host zu (Restrisiko aus §90).

## 5 Belegalter

- **Befund §90:** Ein Beleg durfte beim Signieren bis zu 24 h alt sein, und die Bestätigung gilt bis zu 24 h. Bei der
  Nutzung waren Belege damit **bis zu 48 h** alt.
- **Bewertung je Belegart:** Alle vier Arten beschreiben einen Zustand, der sich jederzeit ändern kann.

| Art | Was sich ändern kann | erforderliche Aktualität |
|---|---|---|
| `firewall_export` (Port-ACL) | jede Regeländerung auf dem Host | höchstens 24 h bei Nutzung; Vorher/Nachher-Hash (V10) im selben Lauf |
| `kanarien` | Listener- und Pfadzustand | höchstens 24 h bei Nutzung; Aktivzeit muss die VM-Proben umfassen; Funktionskontrolle Pflicht |
| `pytest` (Proben/Gate) | Netzwerkzustand; Code (zusätzlich an den Werkzeug-Fingerabdruck gebunden) | höchstens 24 h bei Nutzung |
| `kriterien` (Gast-/VM-Konfiguration, Image, Endpunkt-Log) | Konfiguration, Image, Endpunkt | höchstens 24 h bei Nutzung |

- **Umgesetzt (eindeutig sicher, nur strenger):** `MAX_EVIDENCE_AGE_AT_USE` = 24 h je Art. Das Gate prüft das vor dem
  Start und vor **jedem** Aufruf. Eine Bestätigung endet damit spätestens 24 h nach ihrem ältesten Beleg; ein zu alter
  Beleg ergibt BLOCKIERT und schließt das Gate mitten im Lauf.
- **Nicht umgesetzt:** Eine kürzere Frist (z. B. 12 h) für die Netzwerkgrenze wäre denkbar. Sie ist aber nicht eindeutig
  begründbar, solange kein realer Ablauf gemessen ist. Siehe offene Punkte.

## 6 Was nur in einer echten VM bzw. am echten Host geht

| Prüfung | warum nicht lokal | Host-Rechte |
|---|---|---|
| V1, V10 Port-ACL-Export, Vorher/Nachher | erst mit Hyper-V-VM vorhanden | Mitglied „Hyper-V-Administratoren“ oder erhöhte Administrator-Sitzung (lesend) |
| V4–V8, V12 Kanarien am vSwitch | Listener auf der Host-IP des internen Switches; Pakete müssen aus der VM kommen | Listener selbst ohne Administrator; die **Host-Firewall muss eingehend zu den Kanarienports zulassen** (Systemänderung, Teil von D1) |
| V2 Gast-Firewall, Konto, Änderungsversuch | Gast-Betriebssystem nötig | in der VM: Benchmark-Konto ohne Administrator (gewollt); Richtlinie per Golden Image |
| V3 Endpunkt-Log | Endpunkt (Relay) nötig, D3 | Zugriff auf das Relay-Log |
| V9 VM-Konfiguration | Hyper-V | wie V1 |
| V11 Zurücksetzen, Image-Hash | Golden Image, Checkpoint | Zurücksetzen ist eine Änderung → eigener, freigegebener Schritt; Hash lesen braucht Zugriff auf die VHDX |
| V13 Produktiv-Gate in der VM | VM-Identität und echte Schlüsselliste | – |

### 6.1 Port-ACL und Gast-Firewall – wie der praktische Nachweis aussehen muss

- **Port-ACL (die eigentliche Grenze):**
  - Der Export allein ist nur eine **Konfigurationsaussage** (V1).
  - Die Wirksamkeit belegen erst Kanarientreffer bzw. ihr Ausbleiben am Host-Listener. Das gilt nur bei bestandener
    Funktionskontrolle und mit Proben aus der VM über IPv4, IPv6, UDP, DNS, nativ und Unterprozess (V4–V8).
  - Hinzu kommt der Vorher/Nachher-Vergleich (V10).
  - Offen und nötig ist eine **Kalibrierung über den VM-Pfad**: Vor dem Setzen der ACL muss die VM die Kanarien erreichen
    (Treffer erwartet). Erst danach beweist „kein Treffer“ die ACL und nicht etwa die Host-Firewall. Die heutige
    Funktionskontrolle prüft nur den Listener vom Host aus.
  - Die Semantik der Extended ACL (Gewichtung, zustandsbehaftete Regeln, Wirkung auf IPv6 Neighbor Discovery und DHCP)
    ist **NICHT VERIFIZIERT**.
- **Gast-Firewall (zweite Linie):**
  - Nachweis in der VM (E0): Profile, ausgehend Block, Konto ohne Administrator, gescheiterter Änderungsversuch.
  - Dazu vom Host (E1): Gastrichtlinie gleich dem Golden-Image-Soll.
  - Sie ist **keine** Grenze. Ein Administrator in der VM könnte sie abschalten; deshalb wird sie nur als Plausibilität
    gewertet.

## 7 Offene Voraussetzungen vor D1 (Systemänderungen)

| Voraussetzung | Status |
|---|---|
| Entscheidung D1 (Hyper-V, VM, Plattenplatz, Host-Firewall-Regel für Kanarien) | BLOCKIERT (deine Entscheidung) |
| D3 Endpunkt bzw. Relay-Betrieb und Relay-Log-Format für V3 | BLOCKIERT |
| D4 Schlüssel erzeugen, Wurzel-Fingerabdruck im Code eintragen | NICHT DURCHGEFÜHRT |
| Kalibrierung über den VM-Pfad (Positivprobe vor der ACL) als Beleg und Prüfregel | NICHT DURCHGEFÜHRT (nächster Code-Schritt, rein lokal möglich) |
| Echte Beobachter an reale Ausgaben anpassen (`ConvertTo-Json`-Formate, Einzelobjekt gegen Liste) | NICHT DURCHGEFÜHRT – erst mit echter VM prüfbar |
| Golden Image samt Richtlinie und Hash (V2, V11) | NICHT DURCHGEFÜHRT |
| Ablauf und Dauer der realen Abnahme messen (Grundlage für kürzere Belegfristen) | NICHT DURCHGEFÜHRT |

## 8 Risiken

1. **Synthetische Beobachtungen:** Der Trockenlauf beweist nur die Werkzeugkette, nicht die Isolation. Bündel aus dem
   Trockenlauf sind markiert und werden produktiv abgelehnt.
2. **Herkunft der Belege:** Eine Beobachtung kann weiterhin vom Host gefälscht werden (E1 ist nicht unabhängig vom
   Owner). Die Ableitung verhindert nur Bewertungen, die der Beobachtung widersprechen.
3. **Tautologische Kriterien:** `export_ausserhalb_der_vm_erzeugt` und `auswertung_ausserhalb_des_benchmarks` folgen aus
   dem Erzeuger, sind also nur Prozessaussagen.
4. **Kanarien prüfen nur Ziele am Host:** Ziele im Internet erreicht eine Probe nicht. Dass die ACL auch dorthin sperrt,
   folgt aus der Deny-all-Regel, ist aber ein Schluss und keine Beobachtung. Optional E2-Kanarien im Internet, mit
   Kosten bzw. Fremdsystem.
5. **JUnit-XML ist untrusted:** DTD und Entitäten werden abgelehnt; Klassen-Testfälle (`Klasse::Methode`) würden falsch
   zugeordnet. Die Abnahmetests sind Modulfunktionen.
6. **Der V2-Änderungsversuch ist eine echte Änderung:** In einer fehlerhaft konfigurierten VM gelingt er. Das ist dann
   FAIL, und die VM muss zurückgesetzt werden (V11).
7. **Host-Firewall-Regel für Kanarien:** Sie ist eine Systemänderung auf dem Produktivrechner und gehört zu D1.

## 9 Tests und Ergebnisse

| Prüfung | Ergebnis |
|---|---|
| `tests/test_s91_acceptance_dry_run.py` (30) | **PASS** |
| `tests/test_s90_owner_gate.py` (101, an §91 angepasst), `test_s87` (40), `test_s86` | **PASS** (zusammen mit s91: 187 bestanden, 7 übersprungen = Zielumgebung) |
| Benchmark-Tests §77–§85 (Treiber, Härtung, Zaun, Grenzen, Freigabe) | **PASS** (162 + 153; Treiber- und Härtungstests nach der letzten Änderung erneut: 142 bestanden, 7 übersprungen) |
| volle Suite in 6 Teilen | 5626 bestanden, **4 FAIL** – die bekannten fremden Logo-Tests (`tests/test_design_refresh.py`), nicht behoben |
| CLI-Trockenlauf ohne und mit Leck | PASS bzw. FAIL wie erwartet (Abschnitt 3) |

## 10 Ergebnisübersicht

| Punkt | Ergebnis |
|---|---|
| A3-Skripte (VM/Host/Endpunkt, Bündelung, Listener, gesperrte echte Befehle) | **PASS** (lokal, Trockenlauf) |
| Bewertung nur aus Beobachtungen, manuelle PASS-Werte unwirksam | **PASS** (lokal) |
| Netzwerkprüfungen ohne Host-Beobachtung nie PASS | **PASS** (lokal) |
| Belegalter: höchstens 24 h zum Nutzungszeitpunkt | **PASS** (lokal, umgesetzt) |
| Kalibrierung über den VM-Pfad | **NICHT DURCHGEFÜHRT** |
| Echte Beobachter gegen eine reale VM | **NICHT DURCHGEFÜHRT** (BLOCKIERT bis D1) |
| V1–V13 real, reale Sicherheitsabnahme | **NICHT DURCHGEFÜHRT** |
| C-14 | **GESPERRT** |
