# §95 Sicherheitsreview des Isolations-Abnahmepfads (§90–§94) vor F1

Stand 20.10.2026. Branch `feature/feedback-diagnose`, HEAD `b7e2daa` (geprüft); §90–§94 sind **uncommittet**.

**Grenzen dieser Arbeit:**
- Rein lesende Prüfung. Code, Tests und bestehende Berichte habe ich nicht geändert.
- Keine Systemänderungen, keine VM, keine Schlüssel, keine externen Verbindungen.
- Die Produktivdatenbank wurde nicht geöffnet.
- Belege stammen aus Wegwerf-Skripten im Job-Ordner (`review95_poc.py`); sie liefen nur auf Loopback, ohne
  `LEXONO_ISOLATION_ACCEPTANCE`.
- **Keine Sicherheitsfreigabe.** C-14 bleibt GESPERRT.

**Einstufung:**
- *bestätigt* = lokal reproduziert oder eindeutig aus dem Code;
- *plausibel* = aus dem Code abgeleitet, nicht reproduziert;
- *nicht verifiziert* = hängt von realem Hyper-V-/Windows-Verhalten ab.

## 1 Geprüfte Bereiche und Tests

- **Geprüfte Dateien:** `isolation_gate.py`, `isolation_evidence.py`, `isolation_acceptance.py`, `isolation_real.py`,
  `owner_keys.py`, `owner_sign.py`, `isolation_host_control.py` sowie die Tests §86–§94.
- **Vorhandene Tests (heute ausgeführt):** §86, §87, §90, §91, §92, §94 – **257 bestanden, 7 übersprungen**
  (Zielumgebung). Alle positiven Gesamtergebnisse dieser Tests sind **Simulation**: ein synthetisches bzw. simuliertes
  System und Loopback.
- **Host-Lesebefund (nur lesend):** Auf allen Schnittstellen lauschen u. a. TCP 135 (RPC), 445 (SMB), 623 und 16992
  (Intel AMT/LMS), 5040, 7680 sowie dynamische RPC-Ports; `IPEnableRouter` = 0.

## 2 Befunde nach Schweregrad

### HOCH

**H1 – Kanarien-Treffer kurz vor dem Ende des Listeners gehen verloren (falsches PASS möglich).** *bestätigt*

- **Code:** `isolation_acceptance.py`, `CanaryListener.__exit__` und `_serve`. `__exit__` setzt das Stopp-Signal und
  schließt die Sockets, ohne sie vorher auszulesen.
- **Beleg P3:** Von 20 UDP-Datagrammen, die unmittelbar vor dem Schließen gesendet wurden, gingen **19 verloren**, also
  ohne Treffer im Nachher-Log.
- **TCP:** Für Verbindungen in der Warteschlange, die noch nicht angenommen wurden, gilt dasselbe (*plausibel*).
- **Folge:** Drückt der Operator in `host-lauf` zu früh Enter oder trifft ein später Abfluss das Ende, sieht der Host
  keinen Treffer.
- **Was bisher schützt:** Die Abdeckungsprüfung „Listener lief über den Prüfzeitraum“ vergleicht nur den
  **Schreibzeitpunkt** der VM-Belege mit dem Host-Fenster. Das sind zwei unsynchronisierte Uhren (siehe M6), und der
  Start der Proben ist nicht erfasst.
- **Empfehlung:**
  - Vor dem Schließen eine Wartezeit einhalten und alle Sockets nicht-blockierend leeren.
  - Das Ende der Nachher-Phase erst nach einem Abschluss-Signal der VM zulassen, mit Start- und Endzeit der Proben,
    zusätzlich eine Mindest-Nachlaufzeit.
  - Die Phase „nachher“ erst schließen, wenn danach eine Weile Ruhe herrscht.

**H2 – Die VM-Proben werten „Verbindung abgelehnt“ als „blockiert“.** *bestätigt*

- **Code:** `tests/test_s86_isolation_design.py`, `probe`: Jeder Rückgabecode ungleich 0 gilt als „blockiert“; der
  Selbsttest verlangt das sogar für einen geschlossenen Port. Das betrifft auch `curl` und `native`.
- **Beleg P1:** TCP gegen einen geschlossenen Loopback-Port ergibt „blockiert“.
- **Warum das ein Problem ist:** Eine Ablehnung (RST) heißt, dass das Paket den Host **erreicht hat** – der Pfad ist
  offen, nur der Port nicht. Eine ACL-Sperre verwirft dagegen und führt zu einem Timeout.
- **Folge:**
  - Ein toter oder falscher Listener-Port führt zu einem E0-PASS trotz offenem Pfad.
  - Zusammen mit H1 oder einem falsch konfigurierten Port beruht die Erkennung allein auf dem Host-Log.
- **Empfehlung:** Ergebnisse dreiwertig erfassen: `erreicht`, `abgelehnt` (Pfad offen → FAIL in Phase „nachher“) und
  `zeitueberschreitung`. Nur eine Zeitüberschreitung zählt als Sperre. Der Selbsttest ist entsprechend anzupassen.

### MITTEL

**M1 – Treffer außerhalb der Phasen „vorher“ und „nachher“ werden verworfen.** *bestätigt*

- **Code:** `CanaryListener.observation(phase)` mit `_gegenprobe_cross`.
- **Beleg P2:** Ein Treffer in der Phase „aktivierung“ ist in keinem Log enthalten. Dasselbe gilt für Treffer vor der
  ersten Phase.
- **Folge:** Ein Abfluss zwischen ACL-Aktivierung und `mark("nachher")` bleibt unbewertet.
- **Empfehlung:** Alle Treffer exportieren; jeder unerwartete Treffer außerhalb von „vorher“ ergibt FAIL.

**M2 – V9 prüft nur die Zahl der Adapter, nicht den Switch.** *bestätigt*

- **Code:** `isolation_evidence._v9_e1`.
- **Beleg P4:** Ein Adapter am **„Default Switch“** (NAT, Internet) ergibt `kein_zweiter_netzadapter = true`.
- **Widerspruch zu AUDIT_S93 §2.2:** Die dortige Aussage „prüft V9 über den Switch-Namen“ trifft nicht zu.
- **Was bisher schützt:** Die Gegenprobe würde in diesem Fall scheitern (BLOCKIERT); es gibt also kein falsches PASS
  über diesen Weg. Das Kriterium selbst ist aber zu schwach.
- **Empfehlung:** Switch-Name und Typ `Internal` mit der Konfiguration vergleichen und den Switch in die Konfiguration
  aufnehmen.

**M3 – V11 bindet die Rücksetzung nicht an den Datenträger der VM.** *bestätigt*

- **Code:** `isolation_host_control.image_zuruecksetzen`, `_v11_e1`.
- **Beleg P5:** PASS ist möglich, ohne dass geprüft wird, ob die VM überhaupt `kind_vhdx` mit der Eltern-VHDX
  `eltern_vhdx` nutzt (`Get-VMHardDiskDrive`, `Get-VHD ParentPath`).
- **Ebenfalls nicht geprüft:** ob die Eltern-VHDX schreibgeschützt ist, und ob der neue Differenzdatenträger auf die
  richtige Eltern-VHDX zeigt.
- **Empfehlung:** Datenträgerpfad, Elternkette und Schreibschutz lesend erfassen und als Kriterien aufnehmen.

**M4 – Der Fingerabdruck des Prüfwerkzeugs deckt den Treiber nicht ab; ein geänderter Arbeitsbaum fällt nicht auf.**
*bestätigt*

- **Code:** `isolation_gate.TOOL_FILES`, `answer_run._git_commit`.
- **Beleg P7:**
  - `answer_driver.py`, `net_fence.py`, `answer_exec.py` und `answer_run.py` fehlen im Fingerabdruck.
  - `_git_commit` liefert bei verändertem Arbeitsbaum unverändert `b7e2daa…`.
- **Folge:** In der VM kann ein veränderter Treiber, der das Gate nicht aufruft, mit gültiger Bestätigung laufen. Auch
  der gepinnte Wurzel-Fingerabdruck liegt im Code; wer das Repository beschreiben kann, kann ihn ersetzen. Schutz bieten
  nur ein schreibgeschütztes Golden Image und ein Konto ohne Schreibrechte – **beides prüft heute nichts**.
- **Empfehlung:**
  - Den Fingerabdruck auf alle ausführungsrelevanten Dateien ausdehnen (Treiber, Zaun, Ablage, Provider-Pfad).
  - Einen unsauberen Arbeitsbaum erkennen und als Verstoß werten.
  - Ein V2-Kriterium aufnehmen: Repository und `.venv` sind für das Benchmark-Konto nicht beschreibbar.

**M5 – Die Gast-Firewall wird ohne `-PolicyStore ActiveStore` gelesen.** *plausibel / nicht verifiziert*

- **Code:** `REAL_COMMANDS["vm.gast_firewall"]`, `isolation_gate.guest_firewall`.
- **Beleg P10:** Kein `PolicyStore` im Befehl.
- **Folge:** `Get-NetFirewallProfile` liest standardmäßig den lokalen persistenten Speicher. Eine per Gruppenrichtlinie
  gesetzte Firewall (geplant: lokale Richtlinie) erscheint dort nicht. Das Ergebnis kann falsch negativ oder falsch
  positiv sein.
- **Empfehlung:** `-PolicyStore ActiveStore` verwenden (wirksame Einstellung) und das real prüfen.

**M6 – Zeitvergleiche zwischen der Uhr der VM und der Uhr des Hosts.** *plausibel*

- **Code:** `_gegenprobe_cross` (Versuchszeiten der VM gegen das Host-Fenster, Nachher-Proben gegen den ACL-Export),
  der Prüfzeitraum in `evaluate` und das Belegalter im Gate (Uhr der VM).
- **Folge:** Eine isolierte VM ohne Zeitsynchronisation driftet.
  - Meist entsteht dadurch BLOCKIERT (sicher).
  - Eine Verschiebung kann aber auch Abdeckungs- und Reihenfolgeprüfungen fälschlich bestehen lassen (H1).
- **Empfehlung:** Den Uhrenversatz zu Beginn und am Ende messen und als E1-Beleg erfassen, Toleranz festlegen; alle
  Zeitprüfungen auf Host-Zeit umrechnen oder bei Versatz BLOCKIERT ergeben. Die Zeitsynchronisation des Hyper-V-
  Integrationsdienstes ist zu bewerten.

**M7 – Rückbau nach einem Abbruch ist nur teilweise geregelt.** *bestätigt*

- **Code:** `isolation_real.host_lauf` (kein `try/finally`), `isolation_host_control`.
- **Abbruch nach `acl-setzen`** (Ausnahme, Absturz, Neustart):
  - Die ACL bleibt gesetzt. Der nächste Lauf bricht sicher ab („ACL nicht leer“), dafür ist ein manuelles
    `acl-entfernen` nötig.
  - Falsche Ergebnisse entstehen dadurch nicht, aber es gibt keinen Hinweis und kein Wiederaufnahmeprotokoll.
- **Teilfehler beim Rückbau:** Er wird nur in der Fehlermeldung genannt („UNVOLLSTÄNDIG“); es entsteht kein Protokoll.
- **Host-Firewall-Regeln für die Kanarien:** Sie sind **nicht im Code**; Anlegen und Entfernen sind manuell (AUDIT_S93
  §3.4/C6). Nach einem Abbruch bleiben eingehende Freigaben auf `vEthernet` bestehen, und kein Lauf prüft beim Start auf
  verbliebene Regeln.
- **Golden Image:** Zwischen `Remove-Item` und `New-VHD` fehlt im Fehlerfall der Kinddatenträger. Die VM startet dann
  nicht (sicher), muss aber manuell neu aufgebaut werden.
- **Folge für den nächsten Lauf:**
  - ACL-Reste führen zu einem sicheren Abbruch.
  - Firewall-Reste werden nicht erkannt. Sie erhöhen die Angriffsfläche, verfälschen aber kein Ergebnis.
- **Empfehlung:**
  - Firewall-Regeln im Host-Steuerskript verwalten (Gruppe, Ablaufzeit, Export vorher und nachher).
  - Einen Startcheck einführen, der „keine Reste“ verlangt (ACL leer, Firewall-Gruppe leer, VM aus, Kinddatenträger
    vorhanden).
  - Ein Laufprotokoll mit Zustandsautomat führen und `host-lauf` mit `finally` versehen, das einen klaren Rückbauauftrag
    ausgibt.
  - Den Rückbau nie automatisch über die Grenzen hinaus ausführen.

**M8 – In der Phase „vorher“ erreicht die VM alle Host-Dienste auf der vEthernet-Schnittstelle.** *plausibel*

- **Beleg:** Host-Dienste auf allen Schnittstellen (§1).
- **Folge:** Ohne ACL hängt der Schutz von RPC, SMB und AMT/LMS allein von der Host-Firewall für das Profil des
  `vEthernet`-Adapters ab. Der Standard für eingehende Verbindungen lautet heute `NotConfigured`; ob dort Allow-Regeln
  greifen, ist **nicht verifiziert**.
- **Empfehlung:** Vor der Gegenprobe exportieren, welche eingehenden Regeln auf `vEthernet (lexono-abnahme)` wirken.
  Falls nötig, die eingehende Sperre für diese Schnittstelle als Voraussetzung aufnehmen; Vorher-Phase kurz halten.

### NIEDRIG

| # | Befund | Einstufung | Empfehlung |
|---|---|---|---|
| N1 | V13 ohne gepinnte Wurzel ergibt **FAIL (unklar)** statt BLOCKIERT (P8). Nie PASS, aber der Status ist fachlich falsch; ohne V13-Konfiguration ergibt sich BLOCKIERT | bestätigt | fehlende Vertrauenskette als BLOCKIERT ausweisen |
| N2 | V13-Kriterium sucht nur den Teilstring „abgelaufen“ und trifft damit auch „Schlüssel des Owners abgelaufen“ (P6) | bestätigt | auf die exakte Meldung „Bestätigung abgelaufen“ und eine sonst gültige Kette prüfen |
| N3 | UDP-Probe der VM meldet stets „blockiert“, weil der Listener nicht antwortet; der E0-Beleg ist für UDP ohne Aussage (P9: Host-Treffer 1, Probe „blockiert“). DNS über `getaddrinfo` ist ohne Resolver trivial | bestätigt | in AUDIT und Bewertung als „nur Host-Beleg“ kennzeichnen; ggf. Echo-Antwort des Listeners |
| N4 | Die native Probe ist nur für IPv4 implementiert (bekannt aus §94) | bestätigt | IPv6-Variante oder „nicht abgedeckt“ in der Bewertung |
| N5 | V10 `export_erzeuger` und V12 `auswertung_durch` sind Zirkelschlüsse; `kanarien_logs_sha256` wird nicht mit dem tatsächlichen Kanarienbeleg abgeglichen | bestätigt | Hash gegen den Kanarienbeleg im Bündel prüfen |
| N6 | Der V2-Änderungsversuch läuft vor den Netzwerkproben (Reihenfolge in `ANFORDERUNGEN`) und wird nicht zurückgenommen. Gelingt er, laufen die Proben mit geöffneter Gast-Firewall (V2 wird dann FAIL) | bestätigt | Versuch zuletzt ausführen; danach Zustand prüfen |
| N7 | `vm-teil` prüft nicht, ob es als festgelegtes Benchmark-Konto läuft (nur dessen Gruppen) | bestätigt | Kontoname in die Konfiguration aufnehmen und vergleichen |
| N8 | Bestätigungen sind innerhalb von 24 h für dieselbe `run_id` wiederverwendbar (Wiederaufnahme) | bestätigt (gewollt) | dokumentiert lassen bzw. an den Plan-Fingerabdruck binden |
| N9 | Geklonte VMs teilen sich die MachineGuid (Golden Image) | plausibel | nur eine VM je Image; vTPM-/VM-ID zusätzlich binden |
| N10 | `_writable_by_this_process` prüft nur die Datei, nicht Ordner oder Rechte. Die eigentliche Sicherheit liegt in der Signatur und im Pin im Code (siehe M4) | bestätigt | mit M4 lösen |

### NICHT VERIFIZIERT (reales Verhalten)

- **Hyper-V Extended ACL auf Windows 11:**
  - ob `Outbound` Verkehr **aus** der VM meint;
  - Gewichtung, Parameter von `Remove-VMNetworkAdapterExtendedAcl` (Weight und Richtung), Stateful;
  - Wirkung auf IPv6 Neighbor Discovery und DHCP.

  Eine falsche Richtung würde die Gegenprobe aufdecken (Treffer nachher) – sofern H1 und H2 behoben sind.
- **Echte PowerShell-Ausgaben:** Struktur von `ConvertTo-Json` bei verschachtelten Listen (`vm_netzadapter`) und das
  Format der Enum-Umwandlung. Unbekannte Formen führen zu Fehlern, nicht zu PASS.
- **Rechte:** erhöhte Sitzung bzw. „Hyper-V-Administratoren“ für alle Host-Lesebefehle und -Änderungen. Die Werkzeuge
  fordern keine Rechte an; fehlende Rechte führen zu Fehlern, also nicht zu PASS.

## 3 Bestätigte Schutzeigenschaften

Lokal getestet, durch die Testsuite belegt:
- **Manipulation:** Manipulierte Belege oder Manifeste, nachgezogene Manifest-Hashes und von Hand gesetzte Bewertungen
  bzw. `"status": "PASS"` werden abgelehnt bzw. ignoriert (§90, §91, §92).
- **Trockenlauf:** Trockenlauf-Belege sind produktiv nie verwertbar. Gemischte Bündel ergeben BLOCKIERT, gemischte
  Teile lehnt `assemble` ab (§94).
- **Testbestätigungen:** Test-Schlüssellisten und test-markierte Bestätigungen lehnt das Produktiv-Gate ab; ohne
  gepinnte Wurzel ist das Gate geschlossen. Simulierte Systemantworten können V13 nicht auf PASS bringen (P8, §94).
- **Schlüssel:** Widerruf, Rotation, Mindestversion, Ablauf und die Bindung an Schlüsselliste, Prüfwerkzeug, Bündel und
  Lauf funktionieren. Unerwartete Typen führen zu „Gate geschlossen“.
- **Gegenprobe:** je Kanal getrennt; Reihenfolge Vorher → ACL → Nachher; Widersprüche ergeben BLOCKIERT (§92).
  **Ausnahmen:** H1, H2 und M1.
- **Port-ACL-Werkzeug:**
  - ändert nur mit Umgebungsfreigabe plus `--ausfuehren`;
  - setzt nur bei leerer Ist-ACL, liest nach und baut bei Abweichung zurück;
  - entfernt nur eigene Einträge;
  - lässt nur geprüfte Zeichen in Argumente; Enum-Zahlen werden abgelehnt.

  Alles nur **simuliert** geprüft.
- **Treiber:** stoppt vor jedem Provider-Aufruf ohne gültiges Gate (§87–§94).

## 4 Empfohlene Reihenfolge vor F1 und dem Echtbetrieb

Umsetzung nur nach deiner Freigabe:
1. **Vor jeder realen Abnahme beheben:** H1 (Listener leeren, Abschluss-Signal, Nachlauf), H2 (dreiwertige
   Probenergebnisse), M1 (alle Phasen exportieren).
2. **M4:** Fingerabdruck erweitern, unsauberen Arbeitsbaum erkennen, Schreibschutz-Kriterium. Das sollte **vor F1**
   geschehen, weil F1 genau diesen Code festschreibt.
3. **M2, M3, M5, M6:** Kriterien für Switch, VM-Datenträger, wirksamen Firewall-Speicher und Uhrenversatz.
4. **M7, M8:** Firewall-Regeln ins Steuerskript übernehmen; Startcheck „keine Reste“; Laufprotokoll mit Rückbauauftrag.
5. **N1–N7:** nach Bedarf.

F1 (Commit) ist auch vorher möglich, um den Stand festzuhalten. Für die **reale Abnahme** gilt die Kette aber erst nach
Punkt 1 als belastbar.

## 5 Ergebnisübersicht

| Punkt | Ergebnis |
|---|---|
| Lokale Tests §86–§94 (simuliert bzw. synthetisch) | **PASS** (257 bestanden, 7 übersprungen) |
| Reproduzierbare Belege P1–P10 | **durchgeführt**; bestätigen H1, H2, M1, M2, M3, M4, N1, N2, N3 |
| Gate und Vertrauenskette (Manipulation, Replay, Rollen, Mischung, Alter, Widerruf, Trockenlauf) | **PASS** bis auf M4 und N8 |
| Host-/VM-Isolationslogik | **FAIL** – H1, H2, M1 können ein falsches PASS ermöglichen |
| Fehlerfälle und Rückbau | **FAIL** – M7: Firewall-Rückbau manuell und ungeprüft; ACL-Reste werden sicher erkannt |
| V11 und V13 | V11 **FAIL** (M3); V13 nie PASS ohne Pin (**PASS** hinsichtlich der Umgehung), Status-Semantik falsch (N1) |
| PowerShell und Rechte | **NICHT DURCHGEFÜHRT** (real); lokal: Validierung und Normalisierung **PASS**, M5 offen |
| Reale Abnahme, Isolation | **NICHT DURCHGEFÜHRT** |
| Sicherheitsfreigabe | **keine** |
| C-14 | **GESPERRT** |
