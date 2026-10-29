# §92 A4 – Gegenprobe der Host-Port-ACL

Stand 19.10.2026. Branch `feature/feedback-diagnose`, HEAD `b7e2daa` (unverändert, **nicht committet**); `main` = `5735e5b`.
Die Datei liegt wie AUDIT_S87–S91 unter `benchmarks/legal_answers_v1/`.

**Grenzen dieser Arbeit:**
- Nur lokale Code-, Test- und Dokumentationsänderungen.
- Kein Hyper-V, keine VM, keine Änderung an Windows, Firewall, ACL oder Netzwerk.
- Keine Schlüssel, keine externen Verbindungen, keine Provider-Aufrufe. Listener und Proben laufen nur auf Loopback.
- Die Produktivdatenbank wurde nicht geöffnet.
- Die vier fremden Dateien und die bestehenden uncommitteten Berichte blieben unangetastet.
- **Keine reale Isolation oder Sicherheitsabnahme:** V1–V13 bleiben NICHT DURCHGEFÜHRT, C-14 bleibt GESPERRT.

## 1 Problem (aus AUDIT_S91 §6.1)

„Kein Kanarientreffer nach Aktivierung der ACL“ beweist die ACL nur, wenn dasselbe Ziel über denselben VM-Pfad
**vorher** erreichbar war. Sonst könnte auch Folgendes die Ursache sein:
- ein toter Listener;
- die Host-Firewall;
- ein falscher Port;
- ein nie funktionierender Pfad.

Bis §91 prüfte die Funktionskontrolle nur den Listener vom Host aus.

## 2 Umsetzung (im bestehenden Beleg-, Urteils- und Gate-Konzept)

### 2.1 Neue Belegart `gegenprobe`, je Kanal

**Kanäle** sind die Kombinationen aus Adressfamilie und Protokoll (`isolation_evidence.KANAELE`, Ziel → Kanal über
`channels()`):

| Test | Kanäle |
|---|---|
| V4, V8 | ipv4/tcp |
| V5 | ipv6/tcp, ipv6/udp |
| V6 | ipv4/udp |
| V7 | ipv4/dns |
| V12 | alle fünf |

**Pflichtbelege** je Netzwerktest (Klasse wird **genau** verlangt; ein Host-Beleg ersetzt den VM-Versuch nicht):
- **`gegenprobe` E0 (VM, V4–V8):** Vorher-Versuche aus der VM (`calibration_probe`) mit Ziel, Kanälen, Ergebnis und
  Zeit. Mögliche Ergebnisse sind „erreicht“ (TCP), „gesendet“ (UDP/DNS) und „blockiert“.
- **`gegenprobe` E1 (Host, V4–V8 und V12):** das Log des Listeners in der Phase „vorher“. Es enthält:
  - Ziele, Funktionskontrolle und Treffer mit Quelle und Phase;
  - das Fenster der Phase;
  - die VM-Quelladressen;
  - den SHA-256 der ACL während der Gegenprobe.

### 2.2 Ablauf (Trockenlauf `dry_run`, später real)

1. **Listener-Start mit Funktionskontrolle**, vor der Gegenprobe.
2. **Phase „vorher“:** Die ACL ist noch nicht aktiv. Die VM probt jedes Ziel; der Host protokolliert die Treffer.
3. **Phase „aktivierung“:** ACL setzen (Trockenlauf: nur synthetisch) und vom Host exportieren. Der Export ist der
   V1-Beleg; sein Zeitpunkt gilt als Aktivierung.
4. **Phase „nachher“:** die Abnahme-Proben der VM. Jeder Treffer gilt als Abfluss trotz ACL.

Treffer werden ihrer Phase zugeordnet (`CanaryListener.mark`, Phasen sind einmalig). Vorher-Treffer zählen nie als
Nachher-Treffer und umgekehrt.

### 2.3 Bewertung (`_eval_gegenprobe`, `_gegenprobe_cross`)

Zu **BLOCKIERT** führen (die Sperre ist der ACL nicht zurechenbar):
- **Fehlende Vorher-Erreichbarkeit:** kein Treffer aus einer VM-Quelladresse im Vorher-Fenster auf einem Ziel des
  Kanals; auch nicht, wenn Treffer von fremden Quellen kommen oder außerhalb des Fensters liegen.
- **VM meldet „schon vorher blockiert“** bzw. hat den Kanal nicht versucht.
- **Toter Listener:** Ziel ohne Funktionskontrolle.
- **Fehlender Beleg:** Gegenprobe E0 oder E1 fehlt, oder der ACL-Export (V1) fehlt.
- **Unveränderte ACL:** ACL-Hash während der Gegenprobe gleich der finalen ACL.
- **Reihenfolge verletzt:** ACL-Export vor dem Ende der Gegenprobe, Nachher-Probe vor dem ACL-Export, oder
  Vorher- und Nachher-Fenster überlappen.
- **Nicht gegengeprüftes Ziel:** Ein Nachher-Ziel (anderer Port oder Pfad) ist nicht erfolgreich gegengeprüft, oder ein
  Kanal des Tests fehlt im Nachher-Log. **Eine erfolgreiche Gegenprobe gilt nur für ihr Ziel und ihren Kanal.**
- **Widersprüche:**
  - VM meldet „erreicht“, der Host sah keinen Treffer;
  - VM meldet „blockiert“, der Host sah einen Treffer;
  - verschiedene VM-Adressen;
  - ein Versuch außerhalb des Fensters;
  - ein unklares Ergebnis.

  Widersprüche werden je Kanal des Tests geprüft.

Zu **FAIL** (uneindeutig) führt ein unvollständiges Log: Treffer, Kontrolle, ACL-Hash oder VM-Adressen fehlen, oder
Zeitangaben sind kaputt.

Weiterhin gilt:
- Gültig ist nur die aus der Beobachtung abgeleitete Bewertung (§91). Mitgeschriebene Treffer, die der Beobachtung
  widersprechen, und ein zusätzliches `"status": "PASS"` ändern nichts.
- Manipulierte Hashes ergeben BLOCKIERT.
- Für Belege der Gegenprobe gilt das Höchstalter von 24 h bei der Nutzung (`MAX_EVIDENCE_AGE_AT_USE`).

### 2.4 Gate, Signierwerkzeug, Treiber

Ich habe sie nicht geändert; sie erzwingen die Gegenprobe über `evaluate`:
- **Signierwerkzeug:** Ohne Gegenprobe verweigert `owner_sign` das Signieren („V8 BLOCKIERT“).
- **Gate:** Eine trotzdem signierte Bestätigung lehnt das Gate ab („V5: BLOCKIERT“).
- **Treiber:** Er stoppt vor jedem Provider-Aufruf.

Neu in den Prüfwerkzeug-Fingerabdruck aufgenommen ist `tests/test_s92_acl_counter_probe.py`.

## 3 Geänderte Dateien

| Datei | Änderung |
|---|---|
| `scripts/legal_bench/isolation_evidence.py` | `KANAELE`, `channels`, Belegart `gegenprobe` (Anforderungen mit `genau`), Ableitung, Einzel- und Kettenbewertung, Verdiktstufe „blockiert“, Prüfzeitraum der Kanarien nur aus den Nachher-Proben |
| `scripts/legal_bench/isolation_acceptance.py` | Phasen im `CanaryListener`, `calibration_probe` (Vorher-Probe, ohne Freigabe nur Loopback), `synthetic_gegenprobe`, `collect(select=…)`, Trockenlauf in drei Phasen, Kanarienziel IPv6/UDP, Leck `udp6` |
| `scripts/legal_bench/isolation_gate.py` | Prüfwerkzeug-Dateiliste um `tests/test_s92_acl_counter_probe.py` |
| `tests/isolation_test_support.py` | synthetische Gegenprobe, IPv6/UDP-Ziel, Zeitachse Vorher (60–50 min) → ACL (40 min) → Nachher (ab 35 min) |
| `tests/test_s91_acceptance_dry_run.py` | Alterstest nutzt einen anderen Beleg; ein 23 h alter ACL-Export verletzt jetzt zu Recht die Reihenfolge |
| `tests/test_s92_acl_counter_probe.py` | neu, 31 Tests |

## 4 Ergebnisse

| Prüfung | Ergebnis |
|---|---|
| `tests/test_s92_acl_counter_probe.py` (31): Erfolg; Vorher blockiert (Host bzw. VM); toter Listener; fehlende Belege (E0, E1, V1); fremde Quelle bzw. außerhalb des Fensters; 6 Varianten unvollständiger Logs; unveränderte ACL; Reihenfolge (3 Fälle); nicht gegengeprüftes Ziel bzw. Kanal; Widersprüche (4 Fälle); Manipulation und Handmarkierung; Alter; Trockenlauf mit echter Loopback-Probe; Trockenlauf ohne IPv6-Vorher (nur V5 und V12 BLOCKIERT); Leck nach der ACL (FAIL); Listener-Phasen und Probensperre; Signierwerkzeug, Gate und Treiber | **PASS** |
| Isolationstests §86, §87, §90, §91, §92 zusammen | **PASS** (218 bestanden, 7 übersprungen = Zielumgebung) |
| Benchmark-Tests §77–§85 | **PASS** (162 + 153) |
| volle Suite in 6 Teilen | 5626 bestanden, **4 FAIL** – die bekannten fremden Logo-Tests, nicht behoben |
| CLI-Trockenlauf | ohne Leck: alle 13 PASS (Vorher-Treffer aus 127.0.0.1 und ::1 auf allen fünf Zielen); `--leck udp6` → V4–V8 und V12 FAIL |

Der Trockenlauf belegt nur die Werkzeugkette. Die „VM“ ist dabei der eigene Prozess über Loopback; das ist **kein
Nachweis** über einen Hyper-V-Pfad.

## 5 Verbleibende Risiken

1. **Gegenprobe im echten Ablauf:** Die VM muss vor der ACL die Host-Kanarien erreichen. Dafür braucht es eine
   Host-Firewall-Regel (eingehend zu den Kanarienports am internen Adapter) und eine Phase ohne ACL. Beides sind
   Systemänderungen und gehören zu D1. **NICHT DURCHGEFÜHRT.**
2. **Aktivierungszeitpunkt:** Er wird über den Exportzeitpunkt der ACL angenähert. Wird die ACL erst nach dem Export
   wirksam oder später verändert, fällt das nur über V10 (Vorher/Nachher-Hash) und die Kanarien auf.
3. **Herkunft der Treffer:** Die Quelle wird über die VM-Adressen bestimmt; eine gefälschte Quelladresse auf dem Host
   ist nicht ausgeschlossen. Die Herkunft der E1-Belege sichert weiterhin nur die Signatur des Owners (§90).
4. **Nur Ziele am Host:** Die Gegenprobe deckt nur die gegengeprüften Ziele am Host ab. Ziele im Internet oder andere
   Ports gelten **nicht** als geprüft (bewusst so umgesetzt). Dass die Deny-all-Regel auch dorthin wirkt, bleibt ein
   Schluss.
5. **UDP/DNS:** Die VM kann den Empfang nicht selbst sehen („gesendet“); entscheidend ist allein der Host-Treffer.
6. **Semantik der Hyper-V Extended ACL** (Gewichtung, zustandsbehaftete Regeln, IPv6 Neighbor Discovery und DHCP):
   **NICHT VERIFIZIERT.**
7. **Ausgabeformate:** Die echten Beobachter (PowerShell-Ausgaben) sind gegen eine reale VM noch nicht geprüft.

## 6 Ergebnisübersicht

| Punkt | Ergebnis |
|---|---|
| Gegenprobe als Pflicht je Kanal, fail-closed in Urteil, Signierwerkzeug, Gate und Treiber | **PASS** (lokal, synthetisch bzw. Loopback) |
| Vorher-Erreichbarkeit fehlt, toter Listener, Widerspruch, falsche Reihenfolge, nicht gegengeprüftes Ziel | **BLOCKIERT**, nie PASS (getestet) |
| unvollständiges Log | **FAIL** (uneindeutig), nie PASS (getestet) |
| veraltete oder manipulierte Belege | **BLOCKIERT** (getestet) |
| Gegenprobe über einen echten VM-Pfad | **NICHT DURCHGEFÜHRT** (BLOCKIERT bis D1) |
| V1–V13 real, reale Isolation, Sicherheitsabnahme | **NICHT DURCHGEFÜHRT** |
| C-14 | **GESPERRT** |
