# §97 Unabhängiger Sicherheitsreview der §96-Fixes vor F1

Stand 20.10.2026. Branch `feature/feedback-diagnose`, HEAD `b7e2daa` (geprüft; §90–§96 **uncommittet**, 30 Einträge in
`git status`).

**Grenzen dieser Arbeit:**
- Rein lesende Prüfung. Code, Tests und bestehende Berichte habe ich nicht geändert.
- Keine Systemänderungen, keine VM, keine Schlüssel, keine externen Verbindungen.
- Die Produktivdatenbank wurde nicht geöffnet.
- Nachweise stammen aus Wegwerf-Tests im Job-Ordner (`review97/test_poc97.py`, außerhalb des Repositorys) gegen das
  **simulierte** System aus §94 und Loopback.
- **Keine Sicherheitsfreigabe.** F1, D1 und C-14 bleiben gesperrt.

**Einstufung:**
- *bestätigt* = reproduziert bzw. eindeutig im Code;
- *plausibel* = abgeleitet, nicht reproduziert;
- *nicht verifiziert* = hängt von realem Windows-/Hyper-V-Verhalten ab.

## 1 Geprüfte Bereiche und Tests

- **Geprüfte Dateien:** `isolation_host_control.py`, `isolation_real.py`, `isolation_acceptance.py`,
  `isolation_evidence.py`, `isolation_gate.py`, `owner_sign.py`, `owner_keys.py`, `tests/test_s86_isolation_design.py`
  (Proben).
- **Vorhandene Tests (heute ausgeführt):** §86–§96 – **331 bestanden, 7 übersprungen** (Zielumgebung). Simuliert bzw.
  synthetisch.
- **Review-Nachweise P1–P11:** 11 Wegwerf-Tests, alle ausgeführt; Ergebnisse in §2.

## 2 Befunde

### HOCH

**H1 – Die Host-Firewall wird nach der Nachher-Phase nicht erneut geprüft; ein „kein Treffer“ kann von ihr statt von
der ACL stammen.** *bestätigt (Logik)*

- **Code:** `isolation_real.host_lauf`. `fw_pruefen` läuft nur beim Start, danach nicht mehr.
- **Nachweis P4:** Werden die Kanarien-Regeln während der Nachher-Phase entfernt, ergibt das simulierte Gesamturteil
  weiterhin V1–V12 PASS.
- **Warum das ein falsches PASS ist:** Real würde die Host-Firewall dann eingehende Pakete verwerfen. Die VM-Proben sähen
  eine Zeitüberschreitung, der Listener keinen Treffer. Das ist dasselbe Bild wie bei einer wirksamen ACL – ohne dass die
  ACL etwas bewiesen hat. Die Gegenprobe belegt den Pfad nur für die Vorher-Phase.
- Dasselbe gilt für eine Funktionskontrolle des Listeners am Ende der Phase: Es gibt keine.
- **Vorhandene Tests:** keine.
- **Empfehlung:**
  - Firewall-Regeln am Ende von „nachher“ (und am Ende von „vorher“) erneut prüfen und als E1-Beleg aufnehmen
    (Ist gleich Soll, unverändert seit dem Setzen).
  - Am Ende jeder Phase eine Positivkontrolle des Listeners durchführen.
  - Beides zur Pflicht für alle Kanarientests machen.

**H2 – Der Fingerabdruck deckt den tatsächlich ausgeführten Benchmark-Code nicht vollständig ab.** *bestätigt*

- **Code:** `isolation_gate.TOOL_GLOBS` deckt nur `scripts/legal_bench/*.py`, `app/ai_providers/*.py` und
  `app/privacy/*.py` ab.
- **Nachweis P7 und `grep`:** `scripts/legal_bench/e2e.py` (vom Treiber genutzt) importiert u. a. `app/documents`,
  `app/ingestion`, `app/chat`, `app/drafting`, `app/export`, `app/research`, `app/search`, `app/matching`, `app/models`
  und `app/config`. Keine dieser Dateien ist im Fingerabdruck; ebenso wenig `tests/conftest.py` (lädt bei den
  E0-Abnahmetests) und `scripts/__init__.py`.
- **Weitere Lücke:** `.pth`-Dateien in `.venv/Lib/site-packages` (vorhanden: `coloredlogs.pth`,
  `distutils-precedence.pth`, `__editable__.*.pth`) führen beim Start Code aus.
- **Folge:** Änderungen an diesen Dateien im Arbeitsbaum fallen weder beim Signieren (Commit-Abgleich gilt nur für die
  Fingerabdruck-Dateien) noch im Gate auf. Die Zusicherung „Ein Commit-Hash allein darf Änderungen am Arbeitsbaum nicht
  verdecken“ ist damit **nicht erfüllt**.
- **Vorhandene Tests:** `test_tool_fingerprint_covers_execution_relevant_files` prüft nur die enthaltenen Dateien, nicht
  die Vollständigkeit.
- **Empfehlung:**
  - Den Fingerabdruck über **alle versionierten Dateien** bilden, die zur Laufzeit importiert werden können (mindestens
    `app/**`, `scripts/**`, `tests/conftest.py`, die Konfigurationsdateien).
  - Ein „sauberer Arbeitsbaum“ für **alle** versionierten Dateien ist Voraussetzung für das Signieren auf dem Host.
  - `requirements`/Lock-Datei sowie die Hashes von `site-packages` und `.pth` (bzw. eine Wheel-Hash-Liste) und die
    Python-Version einbinden.

### MITTEL

| # | Befund | Einstufung | Datei | Nachweis | Test vorhanden | Empfehlung |
|---|---|---|---|---|---|---|
| M1 | Der Schreibtest prüft nur Repository-Wurzel, `scripts/legal_bench` und `.venv` (oberste Ebene), nicht `site-packages`, `.pth`, `app/` oder `tests/`. Ein beschreibbares `site-packages` bleibt unentdeckt | bestätigt | `isolation_real.observe_write_access` | P8: 2 Ordner geprüft | nein | Alle Ordner des Fingerabdrucks und `site-packages` prüfen; besser die ACL der Ordner auslesen (`Get-Acl`) statt nur zu probieren |
| M2 | Ein Prozess-Timeout (`subprocess.TimeoutExpired`) der §86-Probe wird als `zeitueberschreitung` gewertet, also als Sperre. Ein hängender Probenprozess zählt damit wie verworfene Pakete | bestätigt | `tests/test_s86_isolation_design.probe` | P5 | nein | Prozess-Timeout als `fehler` werten; nur der Netzwerk-Timeout des Kindprozesses (Exit 4) ist eine Sperre |
| M3 | Die DNS-Kindprobe wertet `socket.gaierror` als `zeitueberschreitung`. Ohne konfigurierten Resolver (Soll in der VM) ist das sofort und ohne jedes Paket der Fall – die Probe sagt für V7 nichts aus. Belastbar ist nur der nslookup-Teil gegen den Host-Kanarienresolver | bestätigt (Code) | `test_s86 _CHILD["dns"]` | Code | nein | `getaddrinfo` als „nicht aussagekräftig“ kennzeichnen bzw. aus dem Sperrnachweis nehmen; V7 allein auf nslookup und Host-Kanarien stützen |
| M4 | Rückbau nach Absturz: `KeyboardInterrupt` (oder ein Prozessende) während `acl-setzen` hinterlässt eine Teil-ACL ohne Protokoll und ohne Rückbau (`except Exception` fängt keine BaseException) | bestätigt | `isolation_host_control.acl_setzen` | P3: 1 Deny-Eintrag, kein Protokoll | nein | Rückbau in `finally` bzw. für `BaseException`; vor jeder Änderung ein Absichtsprotokoll schreiben („Rückbau nötig“), damit `startcheck` die Herkunft eindeutig zuordnet |
| M5 | Reste eines **früheren** Laufs lassen sich mit den Werkzeugen nicht entfernen: `fw-entfernen` und `acl-entfernen` erkennen nur eigene Einträge des **aktuellen** Laufs (`run_id` bzw. aktuelle Politik). Der Startcheck meldet sie, ein dokumentierter, sicherer Wiederherstellungsweg fehlt | bestätigt | `isolation_host_control.fw_entfernen`, `acl_entfernen` | P2: „fremde Regeln … nichts wird entfernt“ | teilweise (`test_firewall_leftovers…`) | `fw-entfernen --lauf <alte run_id>` über die Beschreibung (`run_id=`) zulassen; Wiederherstellung in AUDIT/README dokumentieren; nie pauschal die Gruppe löschen |
| M6 | `lauf_status.json` wird vom nächsten Lauf im selben Ordner überschrieben. Der Rückbauauftrag des abgebrochenen Laufs geht verloren. Außerdem nennt der Status `acl-entfernen` schon, wenn nur die Aufforderung bestätigt, die ACL aber nicht gesetzt wurde | bestätigt | `isolation_real.host_lauf` | P9: erster Status `acl-entfernen, fw-entfernen`, zweiter nur `fw-entfernen` | nein | Status exklusiv und je Lauf (Zeitstempel/Nummer) schreiben, nie überschreiben; Rückbauauftrag aus dem tatsächlichen Ist-Zustand ableiten (ACL/Firewall lesen) |
| M7 | M8-Regelbewertung: Regeln mit leerer oder unlesbarer Portliste gelten als ungefährlich. Programm-, Adress- und Dienstfilter werden ignoriert | bestätigt | `isolation_evidence._risky_rules` | P6: leere Portliste ergibt `[]` (keine Gefahr) | nein | Leere oder unklare Filter als `None` (unklar) werten |
| M8 | V9 deckt Hyper-V-Kanäle **neben** dem Netzwerk nicht ab: Hyper-V-Sockets (hvsocket/VMBus), KVP-Datenaustausch und weitere Integrationsdienste außer „Guest Service Interface“. Sie umgehen die Port-ACL | plausibel | `REAL_COMMANDS["host.vm_konfiguration"]`, `_v9_e1` | Code | nein | Alle Integrationsdienste erfassen (Soll: aus, bis auf ausdrücklich nötige); hvsocket-Dienste am Host bewerten |
| M9 | Der Gegenprobe-/ACL-Nachweis ist IP-basiert. Nicht-IP-Frames, Broadcast/Multicast (DHCP, LLMNR, mDNS, NetBIOS) und Link-Local-Ziele werden nicht geprüft; ebenso wenig MAC-Spoofing, DHCP- und Router-Guard am VM-Adapter | plausibel / nicht verifiziert | `isolation_evidence.KANAELE`, `REAL_COMMANDS` | Code | nein | Adaptereinstellungen (`MacAddressSpoofing`, `DhcpGuard`, `RouterGuard`) als V9-Kriterien; verbleibende Pfade als „nicht abgedeckt“ im Urteil ausweisen |

### NIEDRIG

| # | Befund | Einstufung | Nachweis | Empfehlung |
|---|---|---|---|---|
| N1 | `startcheck` prüft – entgegen der eigenen Beschreibung – nicht, ob der Kinddatenträger existiert (Rest nach abgebrochener Rücksetzung) | bestätigt (P1) | P1: `ok: true` ohne Kinddatenträger | prüfen oder die Beschreibung korrigieren |
| N2 | Die Ablaufzeit der Firewall-Regeln ist nur ein Text; Windows entfernt die Regeln nicht. Nach einem Absturz bleiben sie unbegrenzt bestehen, bis jemand `startcheck` ausführt | bestätigt (Code) | Code | geplante Aufgabe oder Anmeldeprüfung für Reste; mindestens deutlicher Hinweis in der Anleitung |
| N3 | Die nslookup-Auswertung erkennt eine Zeitüberschreitung u. a. am Teilstring `Zeit` – zu weit | bestätigt (Code) | Code | exakte Meldungen bzw. Exit-Code und Antwortanalyse |
| N4 | Während der Funktionskontrolle (`_checking`) wird ein gleichzeitiger fremder Treffer auf dasselbe Ziel als Kontrolle gezählt | bestätigt (Code) | Code | Kontrolle nur mit Absender gleich Host-Adresse und Kennung im Paket werten |
| N5 | Das Belegalter (24 h) vergleicht die Uhr der VM-Belege mit der Signaturzeit des Hosts. Das Gate in der VM nutzt die VM-Uhr gegen die Host-Gültigkeit; ein Administrator in der VM kann die Uhr verstellen | bestätigt (P11) | P11 | Uhrenversatz als Beleg erfassen; Gültigkeit zusätzlich über Tokens bzw. einen Host-Gegencheck absichern |
| N6 | Die V9-Gateway-Prüfung erfasst nur Standard-Gateways, keine spezifischen Routen | bestätigt (Code) | Code | `Get-NetRoute` als Beobachtung ergänzen |

### Grenzen des Modells (keine Mängel der Fixes, aber ausdrücklich)

- **Administrator in der VM:** kann jede E0-Beobachtung fälschen, einschließlich des V2-Admin-Kriteriums (P10:
  gefälschte SIDs ergeben `true`), Uhr, Tokens und Proben. Gegen einen Administrator in der VM schützen nur die
  E1-Belege (Kanarien, ACL-Export) und ein Golden Image ohne Administratorzugang.
- **Absenderfälschung:** Vorher-Treffer zählen nur mit VM-Adresse; eine gefälschte Adresse kann eine Vorher-
  Erreichbarkeit vortäuschen. Nachher zählt jede Quelle (konservativ).
- **Host-Belege (E1):** nicht unabhängig vom Owner; ein kompromittierter Host kann alles fälschen.

## 3 Zusammenfassung je Prüfbereich

| Bereich | Ergebnis |
|---|---|
| 1 Rückbau und Absturz | **FAIL** – M4, M5, M6, N1, N2. ACL- und Firewall-Reste werden vom Startcheck erkannt; der Rückbauauftrag ist aber nicht absturzsicher, nicht gegen Überschreiben geschützt und alte Läufe sind nicht entfernbar |
| 2 Fingerabdruck und Commit-Grenze | **FAIL** – H2, M1. Für die erfassten Dateien gilt: Host signiert nur den Commit-Inhalt, VM-Inhalt = Host-Inhalt (**PASS**). Die Abdeckung ist aber unvollständig |
| 3 V9 und Windows-Firewall | **FAIL** – M7, M8, M9, N6. `NotConfigured` führt erwartungsgemäß zu FAIL (unklar); reale Standardregeln **NICHT VERIFIZIERT** |
| 4 Kanarien und Phasen | **FAIL** – H1, M2, M3. Drain, Echo, Tokens und Treffer außerhalb der Phasen funktionieren lokal (**PASS**, 331 Tests); nicht getestete Pfade sind in M8/M9 benannt |
| 5 Gate und Bewertung | **PASS** für FAIL/BLOCKIERT/NICHT DURCHGEFÜHRT, V11, V13 (BLOCKIERT ohne Kette), Signatur nur bei PASS, Trockenlauf nie produktiv; **Einschränkung** N5 und Grenzen des Modells |
| 6 Tests | **PASS** (331 bestanden, 7 übersprungen); 11 Review-Nachweise ausgeführt |
| Reale Abnahme, Isolation | **NICHT DURCHGEFÜHRT** |
| Sicherheitsfreigabe | **keine** |

## 4 Empfehlung vor F1

Vor F1 (Commit) beheben oder bewusst als Restrisiko annehmen:
1. **H2** (Fingerabdruck über allen ausführbaren Code und die Abhängigkeiten) – F1 legt diesen Stand fest.
2. **H1** (erneute Prüfung von Firewall und Listener am Phasenende).
3. M2 und M3 (Semantik der Proben), M4–M6 (Rückbau absturzsicher und lauftreu), M1 (Schreibtest).

M7–M9 und N1–N6 können vor D1 nachgezogen werden; sie verhindern bis dahin ohnehin kein PASS, das ohne reale VM nicht
entstehen kann.
