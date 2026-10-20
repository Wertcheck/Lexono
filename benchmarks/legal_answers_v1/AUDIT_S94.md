# §94 Echter Isolations-Abnahmemodus – Vervollständigung

Stand 20.10.2026. Branch `feature/feedback-diagnose`, HEAD `b7e2daa` (geprüft; **nicht committet**, §90–§94 liegen
weiterhin uncommittet vor); `main` = `5735e5b`.

**Grenzen dieser Arbeit:**
- Nur lokale Code-, Test- und Dokumentationsänderungen.
- Keine Änderung an Windows, Hyper-V, Firewall, ACL, Netzwerk oder Konten; keine VM.
- Keine externen Verbindungen, Provider-Aufrufe oder Schlüssel.
- Die Produktivdatenbank wurde nicht geöffnet.
- Die vier fremden Dateien, AUDIT_S88–S93 und `PRIVACY_REVIEW_ARCHITECTURE.md` blieben unverändert.
- **Keine reale Isolation oder Sicherheitsabnahme:** Alle Systembefehle liefen in den Tests gegen ein **simuliertes**
  Hyper-V bzw. eine simulierte PowerShell. V1–V13 bleiben real NICHT DURCHGEFÜHRT, C-14 bleibt GESPERRT.

## 1 Bestandsaufnahme

Aus AUDIT_S93 §1.1 waren fünf Lücken offen:
1. eine Kommandozeile für den realen Modus;
2. ein Manifest-Generator für die §86-Proben;
3. die Normalisierung der PowerShell-Ausgaben;
4. Beobachter für V11 und V13;
5. ein Host-Skript für die Port-ACL.

Vorhanden und **unverändert weiterverwendet** sind:
- `EvidencePart`, `collect`, `assemble`, `CanaryListener` mit Phasen, `calibration_probe`, `run_real`;
- das Urteil mit Ableitung aus Beobachtungen, die Gegenprobe (§92), Gate v2 und `owner_sign`.

## 2 Änderungen

| Datei | Art | Inhalt |
|---|---|---|
| `scripts/legal_bench/isolation_real.py` | neu | Konfiguration (`load_konfig`, strenge Prüfung), Lauf-Bindung (`lauf_aus_konfig`), Abnahme-Manifest (`abnahme_manifest`/`check_manifest`), echte Beobachter (V2, V9, V13, Gastrichtlinie, pytest/JUnit), CLI `konfig-pruefen`, `vm-teil --phase vorher\|nachher`, `host-lauf`, `endpunkt-teil`, `buendeln` |
| `scripts/legal_bench/isolation_host_control.py` | neu | **einziges Werkzeug mit Änderungen am Host**: `acl-plan`, `acl-setzen`, `acl-pruefen`, `acl-entfernen`, `image-zuruecksetzen` |
| `scripts/legal_bench/isolation_acceptance.py` | geändert | `normalize_ps_json`; Belege im Modus `real` nur mit `LEXONO_ISOLATION_ACCEPTANCE=1`; Teile über Phasen fortsetzen (`EvidencePart.resume`, Prüfung auf unveränderte Belege); `assemble` lehnt gemischte Modi ab; lesende Befehle mit Enum-Werten als Text, neu `host.vm_zustand`, `vm.konto_sids` |
| `scripts/legal_bench/isolation_evidence.py` | geändert | V11-Kette (Rücksetzung vor **allen** VM-Belegen); V13 zählt nur mit gültiger Schlüsselliste des Gates; Bündel mit gemischten Modi ergibt BLOCKIERT |
| `scripts/legal_bench/isolation_gate.py` | geändert | Fingerabdruck des Prüfwerkzeugs umfasst die neuen Dateien und den Test |
| `tests/test_s86_isolation_design.py` | geändert | Export und Politik dürfen im Manifest eingebettet sein, weil die VM keinen Zugriff auf Host-Pfade hat |
| `tests/test_s94_real_acceptance_path.py` | neu | 39 Tests |

### 2.1 Ablauf des echten Modus (nichts davon ausgeführt)

1. Host: `isolation_host_control image-zuruecksetzen --ausfuehren` – Protokoll V11.
2. Host: `isolation_real host-lauf --real` – Listener mit Funktionskontrolle; ACL muss leer sein; Phase „vorher“.
3. VM: `isolation_real vm-teil --phase vorher --real` – Gegenprobe (E0).
4. Host: Enter → Phase „aktivierung“, Gegenprobe (E1); dann `isolation_host_control acl-setzen --ausfuehren` mit
   Aktivierungsprotokoll.
5. Host: Enter → Prüfung des Protokolls (VM, Zeit nach dem Ende der Gegenprobe), V1-Export, Abnahme-Manifest für die
   VM, Phase „nachher“.
6. VM: `vm-teil --phase nachher --manifest …` – §86-, §87- und §90-Tests als JUnit; V2 und V9 (E0); V11-Kurzprüfung.
7. Host: Enter → Listener aus; Kanarien, V9, V10, V11, V12, V13, V2 (E1).
8. Endpunkt (nach D3): `endpunkt-teil`. Danach `buendeln` und `owner_sign urteil`; die Signatur erst nach D4.

### 2.2 Sperren und Fail-closed

- **Echte Schritte** brauchen `LEXONO_ISOLATION_ACCEPTANCE=1` **und** `--real` bzw. `--ausfuehren`. Ohne beides wird
  vor jedem Systemaufruf abgelehnt (getestet: das simulierte System erhält 0 Aufrufe).
- **Richtige Maschine:** `vm-teil` läuft nur auf der Maschine der Konfiguration, `host-lauf` nie dort.
- **Reihenfolge:**
  - „nachher“ ohne „vorher“ wird abgelehnt;
  - ein zweites „vorher“ ebenso;
  - ein Beleg, der zwischen den Phasen verändert wurde, ebenso.
  - Die ACL muss vor der Gegenprobe leer sein.
  - Fehlt das Aktivierungsprotokoll, bricht der Lauf ab; der Teil bleibt offen und ist **nicht bündelbar**.
- **Manifest:** Es ist an Lauf, Maschine, Politik und Prüfwerkzeug gebunden (`lauf_sha256`). Ein manipuliertes oder
  fremdes Manifest bzw. eines ohne ACL-Export lehnt die VM ab.
- **Modi trennen:**
  - Trockenlauf-Belege werden produktiv abgelehnt (`owner_sign urteil`: BLOCKIERT).
  - Gemischte Teile verweigert `assemble`; gemischte Bündel bewertet `evaluate` als BLOCKIERT.
  - Belege im Modus `real` entstehen nur mit der Umgebungsfreigabe.
- **Signatur:** `owner_sign` signiert nur bei PASS aller 13. Ein reales Bündel ohne V13 ist nicht PASS.
- **Provider:** Der Treiber stoppt weiterhin vor jedem Provider-Aufruf, solange das Gate nicht bestätigt (§87–§92-Tests
  unverändert grün).

### 2.3 PowerShell-Normalisierung

- **`normalize_ps_json`:**
  - leere Ausgabe oder `null` ergibt `[]`;
  - ein Objekt ergibt `[Objekt]` (PowerShell liefert bei genau einem Ergebnis kein Array);
  - eine Liste bleibt eine Liste;
  - BOM und Leerraum werden entfernt, Schlüssel sortiert.
  - Kein JSON, Skalare und verschachtelte Listen führen zu einem Fehler; die Beobachtung wird zum Fehler, also nie PASS.
- **`normalize_acl`:** Feste Felder und Typen. Enum-Werte als **Zahl** (Verhalten von Windows PowerShell 5.1) werden
  **abgelehnt** statt geraten; die Lesebefehle wandeln Enums deshalb ausdrücklich in Text um. Die Reihenfolge ist
  deterministisch.
- **Ein-Element-Listen:** Eine Liste mit einem Element in Objekten (z. B. Netzadapter der VM) wird deterministisch als
  Liste behandelt.

### 2.4 Port-ACL (`isolation_host_control`)

- **Soll:**
  - Deny ausgehend `0.0.0.0/0` (Weight 1) und `::/0` (Weight 2);
  - Allow nur für Endpunkte einer **FREIGEGEBENEN** Politik (TCP, Stateful, ab Weight 10).
  - Aus der heutigen Politik (ENTWURF) entstehen nur die zwei Deny-Regeln.
- **`acl-setzen`:**
  - Die Ist-ACL muss leer sein; sonst wird abgebrochen, ohne etwas zu ändern.
  - Danach Soll setzen, erneut lesen und vergleichen.
  - Weicht das Ergebnis ab, wird **automatisch zurückgebaut** (getestet: ein verlorener IPv6-Deny führt zum Rückbau,
    die ACL ist danach leer).
  - Das Aktivierungsprotokoll wird exklusiv geschrieben.
- **`acl-entfernen`:** Entfernt nur die eigenen Einträge; fremde Einträge führen zum Abbruch. Danach muss die ACL leer
  sein.
- **`acl-plan`:** Zeigt die Befehle nur an.

### 2.5 V11 und V13

- **V11:**
  - Die VM muss aus sein.
  - Die Eltern-VHDX wird vor und nach der Rücksetzung gehasht (nur gelesen); der Differenzdatenträger wird neu angelegt.
  - Ein Fehlschlag wird protokolliert und ergibt FAIL.
  - Ein verändertes Golden Image ergibt FAIL.
  - Liegt die Rücksetzung nach VM-Belegen, ergibt das BLOCKIERT.
- **V13:**
  - Das Produktiv-Gate wird mit einer absichtlich veralteten Bestätigung geprüft, eine Stunde nach deren Ablauf.
  - Gewertet wird das nur, wenn die Schlüsselliste des Gates gültig war. Ohne gepinnten Wurzelschlüssel (heute) ist das
    Ergebnis „unklar“, also nie PASS.

## 3 Testbefunde

| Prüfung | Ergebnis |
|---|---|
| `tests/test_s94_real_acceptance_path.py` (39) | **PASS** |
| – Gesamtablauf mit simuliertem System und echtem Loopback-Listener | V1–V12 PASS, **V13 BLOCKIERT** (kein V13-Beleg ohne D4), Gesamt BLOCKIERT; Owner-Werkzeug würde nicht signieren |
| – übersprungener Abnahmetest | V6 FAIL |
| – Reihenfolge, Maschine, Manifest, Belegänderung zwischen Phasen, ACL nicht leer, fehlendes Aktivierungsprotokoll | jeweils abgelehnt bzw. Teil nicht bündelbar |
| – Sperren ohne Freigabe; gemischte Modi; Trockenlauf nie signierbar | abgelehnt |
| – Konfiguration (9 Fehlerfälle), Manifest-Bindung, Politik im ENTWURF ohne Endpunkt | abgelehnt bzw. kein Endpunkt erfunden |
| – PowerShell- und ACL-Normalisierung, SID-Erkennung | deterministisch, unklare Werte abgelehnt |
| – ACL setzen, prüfen, entfernen; Abweichung mit Rückbau; fremde Einträge; Plan ohne Ausführung | wie erwartet |
| – V11 (VM an, Fehlschlag, verändertes Image, falsche Reihenfolge), V13 ohne Wurzel, unvollständiges Endpunkt-Log | nie PASS |
| Isolationstests §86–§94 zusammen | **PASS** (257 bestanden, 7 übersprungen = Zielumgebung) |
| Benchmark-Tests §77–§85 | **PASS** (162 + 153) |
| volle Suite in 6 Teilen | 5626 bestanden, **4 FAIL** – die bekannten fremden Logo-Tests (`tests/test_design_refresh.py`), nicht behoben |

**Hinweis zur Prüfung der Konfiguration:** Ich habe die Prüfung „VM- und Host-Adressen überschneiden sich“ wieder
entfernt. Die Tests müssen auf einem einzigen Rechner (Loopback) laufen. Real ist eine Überschneidung im selben Netz
ohnehin nicht möglich und würde in der Gegenprobe auffallen.

## 4 Voraussetzungen für den späteren Echtbetrieb

| Voraussetzung | Status |
|---|---|
| F1: §90–§94 committen; Host und VM auf demselben Commit (sonst stimmt der Werkzeug-Fingerabdruck nicht) | NICHT DURCHGEFÜHRT |
| Konfiguration `lexono-isolation-abnahme-konfig/1` mit echten Werten (VM-Name, Identität, Adressen, Ports, Golden-Image-Pfade und Hash, Protokollpfade) | NICHT DURCHGEFÜHRT – Werte gibt es erst nach D1 |
| D1-Schritte aus AUDIT_S93 (Updates, Sicherung, Speicher, Hyper-V, Switch, VM, Golden Image, Firewall-Regeln) | BLOCKIERT (deine Freigaben F2–F8) |
| Übertragung von Manifest und Belegen zwischen Host und VM (z. B. Offline-Daten-VHDX) | NICHT DURCHGEFÜHRT – Ablauf festgelegt, nicht erprobt |
| D3 (Endpunkt/Relay) für V1-Allow, V3 und Stufe B | BLOCKIERT |
| D4 (Schlüssel, Wurzel-Pin) für V13 und die Signatur | BLOCKIERT |

## 5 Offene Risiken und nicht durchgeführte Prüfungen

1. **Parameter und Semantik von `Add-/Remove-VMNetworkAdapterExtendedAcl` auf Windows 11:** NICHT VERIFIZIERT. Das
   betrifft das Entfernen über Weight und Richtung, die Eindeutigkeit der Weights und Stateful. Der Rückbau prüft
   deshalb immer per erneutem Lesen.
2. **Echte PowerShell-Ausgabeformate:** Die Tests nutzen nachgebildete Ausgaben; die realen Formate sind nicht geprüft.
   Unbekannte Formen führen zu Fehlern, nicht zu PASS.
3. **Native IPv6-Probe:** Die §86-Probe „native“ (ctypes) behandelt nur IPv4. Für IPv6-Ziele liefert sie „blockiert“,
   ohne etwas zu prüfen. Der IPv6-Kanal stützt sich deshalb auf TCP/UDP-Proben und die Gegenprobe; „native“ gilt für
   IPv6 als **nicht abgedeckt**.
4. **Der V2-Änderungsversuch** ist in einer fehlerhaften VM eine echte Änderung. Das Ergebnis ist dann FAIL, und die VM
   muss zurückgesetzt werden.
5. **`host-lauf` wartet auf Eingaben:** Ein abgebrochener Lauf hinterlässt offene Teile. Sie sind nicht bündelbar, also
   sicher, müssen aber verworfen werden. Die Firewall-Regeln werden nicht automatisch entfernt; der Rückbau ist Schritt
   C6 aus AUDIT_S93.
6. **E1-Herkunft und Absenderfälschung:** wie in §91 bis §93 beschrieben; durch §94 unverändert.
7. **Ergebnisse der Simulation:** Das PASS von V1–V12 im Gesamttest stammt aus simulierten Systemantworten. Es ist
   **kein Messergebnis** und kein Nachweis über Hyper-V.

## 6 Ergebnisübersicht

| Punkt | Ergebnis |
|---|---|
| CLI für Host, VM und Endpunkt, Manifest-Generator, Normalisierung, V11/V13, ACL-Steuerung | **PASS** (lokal, simuliert) |
| Fail-closed von der Erfassung bis zu Gate und Signatur (Trockenlauf, Reihenfolge, Manipulation, Alter, Fehler) | **PASS** (lokal) |
| volle Suite | **PASS** bis auf 4 FAIL (bekannte fremde Logo-Tests) |
| Echtbetrieb gegen eine reale VM | **NICHT DURCHGEFÜHRT** (BLOCKIERT bis F1, D1) |
| V13 real, Signatur | **BLOCKIERT** (D4) |
| V1–V13 real, Isolation, Sicherheitsabnahme | **NICHT DURCHGEFÜHRT** |
| C-14 | **GESPERRT** |
