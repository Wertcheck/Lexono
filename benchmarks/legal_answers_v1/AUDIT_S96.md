# §96 Behebung der Sicherheitsbefunde aus §95

Stand 20.10.2026. Branch `feature/feedback-diagnose`, HEAD `b7e2daa` (geprüft; §90–§96 **uncommittet**); `main` = `5735e5b`.

**Grenzen dieser Arbeit:**
- Nur lokale Code-, Test- und Dokumentationsänderungen.
- Keine Änderung an Windows, Hyper-V, Firewall, ACL, Netzwerk oder Konten; keine VM.
- Keine externen Verbindungen, Provider-Aufrufe oder Schlüssel.
- Die Produktivdatenbank wurde nicht geöffnet.
- Fremde Dateien, AUDIT_S88–S95 und `PRIVACY_REVIEW_ARCHITECTURE.md` blieben unverändert.
- Alle Ergebnisse sind **lokal bzw. simuliert**; es gibt **keine reale Isolation und keine Sicherheitsabnahme**.
  V1–V13 bleiben real NICHT DURCHGEFÜHRT, C-14 bleibt GESPERRT.

## 1 Geänderte Dateien

| Datei | Art |
|---|---|
| `scripts/legal_bench/isolation_evidence.py` | geändert: Kriterien mit Soll-Parametern, Phasen-Tokens, Listener-Abschluss, Treffer außerhalb der Phasen, dreiwertige Proben, V9/V11/V12/V13-Ketten |
| `scripts/legal_bench/isolation_acceptance.py` | geändert: Listener (Ruhezeit, Leeren, Echo, alle Phasen), `probe_once`, `calibration_probe` mit Tokens, neue lesende Befehle, Parameter in `EvidencePart`, Trockenlauf mit Tokens |
| `scripts/legal_bench/isolation_real.py` | neu geschrieben: Konfiguration mit Switch, Gateways und Benchmark-Konto; Tokens; Rücknahme des V2-Versuchs; Arbeitsbaum- und Schreibtest; Switch, vEthernet-Schutz, Datenträger; Statusdatei bei Abbruch |
| `scripts/legal_bench/isolation_host_control.py` | geändert: eigene Firewall-Regeln (`fw-plan/-setzen/-pruefen/-entfernen`), `startcheck` |
| `scripts/legal_bench/isolation_gate.py` | geändert: Fingerabdruck über alle ausführungsrelevanten Dateien, ActiveStore, exakte Ablaufmeldung |
| `scripts/legal_bench/owner_sign.py` | geändert: Signatur nur für den Inhalt des Commits (`committed_stand`) |
| `tests/test_s86_isolation_design.py` | geändert: Proben dreiwertig plus Fehler, native IPv6-Probe, Zeitlimit 5 s |
| `tests/isolation_test_support.py`, `tests/test_s90…`, `test_s91…`, `test_s92…`, `test_s94…` | an die strengere Semantik angepasst |
| `tests/test_s96_review_fixes.py` | neu, 71 Tests |

## 2 Befunde aus §95: Behebung, Test, Restrisiko

| Befund | Behebung | Test(s) | Ergebnis (lokal) | verbleibendes Risiko |
|---|---|---|---|---|
| **H1** verlorene Treffer am Phasenende | Listener-Threads lesen bis zur Leere und enden erst nach dem Stopp **und** einem leeren Abfragezyklus. Fehler beenden einen Thread nie still. Eine Phase endet erst nach einer Ruhezeit ohne Treffer (monoton, ≥ 2 s). Das Kanarienlog enthält `abschluss` (geleert, Threads beendet, Nachlauf); ohne vollständigen Abschluss ergibt sich FAIL. Das Phasenende auf dem Host erfolgt erst, nachdem der Abschluss-Token der VM eingegeben wurde | `test_udp_datagrams_just_before_exit_are_not_lost` (Regression P3: früher 19 von 20 verloren, jetzt 0 von 10), `…tcp…`, `test_phase_end_waits_for_quiet…`, `test_incomplete_listener_completion…`, `test_vorher_phase_without_quiet…` | **PASS** | Pakete nach dem Schließen des Listeners können naturgemäß nicht beobachtet werden. Der Token belegt, dass die VM fertig war; eine VM, die danach noch sendet, bleibt unentdeckt. Die Ruhezeit ist eine Heuristik |
| **H2** Ablehnung als Sperre gewertet | §86-Proben und `probe_once` liefern `erreicht`, `abgelehnt`, `zeitueberschreitung` oder `fehler`. Abnahmetests verlangen **nur** `zeitueberschreitung`; die Gegenprobe verlangt `erreicht`. UDP/DNS-Kanarien senden ein Echo, damit „zugestellt“ messbar wird. Zeitlimit 5 s, weil Windows nach einem RST etwa 2 s wiederholt (gemessen) | `test_probe_results_are_unambiguous`, `test_short_timeout_would_mistake…`, `test_calibration_without_reach_is_blocked[abgelehnt/zeitueberschreitung/fehler]`, Selbsttest in `test_s86` | **PASS** | Ob ICMP-Antworten und RST in Hyper-V wie lokal ankommen, ist **NICHT VERIFIZIERT**. Ein Netzwerkgerät, das RST fälscht, würde als `abgelehnt` (FAIL) erscheinen, nicht als PASS – also fail-closed |
| **M1** unbeobachtete Zeitfenster | Jeder Treffer wird mit seiner Phase gespeichert. Das Nachher-Log führt `ausserhalb_der_phasen` (vor der ersten Phase, während der Aktivierung); jeder solche Treffer ergibt FAIL | `test_hits_outside_phases_are_reported_and_fail` | **PASS** | Treffer während der Aktivierung können auch harmlose Nachzügler der Gegenprobe sein; das ist bewusst konservativ (FAIL, nie PASS) |
| **M2** V9 nur Adapterzahl | Neue Kriterien: `adapter_am_abnahme_switch` (Name **und** Typ `Internal`, aus `Get-VMSwitch`), `standardgateway_wie_soll`, `kein_dns_server_in_der_vm` (aus `Get-NetIPConfiguration`); Soll aus der Konfiguration | `test_v9_checks_the_actual_switch[3]`, `test_v9_checks_vm_ip_configuration[3]`, `test_wrong_switch_in_the_simulated_pipeline_fails_v9` | **PASS** | Echte Ausgabeformate **NICHT VERIFIZIERT**. Die Ausnahme der Windows-Platzhalter-DNS `fec0:0:0:ffff::1–3` ist eine Annahme. Stufe B braucht erlaubte Gateways |
| **M3** V11 ohne VM-Datenträger | Neue Kriterien: `vm_nutzt_kinddatentraeger` (genau ein Laufwerk gleich `kind_vhdx`), `kind_haengt_am_golden_image` (`Get-VHD`: ParentPath gleich Golden Image, Typ `Differencing`), `golden_image_schreibgeschuetzt` (Dateiattribut). Die Rücksetzung muss vor Beginn der Gegenprobe liegen (Host-Uhr) | `test_v11_is_bound_to_the_vm_disk[5]`, `test_other_vm_disk…`, `test_writable_golden_image_fails_v11`, `test_reset_after_counter_probe_start_blocks_v11` | **PASS** | Das Readonly-Attribut ist kein ACL-Schutz gegen Administratoren. Ob die VM vom Golden Image **gebootet** hat, wird nur über die Konfiguration belegt |
| **M4** Fingerabdruck, Arbeitsbaum, Schreibrechte | Fingerabdruck über `scripts/legal_bench/*.py`, `app/ai_providers/*.py`, `app/privacy/*.py`, Isolationstests und Politik/Matrix (60 Dateien). `owner_sign` signiert nur, wenn der Arbeitsbaum des Hosts dem Inhalt des Commits entspricht (`git show`). Das Gate in der VM vergleicht den Inhalt des Arbeitsbaums mit diesem Stand. V2 E0: `arbeitsbaum_sauber` (`git status --porcelain`), `repository_schreibgeschuetzt` (Anhänge-Test an allen Werkzeugdateien, Anlage-Test in Repository, `scripts/legal_bench` und `.venv`) | `test_tool_fingerprint_covers…`, `test_committed_stand_detects_uncommitted_files` (echtes lokales git), `test_committed_stand_matches…`, `test_signing_refuses_a_worktree…`, `test_v2_worktree_write_access_and_account[5]`, `test_write_access_probe…`, `test_evidence_folders_inside_the_repository_are_refused` | **PASS** | Python, `.venv`-Pakete und Betriebssystem liegen außerhalb des Fingerabdrucks. Ein Administrator in der VM bleibt außerhalb des Modells. **Bis F1 ist keine reale Signatur möglich** – die neuen Dateien sind nicht committet (gewollt) |
| **M5** Policy Store | Gast-Firewall in V2 und im Gate aus `-PolicyStore ActiveStore` | `test_guest_firewall_is_read_from_the_active_store` | **PASS** (nur statisch) | ActiveStore-Semantik real **NICHT VERIFIZIERT** |
| **M6** Uhren VM gegen Host | Alle Vergleiche zwischen VM- und Host-Uhr sind entfernt. Reihenfolgen gelten über Phasen-Tokens: Der Host erzeugt den Start-Token, die VM ihren Abschluss-Token nach den Proben, der Host schließt danach. Host-interne Reihenfolgen nutzen nur die Host-Uhr. Dauern werden monoton gemessen | `test_vm_clock_skew_does_not_change_the_verdict`, `test_listener_measures_durations_monotonically`, Token-Fälle in `test_s92` | **PASS** | Das Belegalter (24 h) vergleicht weiterhin VM-Belegzeit mit der Signaturzeit des Hosts – grob, mit 5 min Toleranz. Tokens werden vom Operator übertragen (Tippfehler ergeben einen Abbruch, kein PASS) |
| **M7** Rückbau, Reste | `isolation_host_control`: eigene Regelgruppe `Lexono-Abnahme`. Je Kanarienziel eine eingehende Regel (Schnittstelle vEthernet, Host-Adresse, Port, nur VM-Adressen), Beschreibung mit `run_id` und Ablauf (≤ 12 h). Setzen nur bei leerer Gruppe; Nachlesen; bei Abweichung Rückbau. Entfernen nur eigener Regeln; fremde Regeln führen zum Abbruch. `startcheck` findet ACL-, Firewall-, VM- und Protokollreste. `host-lauf` verlangt gültige eigene Regeln und schreibt bei **jedem** Ende `lauf_status.json` mit dem Rückbauauftrag | `test_firewall_rules_are_narrow…`, `test_firewall_leftovers…`, `test_partial_firewall_failure_is_rolled_back`, `test_expired_or_foreign_run_rules…`, `test_startcheck_finds_leftovers`, `test_host_run_requires_own_firewall_rules`, `test_aborted_host_run_writes_a_rollback_order`, `test_successful_run_still_requires_rollback` | **PASS** (simuliert) | Der Rückbau ist bewusst **nicht automatisch**: ein manueller Schritt, aber angezeigt und protokolliert. Nach einem Prozessabsturz ohne `finally` (Stromausfall) fehlt die Statusdatei; `startcheck` findet die Reste beim nächsten Lauf. Parameter von `New-NetFirewallRule` real **NICHT VERIFIZIERT** |
| **M8** Host-Dienste in der Vorher-Phase | V9 E1: `host_dienste_in_vorher_phase_geschuetzt`. Lesend werden Netzwerkkategorie, wirksamer Eingangsstandard und eingehende Allow-Regeln (ActiveStore) erfasst. FAIL, wenn eine Regel für Profil und Schnittstelle RPC, SMB, AMT/LMS, WinRM, RDP, 5040, 7680, Bereiche oder „Any“ öffnet. `NotConfigured` gilt als unklar | `test_v9_host_services_during_counter_probe[7]` | **PASS** (lokal) | Die Regelbewertung ist vereinfacht (Programm-, Adress- und Dienstfilter werden nicht ausgewertet). Wahrscheinlich FAIL bei Standard-Windows-Regeln – dann ist eine bewusste Entscheidung über eingehende Sperren auf vEthernet nötig (Systemänderung, Freigabe). AMT im Firmware-Bereich ist über einen internen Switch vermutlich nicht erreichbar (**plausibel, nicht verifiziert**) |
| **N1** V13 FAIL statt BLOCKIERT | Ohne gültige Schlüsselliste oder aktiven Schlüssel ergibt sich das Kriterium `blockiert`, also Status BLOCKIERT | `test_v13_without_trust_chain_is_blocked_not_failed`, `test_v13_with_unpinned_root…` (§94) | **PASS** | – |
| **N2** Teilstring „abgelaufen“ | Exakte Gate-Meldung (`MSG_ATTESTATION_EXPIRED`) und Schlüsselstatus aktiv/widerrufen/abgelaufen/unbekannt aus der Schlüsselliste | `test_v13_is_semantic[5]`, `test_v13_gate_probe_with_a_pinned_test_root` | **PASS** | Der V13-Beleg ist ohne D4 nicht erzeugbar → V13 bleibt BLOCKIERT |
| **N3** UDP-Probe ohne Aussage | Echo der UDP/DNS-Kanarien | `test_s86_udp_probe_is_meaningful_against_the_canary` (Regression P9) | **PASS** | Das Echo ist ein Antwortpaket an die VM (nur Absender, interner Switch) |
| **N4** native Probe nur IPv4 | `sockaddr_in6` und WSA-Fehlercodes 10061/10060 | Selbsttest `test_s86` (IPv4); IPv6 nativ nur in der Zielumgebung | IPv4 **PASS**, IPv6 **NICHT DURCHGEFÜHRT** | – |
| **N5** V12 tautologisch | Der Hash des Kanarienlogs muss dem Kanarienbeleg dieses Bündels entsprechen; sonst BLOCKIERT | `test_v12_report_must_match_the_canary_log` | **PASS** | „auswertung_durch“ bleibt eine Prozessaussage |
| **N6** V2-Änderungsversuch | Läuft nach allen Proben. Zustand vorher/nachher (PersistentStore) wird erfasst; ein gelungener Versuch wird zurückgenommen; ein Rücknahmefehler bzw. unbekannter Ausgangswert verhindert PASS (`zustand_nach_aenderungsversuch_unveraendert`) | `test_successful_change_attempt_is_rolled_back_and_fails`, `test_failed_rollback_prevents_pass`, `test_change_attempt_runs_after_all_probes` | **PASS** (simuliert) | Die Rücknahme setzt den lokalen Speicher, nicht eine GPO |
| **N7** Benchmark-Konto | `laeuft_als_benchmark_konto` gegen `benchmark_konto` der Konfiguration | `test_v2_…[falsches_konto]` | **PASS** | – |
| N8–N10 | unverändert dokumentiert (Wiederverwendung innerhalb 24 h, geklonte MachineGuid, Anker-Datei) | – | **NICHT DURCHGEFÜHRT** (bewusst) | wie AUDIT_S95 |

## 3 Testbefunde

| Prüfung | Ergebnis |
|---|---|
| `tests/test_s96_review_fixes.py` (71) | **PASS** |
| Isolationstests §86–§96 zusammen | **PASS** (331 bestanden, 7 übersprungen = Zielumgebung) |
| Gesamtablauf mit **simuliertem** System (§94/§96) | V1–V12 PASS, V13 BLOCKIERT – **Simulation, keine Isolation** |
| Trockenlauf CLI | 13× PASS (synthetisch, Loopback) |
| Benchmark-Tests §77–§85 | **PASS** (162 + 153) |
| volle Suite in 6 Teilen | 5626 bestanden, **4 FAIL** – die bekannten fremden Logo-Tests, nicht behoben |

**Neuer lokaler Befund (gemessen):** Windows wiederholt einen TCP-Verbindungsaufbau nach einem RST etwa 2 s lang.
Proben mit kürzerem Zeitlimit melden dann „zeitueberschreitung“ statt „abgelehnt“. Das Zeitlimit ist deshalb 5 s, und ein
Test hält dieses Verhalten fest.

## 4 Voraussetzungen für den Echtbetrieb

| Voraussetzung | Status |
|---|---|
| F1: §90–§96 committen. Erst dann kann `owner_sign` überhaupt signieren (Commit-Bindung, M4) | NICHT DURCHGEFÜHRT |
| D1-Schritte aus AUDIT_S93 einschließlich `startcheck`, `fw-setzen` und einer Entscheidung zu eingehenden Regeln auf vEthernet (M8) | BLOCKIERT (deine Freigaben) |
| Echte Formate von `Get-VMSwitch`, `Get-VMHardDiskDrive`, `Get-VHD`, `Get-NetIPConfiguration`, `Get-NetFirewallRule`/Filter, `Get-NetConnectionProfile` | NICHT VERIFIZIERT |
| D3 (Relay), D4 (Schlüssel) | BLOCKIERT |

## 5 Offene Risiken (Auswahl)

1. **Simulation:** Alle positiven Gesamtergebnisse stammen aus simulierten Systemantworten bzw. Loopback.
2. **Hyper-V Extended ACL**, Wirkung von RST/ICMP sowie ActiveStore-, Firewall- und Profilsemantik auf Windows 11:
   **NICHT VERIFIZIERT**.
3. **Herkunft der E1-Belege, Absenderfälschung, Admin in der VM:** unverändert (AUDIT_S95 §2).
4. **M8:** Die Regelbewertung ist vereinfacht und auf FAIL statt PASS ausgelegt. Eine echte Absicherung von vEthernet
   ist eine Systemänderung und muss entschieden werden.
5. **Längere Testlaufzeit** durch Ruhezeiten (≥ 2 s je Phasenende) und das Zeitlimit von 5 s.

## 6 Ergebnisübersicht

| Punkt | Ergebnis |
|---|---|
| H1, H2, M1 | **PASS** (lokal) |
| M2, M3, M4, M6, M7 | **PASS** (lokal bzw. simuliert) |
| M5, M8 | **PASS** lokal; reale Semantik **NICHT VERIFIZIERT** |
| N1–N3, N5–N7 | **PASS** (lokal) |
| N4 IPv6 nativ | **NICHT DURCHGEFÜHRT** (nur Zielumgebung) |
| N8–N10 | **NICHT DURCHGEFÜHRT** (dokumentiert) |
| Volle Suite | **PASS** bis auf 4 FAIL (bekannte fremde Logo-Tests) |
| Reale Abnahme, Isolation, Sicherheitsfreigabe | **NICHT DURCHGEFÜHRT** – keine Freigabe |
| C-14 | **GESPERRT** |
