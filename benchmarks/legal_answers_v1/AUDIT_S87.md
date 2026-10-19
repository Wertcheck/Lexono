# §87 Externes Isolations-Gate (I12) und VM-Abnahme

Stand 19.10.2026, Ausgangsstand `951e3e4` (§86, verifiziert: HEAD = Remote, Working Tree nur mit den 4 fremden
Logo-/UX-Dateien), Branch `feature/feedback-diagnose`; `main` unverändert (`5735e5b`).

**Grenzen dieser Arbeit:**
- Es gab keine Installation, keine VM, keine Firewall-, Konto-, Richtlinien- oder Systemänderung, keine Modell- oder
  Cloud-Aufrufe, keine externen DNS-Abfragen und keine Kosten.
- **Die isolierte VM existiert nicht; keine Abnahme ist durchgeführt.** C-14 bleibt GESPERRT.
- J1–J6, F-1, C-7 und C-11 sind unverändert offen.
- Es waren Backend-Tests ohne Screenshots. Protokoll: `audit/s87_gate_tests.txt`.

| Bereich | Ergebnis |
|---|---|
| I12-Gate implementiert und im Treiber vor Start und vor **jedem** Aufruf verdrahtet | **PASS** |
| Produktiv-Gate heute | **geschlossen** (kein Vertrauensanker, Politik ENTWURF, kein Endpunkt, DNS offen, keine Bestätigung) – gewollt |
| Negativfälle (fehlend, manipuliert, veraltet, fremd, widersprüchlich, geänderte Umgebung, Endpunkt, Firewall, Konfiguration) | **PASS** – jeder bricht vor dem Provider-Aufruf ab |
| Vertrauensanker der Prüfstelle | **NACHWEIS FEHLT**: kann nur die IT bereitstellen; ohne ihn bleibt das Gate zu |
| VM-Abnahme V1–V13 | **NICHT DURCHGEFÜHRT / NICHT VERIFIZIERT** – Matrix festgelegt |
| Volle Suite | **PASS** bis auf die 4 bekannten, fremden Logo-Tests |

## 1 I12-Design und Vertrauensmodell

**Komponente:** `scripts/legal_bench/isolation_gate.py`. Der Treiber (`answer_driver.BenchmarkDriver`) prüft das Gate:
- beim Start, vor Ablage, Plan und jedem Aufruf, vollständig einschließlich Gast-Firewall;
- **vor jedem einzelnen Aufruf** erneut: Signatur, Gültigkeit, Maschine, Politik und Endpunkt. Die Gast-Firewall wird
  dabei nicht erneut gelesen, denn das bräuchte einen Prozessstart, den der Prozesszaun zu Recht sperrt.

Ohne ausdrücklich übergebenes Gate gilt **immer** das Produktiv-Gate aus der Umgebung.

**Vertrauensanker:**
- Der Anker besteht aus öffentlichen Ed25519-Schlüsseln der **Prüfstelle (IT)**, mit `key_id`, Inhaber, Rolle und
  Ablaufdatum.
- Bereitgestellt wird er als Datei **außerhalb** des Benchmarks (`LEXONO_ISOLATION_TRUST_ANCHORS`), die für das
  Benchmark-Konto **schreibgeschützt** sein muss. Das Gate prüft das selbst: Lässt sich die Datei vom laufenden Prozess
  zum Schreiben öffnen, gilt sie nicht als Anker.
- Der private Schlüssel liegt nur bei der Prüfstelle.

**Bestätigung:** ein signiertes, kanonisches JSON (`LEXONO_ISOLATION_ATTESTATION`). Es bindet:

| Bindung | Prüfung |
|---|---|
| Lauf | `run_id` = Lauf-ID der Konfiguration |
| Maschine bzw. VM | Hostname **und** MachineGuid (ohne GUID: geschlossen) |
| Netzwerkkonfiguration | SHA-256 der freigegebenen `netzwerkpolitik.json`; Politik muss Status `FREIGEGEBEN` haben, mit Endpunkt und DNS-Entscheidung |
| Host- bzw. Perimeterregeln | SHA-256 des Regel-Exports vom Host (Pflichtfeld; der Export selbst wird bei V1/V10 geprüft) |
| Endpunkt | bestätigte Endpunkte = Endpunkte der Politik; jeder Provider-Endpunkt muss bestätigt sein |
| DNS | DNS-Modus = Entscheidung in der Politik |
| Prüfer | Name und Rolle = Inhaber des Signaturschlüssels |
| Prüfergebnis | V1–V13 alle „bestanden“ |
| Gültigkeit | jetzt innerhalb von `gueltig_von`…`gueltig_bis`, Fenster höchstens 24 h; Schlüssel nicht abgelaufen |
| Integrität und Herkunft | Ed25519-Signatur über das **gesamte** Payload mit einem Ankerschlüssel; jedes nachträglich geänderte Feld macht sie ungültig (getestet) |
| Plausibilität | Gast-Firewall in allen Profilen aktiv und ausgehend „Block“; ein Widerspruch schließt das Gate |

**Test und Betrieb getrennt:**
- Test-Gates (`IsolationGate.for_tests`) haben Anker nur im Speicher und verlangen eine test-markierte Bestätigung.
  Sie lassen **nur Loopback-Endpunkte** zu.
- Das Produktiv-Gate lehnt test-markierte Bestätigungen ab (getestet mit einem schreibgeschützten Anker aus einem
  Testschlüssel).
- Die Endpunktsperre `RealCloudDisabled` (C-14) und der Netz- und Prozesszaun bleiben unabhängig davon bestehen; auch
  eine gültige Bestätigung schaltet heute keinen echten Lauf frei.

**Was das Gate nicht leisten kann:**
- Es belegt nur, dass **die Prüfstelle signiert** hat, nicht, wie gründlich sie geprüft hat.
- Wird der private Schlüssel gestohlen oder ist der Anker doch schreibbar (etwa bei Administratorrechten in der VM),
  bricht das Modell.
- Den Firewall-Export kann das Gate in der VM nicht selbst nachrechnen; er ist nur über die Signatur gebunden.

## 2 Tests

`tests/test_s87_isolation_gate.py` (37 Tests, alle bestanden):
- **Produktivzustand:**
  - Das Gate ist heute geschlossen; alle fünf Gründe werden genannt.
  - Ein Treiber ohne Gate-Parameter stoppt vor allem, ohne Ablage und mit 0 Payloads.
  - Ein schreibbarer Anker wird nicht anerkannt.
  - Das Produktiv-Gate akzeptiert keine Test-Bestätigung.
- **Positivfall:** nur Mock, Test-Gate, Loopback.
- **20 Negativfälle:**
  - fehlende bzw. unlesbare Bestätigung;
  - fremde Prüfstelle;
  - nachträglich manipuliert, Signatur vertauscht;
  - abgelaufen, noch nicht gültig, Fenster über 24 h;
  - anderer Lauf, andere Maschine;
  - ohne Firewall-Export;
  - anderer Endpunkt, anderer DNS-Modus;
  - Prüfer nicht Schlüsselinhaber;
  - Abnahme unvollständig bzw. nicht bestanden;
  - unbekanntes Schema, nicht test-markiert;
  - Gast-Firewall offen bzw. unbekannt.
- **Weitere Fälle:**
  - geänderte Politik;
  - nicht bestätigter Provider-Endpunkt;
  - Test-Gate mit Nicht-Loopback-Ziel (nur als Daten, keine Verbindung);
  - unlesbare Firewall;
  - abgelaufener Ankerschlüssel;
  - Maschine ohne GUID;
  - Signatur deckt **jedes** Feld.
- **Treiber:**
  - 4 Fehlerarten stoppen vor dem ersten Aufruf mit 0 Payloads.
  - Läuft die Bestätigung **mitten im Lauf** ab oder ändert sich die **Politik im Lauf**, stoppt der Treiber vor dem
    nächsten Aufruf: 1 Payload, dann Stopp.
- **Matrix:** `isolation/vm_abnahme.json` entspricht genau den vom Gate verlangten Tests V1–V13; alle stehen auf „NICHT
  DURCHGEFÜHRT“.

**Angepasst:** `tests/test_s80_benchmark_driver.py` und `tests/test_s81_benchmark_hardening.py` übergeben für ihre
Mock-Läufe ein Test-Gate (`tests/isolation_test_support.py`, nur Loopback). Die übrigen Treiber-Tests (§80–§85) bleiben
unverändert grün; das gilt auch für den Recorder-Lauf, dessen Loopback-Port bestätigt ist.

## 3 VM-Abnahme V1–V13

Vollständig in `isolation/vm_abnahme.json`. Für jeden Test sind Sollzustand, Durchführung, Beleg und Rolle festgelegt;
der Status ist überall **NICHT DURCHGEFÜHRT**.

| # | Sollzustand (Kurzform) | Beleg | Rolle |
|---|---|---|---|
| V1 | Host/Perimeter: Standard blockieren (IPv4, IPv6, alle Protokolle), nur die Ausnahme laut Politik | signierter Regel-Export und Prüfprotokoll | IT |
| V2 | Gast-Firewall: aktiv, ausgehend Block, per Richtlinie gesperrt; Benchmark-Konto ohne Admin | Ausgaben und gescheiterter Änderungsversuch | IT |
| V3 | freigegebener **Test**-Endpunkt erreichbar (kein Modell-Endpunkt) | pytest-Protokoll und Dienst-Log | prüfende Stelle |
| V4 | nicht freigegebene IPv4/TCP-Ziele blockiert | Protokoll und Kanarien-Log ohne Treffer | prüfende Stelle und IT |
| V5 | IPv6 blockiert | dito | prüfende Stelle und IT |
| V6 | UDP blockiert | dito | prüfende Stelle und IT |
| V7 | DNS blockiert (53, 853, DoH, LLMNR, mDNS, NetBIOS) | Protokoll und Resolver-/Kanarien-Log | prüfende Stelle und IT |
| V8 | native Aufrufe und Unterprozesse blockiert (getrennte Prozesse) | Protokoll und Kanarien-Log | prüfende Stelle und IT |
| V9 | alternative Pfade geschlossen (Adapter, WLAN, Bluetooth, USB, VPN, Host-Ordner, Zwischenablage, RDP, Update, Telemetrie, NTP) | Host-Konfigurationsexport und Adapterliste | IT |
| V10 | Host-Regeln unabhängig vom Gast geprüft; aus der VM nicht änderbar | Export, Protokoll, gescheiterter Versuch | IT-Sicherheit |
| V11 | Wiederherstellung des Golden Image; danach V1–V10 erneut bestanden | Image-Hash und Wiederholungsprotokolle | IT |
| V12 | Kanarien-Logs durch die IT ausgewertet: kein Treffer | unterschriebener Auswertungsbericht | IT |
| V13 | Start-Gate in der VM: ohne bzw. mit ungültiger Bestätigung kein Start | pytest-Protokoll und Diagnose | prüfende Stelle |

## 4 Offene IT- und Owner-Entscheidungen

1. **Prüfstelle** benennen (IT-Sicherheit o. ä.), Ed25519-Schlüssel erzeugen und schützen (Token/HSM), Anker-Datei
   schreibgeschützt bereitstellen, Rotation und Widerruf regeln. Das hängt mit F-1 zusammen (Nachweis von Freigaben).
2. Zielumgebung bereitstellen (VM auf IT-Host, §86): Host-/Perimeter-Firewall, Golden Image, Kanarienziele,
   Manifest für die Abnahmetests.
3. Netzwerkpolitik freigeben: Endpunkt (IP/Port bzw. FQDN-Filterung am Perimeter), DNS-Modus, Verwaltende. Danach
   Status `FREIGEGEBEN`; jede Änderung macht bestehende Bestätigungen ungültig.
4. Gültigkeitsdauer der Bestätigung (heute höchstens 24 h) und Ablauf der Bestätigung pro Lauf festlegen.
5. Kosten (Infrastruktur, Arbeitszeit): nicht beziffert, Owner-Entscheidung.

## 5 Restrisiken

- **Prüfstelle:** Das Gate ist nur so stark wie Schutz und Sorgfalt der Prüfstelle (Schlüssel, Abnahme).
- **Administratorrechte in der VM:** Wer sie erlangt, könnte den Anker austauschen. Dagegen hilft nur die Grenze am Host
  bzw. Perimeter (V10) und ein Konto ohne Administratorrechte (V2).
- **Maschinenidentität:** Hostname und MachineGuid sind in einer geklonten VM gleich. Die Bindung an den Lauf und das
  kurze Fenster begrenzen eine Wiederverwendung, verhindern sie aber nicht vollständig. Ein Klon zählt in der Abnahme
  als neue VM.
- **Gast-Firewall:** Sie wird nur beim Start gelesen. Änderungen im Lauf erkennt erst die Grenze am Host bzw. Perimeter.
- **Isolation:** Sie besteht erst, wenn die VM eingerichtet, V1–V13 bestanden und eine echte Bestätigung signiert ist.
  **Heute: nicht vorhanden.**
