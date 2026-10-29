# §98 Sicherheitsbefunde aus §97 beheben

Stand 20.10.2026. Branch `feature/feedback-diagnose`, HEAD `b7e2daa`. §90–§98 sind **uncommittet**.

**Grenzen dieser Arbeit:**
- Nur lokale Änderungen an Code, Tests und Dokumentation.
- Keine Änderungen an Windows, Hyper-V, Firewall, ACL, Netzwerk oder Konten.
- Keine VM, keine Schlüssel, keine externen Verbindungen. Die Produktivdatenbank wurde nicht geöffnet.
- Alle Tests laufen gegen das **simulierte** Hyper-V/PowerShell-System aus §94, gegen Loopback und gegen synthetische
  Belege. Sie belegen nur die Werkzeugkette, **keine reale Isolation**.
- **Keine Sicherheitsfreigabe.** F1, D1 und C-14 bleiben gesperrt.

## 1 Übersicht

| # | Befund (§97) | Ergebnis §98 |
|---|---|---|
| H1 | Firewall und Listener nach der Nachher-Phase nicht geprüft | behoben (simuliert getestet) |
| H2 | Fingerabdruck unvollständig | behoben, Restrisiko: Prüfung des Inhalts von `site-packages` ist eine Liste, keine Wheel-Hash-Prüfung |
| M1 | Schreibtest zu schmal | behoben (Probe in allen Codeordnern); Restrisiko: probiert statt ACL auszulesen |
| M2 | Prozess-Timeout zählt als Sperre | behoben |
| M3 | DNS-Probe ohne Aussagekraft | behoben |
| M4 | Abbruch während `acl-setzen` ohne Rückbau | behoben |
| M5 | Reste alter Läufe nicht entfernbar | behoben (`--lauf`) |
| M6 | Status wird überschrieben, Rückbauauftrag ungenau | behoben |
| M7 | Leere Portlisten gelten als ungefährlich | behoben |
| M8 | Hyper-V-Kanäle neben dem Netz | teilweise: Integrationsdienste geprüft; hvsocket als **nicht abgedeckt** ausgewiesen |
| M9 | Nicht-IP, Broadcast, Adapterschutz | teilweise: MAC-Spoofing/DHCP-/Router-Guard geprüft; Rest als **nicht abgedeckt** ausgewiesen |
| N1 | Startcheck ohne Kinddatenträger | behoben |
| N2 | Ablaufzeit der Firewall-Regeln nur Text | Restrisiko (begründet, §3) |
| N3 | nslookup-Teilstring „Zeit“ | behoben |
| N4 | Fremder Treffer während der Kontrolle zählt als Kontrolle | behoben |
| N5 | VM-Uhr | behoben (Plausibilität gegen Host-Phasen, sonst BLOCKIERT); Restrisiko: Administrator in der VM |
| N6 | V9 nur Standard-Gateways | behoben (`Get-NetRoute`) |

## 2 Befund, Fix und Test

Tests in `tests/test_s98_review_fixes.py`, wenn nicht anders angegeben.

### H2 und M1 – Fingerabdruck und Schreibrechte

- **Fix Code:** `isolation_gate.TOOL_GLOBS` erfasst jetzt:
  - alle `*.py` der Wurzel (`run.py` usw.),
  - `app/**/*.py` und die Konfigurationen in `app/` (`json`, `yaml`, `yml`, `toml`),
  - `scripts/**/*.py` (mit `scripts/__init__.py`),
  - `tests/conftest.py`, `pyproject.toml`, `setup.cfg`, `requirements*.txt`,
  - die Abnahmetests und die Politikdateien (`TOOL_FILES_EXTRA`).

  `__pycache__` ist ausgenommen. Eine Datei, die ein Symlink ist, fehlt oder nicht lesbar ist, ergibt `sha256 = None`;
  das Gate schließt dann.
- **Fix Laufzeit:** Neu ist `laufzeit_stand()`. Der Wert `laufzeit_sha256` ist im Pflichtfeld `pruefwerkzeug` der
  Bestätigung. Erfasst werden:
  - Python-Version und Interpreter,
  - alle installierten Distributionen mit Version,
  - Inhalt jeder `.pth`-Datei,
  - Editable-Finder, `sitecustomize` und `usercustomize`,
  - jeder Eintrag in `site-packages`, also auch ein neues, unbekanntes Modul,
  - die Import-Umgebungsvariablen (`PYTHONPATH`, `PYTHONSTARTUP`, `PYTHONHOME` u. a.).

  Absolute Installationspfade werden normalisiert. Fehlt ein Wert oder ist er nicht lesbar, ist `laufzeit_sha256 = None`
  und das Gate meldet BLOCKIERT. Ein abweichender Wert macht die Bestätigung ungültig.
- **Fix Commit-Grenze:** `owner_sign.committed_stand` liest alle Fingerabdruck-Dateien aus dem Commit
  (`git cat-file --batch`). Ist eine Datei nicht im Commit (untracked) oder lässt sich die Ausgabe nicht lesen, gilt
  `sha256 = None` mit Angabe der fehlenden Datei. Das Signieren wird dann verweigert.
- **Fix M1:** `observe_write_access` probiert jetzt:
  - jeden Ordner, der eine Fingerabdruck-Datei enthält (auch `app/…`),
  - jede Fingerabdruck-Datei,
  - jeden `site-packages`-Ordner und jede `.pth`-Datei.

  Probedateien werden entfernt.
- **Tests:**
  - `test_fingerprint_covers_all_executable_code` (jede `app`-Datei, auch künftige),
  - `test_runtime_fingerprint_detects_pth_modules_and_import_env` (`.pth` geändert, neues Modul, `PYTHONPATH`),
  - `test_runtime_fingerprint_normalizes_install_paths`,
  - `test_unreadable_runtime_closes_the_gate`,
  - `test_changed_runtime_invalidates_attestation`,
  - `test_symlinked_tool_file_is_not_accepted` (übersprungen: ohne Entwicklermodus keine Symlinks anlegbar),
  - `test_commit_boundary_detects_changed_untracked_and_unknown_files`,
  - `test_real_git_reports_uncommitted_tool_files` (echtes Git, nur lesend: der heutige Stand ist nicht signierbar),
  - `test_write_test_covers_app_dirs_site_packages_and_pth`.
- **Restrisiko:**
  - Der Fingerabdruck von `site-packages` besteht aus Namen, Versionen und dem `.pth`-Inhalt. Er hasht **nicht** jede
    installierte Datei. Wer eine Datei innerhalb eines installierten Pakets ändert, ohne die Version zu ändern, fällt
    nicht auf. Gegenmaßnahme: der Schreibschutz für `site-packages` (M1, Kriterium V2). Eine Wheel-Hash-Liste kann
    später folgen.
  - Code, der außerhalb von Python geladen wird (DLLs, Treiber), ist nicht erfasst.
  - Der Schreibtest probiert und liest keine ACLs aus (`Get-Acl`). Ein Konto mit verweigertem Schreiben, aber dem Recht
    „Berechtigungen ändern“, wird nicht erkannt.

### H1 und M7 – Firewall und Listener während der Phasen

- **Fix:**
  - `host_lauf` prüft die eigenen Kanarien-Firewall-Regeln (`fw_pruefen`, Ist gleich Soll mit sha256) an diesen
    Zeitpunkten:
    - `vorher_ende`, `nachher_start`, `nachher_ende`,
    - während „nachher“ periodisch alle 15 s (`PeriodicCheck`, Zeitpunkt `nachher_waehrend`).
  - Jede Prüfung wird Teil des E1-Kanarienbelegs (`firewall_kontrollen`).
  - Der Listener führt an `vorher_ende` und `nachher_ende` eine Positivkontrolle durch (`kontrollen`).
  - Der Listener muss sauber abschließen (`abschluss`: Drain ohne Fehler).
- **Bewertung** (`_kontroll_problem`, `_gegenprobe_cross`) – BLOCKIERT, nie PASS, wenn:
  - eine Kontrolle fehlt,
  - eine Prüfung „ungleich“ oder unlesbar ist,
  - sich der Firewall-sha256 innerhalb der Phase oder zwischen Vorher und Nachher ändert,
  - der Listener nicht antwortet oder nicht sauber abschließt.

  Das gilt für alle Kanarientests (V4–V8, V12).
- **Phasenübergänge:** Die Phasen sind Tokens. Jeder Übergang setzt eine Kontrolle voraus. Treffer außerhalb der Phasen
  ergeben BLOCKIERT (schon in §96).
- **M7:** Eine leere oder unlesbare Port- bzw. Schnittstellenliste ist `None` (unklar) und wird als FAIL gewertet.
- **Tests:**
  - `test_firewall_removed_during_after_phase_blocks_canary_tests` (Regression zu P4),
  - `test_firewall_changed_during_after_phase_blocks`,
  - `test_missing_or_bad_controls_block` (6 Fälle),
  - `test_firewall_changed_between_before_and_after_blocks`,
  - `test_periodic_check_records_transient_failures`,
  - `test_empty_or_unreadable_port_lists_are_not_safe`,
  - §91: `test_listener_positive_control_is_recorded_and_required`.
- **Restrisiko:**
  - Zwischen zwei periodischen Prüfungen (15 s) kann eine Regel kurz entfernt und wiederhergestellt werden. Der
    sha256-Vergleich erkennt nur ein Ergebnis, das am Prüfzeitpunkt sichtbar ist.
  - Ein Angreifer mit Administratorrechten am Host kann alles fälschen (Grenze des Modells, §97).

### M2, M3 und N3 – Proben und DNS

- **Fix:** Die Probe trennt jetzt diese Ergebnisse:
  - `zeitueberschreitung` – Netzwerk-Timeout im Kindprozess. **Nur dieses Ergebnis zählt als Sperre.**
  - `abgelehnt`,
  - `prozess_timeout` – der Kindprozess hängt,
  - `prozessfehler` – Absturz oder unbekannter Exit-Code,
  - `keine_aufloesung` – `getaddrinfo` ohne Resolver,
  - `fehler`.
- **V7:**
  - Gestützt auf eine echte DNS-Abfrage (`dnsquery`, UDP/53-Paket an den Host-Kanarienresolver) und `nslookup`.
  - Die Systemauflösung dient nur der Plausibilität.
  - Der Abnahmetest verlangt einen konfigurierten Resolver.
- **nslookup:** Die Auswertung prüft exakte Meldungen (Deutsch/Englisch) und die Antwortanalyse. Nur Kopfzeilen oder
  unbekannter Text ergeben `fehler`; ein „No response from server“ ergibt `abgelehnt`.
- **Tests:**
  - `test_hanging_probe_process_is_not_a_block`,
  - `test_dns_query_probe_is_meaningful`,
  - `test_nslookup_output_is_interpreted_strictly` (6 Fälle),
  - `test_v7_acceptance_requires_a_resolver_and_a_real_query`,
  - §86: Probe- und Selbsttests.
- **Restrisiko:**
  - Die deutschen Meldungen von nslookup sind nur für die getesteten Wortlaute abgedeckt. Andere Sprachpakete ergeben
    `fehler`, also BLOCKIERT (fail-closed).
  - **Reales Windows-Verhalten ist nicht verifiziert.**

### M4, M5 und M6 – Absturz und Rückbau

- **Fix Journal:**
  - Jede Host-Änderung wird **vor** und **nach** ihrer Ausführung in `aenderungsjournal.jsonl` protokolliert (fsync).
  - Zustände: `geplant` → `begonnen` → `erfolgreich` / `fehlgeschlagen` / `abgebrochen` → `abgeschlossen` /
    `zurueckgebaut` / `unvollstaendig`.
  - Bei `BaseException` (auch `KeyboardInterrupt`) baut `acl_setzen`/`fw_setzen` zurück. Der Endzustand wird aus dem
    nachgelesenen Ist-Zustand bestimmt.
  - `journal_offen` meldet jeden Vorgang ohne Endzustand. `startcheck` verweigert den Start, solange ein Vorgang offen ist.
- **Fix alte Läufe:**
  - Befehle: `acl-entfernen --lauf <run_id>` und `fw-entfernen --lauf <run_id>`.
  - Entfernt werden **nur** Einträge, die das Journal diesem Lauf als erfolgreich gesetzt zuordnet und die im Ist-Zustand
    exakt so vorhanden sind.
  - Regeln mit gleichem Namensschema, die nicht im Journal stehen, bleiben unberührt und werden gemeldet.
  - Ohne Journaleintrag wird verweigert.
- **Fix Status:**
  - Je Lauf eine eigene Datei `lauf_status_<run_id>_<zeit>.json`, exklusiv angelegt, nie überschrieben.
  - Der Rückbauauftrag wird aus dem tatsächlichen Ist-Zustand abgeleitet (`_rueckbau_aus_ist`). Er nennt nur, was
    wirklich gesetzt ist, mit `--lauf`.
- **Sicherer Weg für Reste alter Läufe:**
  1. `startcheck` ausführen. Er meldet offene Journalvorgänge, eigene ACL-Einträge, Firewall-Regeln mit `run_id` und den
     Kinddatenträger.
  2. `acl-entfernen --lauf <run_id>` bzw. `fw-entfernen --lauf <run_id>` ausführen, zuerst ohne `--ausfuehren` (Plan),
     dann mit.
  3. Was als „unberührt“ gemeldet wird, ist **nicht** eigen. Es wird nie automatisch entfernt; die Gruppe wird nie
     pauschal gelöscht.
  4. `startcheck` erneut ausführen; der Start setzt `ok: true` voraus.
- **Tests:**
  - `test_acl_set_interrupted_at_each_step_is_rolled_back` (Schritte 1–3),
  - `test_firewall_set_interrupted_at_each_step_is_rolled_back` (Schritte 1, 3, 5),
  - `test_failed_rollback_is_recorded_as_incomplete`,
  - `test_power_loss_leaves_an_open_journal_entry_that_startcheck_reports` (Kindprozess mit `os._exit`, Schritte 1 und
    3),
  - `test_leftovers_of_an_old_run_are_removed_selectively`,
  - `test_acl_leftovers_of_an_old_run_are_removed_selectively`,
  - `test_status_files_of_several_runs_are_kept`,
  - §96: angepasste Rückbautests.
- **Restrisiko:**
  - Ein Stromausfall **zwischen** der Ausführung eines Befehls und dem Schreiben von `begonnen` ist ausgeschlossen, weil
    `begonnen` vorher geschrieben wird. Danach bleibt der Ausgang des Befehls unbekannt. `startcheck` meldet den Vorgang
    als offen; der Mensch entscheidet nach Ist-Zustand.
  - Geht das Journal selbst verloren, sind eigene Reste nicht mehr zweifelsfrei zuordenbar. `--lauf` verweigert dann.

### M8, M9 und N6 – Weitere Pfade und V9

- **Fix V9 E1:**
  - `mac_spoofing_aus`, `dhcp_guard_an`, `router_guard_an`.
  - `integrationsdienste_minimal`: aktiv nur Takt/Heartbeat, Herunterfahren und Zeitsynchronisierung
    (`ERLAUBTE_INTEGRATIONSDIENSTE`). KVP-Datenaustausch, Gastdienste und unbekannte Dienste ergeben FAIL; eine leere
    Liste gilt als unklar.
- **Fix V9 E0:** Routen über fremde Gateways (`Get-NetRoute`, N6) ergeben FAIL.
- **Unbekannte oder unlesbare Firewall-Regeln:** Sie gelten als unklar und ergeben FAIL (fail-closed).
- **Nicht abgedeckt:** Jedes Urteil enthält `nicht_abgedeckt`. Diese Pfade gelten ausdrücklich **nicht** als
  abgesichert:
  - ICMP und andere IP-Protokolle als TCP/UDP,
  - Ziele außer den Host-Kanarien (nur per Schluss aus der Deny-all-Regel),
  - Nicht-IP-Frames, Broadcast/Multicast (DHCP, LLMNR, mDNS, NetBIOS) und IPv6-Link-Local,
  - DNS über TCP, DoT/DoH, IPv6-DNS,
  - Hyper-V-Sockets (hvsocket/VMBus) und weitere Integrationskanäle (nur die Konfiguration ist geprüft, keine Probe),
  - Verhalten nach Ende des Prüfzeitraums (Belege gelten höchstens 24 h).

  Ebenso nicht belegt: die **Wirksamkeit** von MAC-Spoofing-Sperre, DHCP- und Router-Guard (geprüft ist nur die
  Einstellung).
- **Tests:**
  - `test_v9_adapter_protection_and_integration_channels` (7 Fälle),
  - `test_v9_routes_via_foreign_gateways_fail`,
  - `test_uncovered_paths_are_reported_in_every_verdict`.
- **Restrisiko:** Die genannten Pfade sind **ungetestet**. Ihr realer Schutz durch Hyper-V ist **NICHT VERIFIZIERT**.

### N1, N2, N4 und N5

- **N1 – Fix:** `startcheck` prüft den Kinddatenträger und meldet „Kinddatenträger fehlt“. Test: `test_startcheck_reports_missing_child_disk`
  und §96.
- **N2 – Restrisiko, kein Fix:** Eine geplante Aufgabe wäre eine Windows-Änderung und fällt damit außerhalb der Grenzen.
  Die Ablaufzeit bleibt eine Beschreibung. Abgelaufene Regeln erkennen `fw_pruefen` (Abbruch) und `startcheck` (Rest).
  Der Bediener muss nach einem Absturz `startcheck` ausführen.
- **N4 – Fix:** Kontrollpakete tragen eine zufällige Kennung (`CONTROL_PREFIX` + Nonce) und werden nur am Inhalt
  erkannt. Ein gleichzeitiger VM-Treffer auf dasselbe Ziel bleibt ein Treffer. Test:
  `test_control_packets_are_identified_by_content_not_target`.
- **N5 – Fix:**
  - `vm_clock_problem` vergleicht die Zeiten der VM-Belege mit den Host-Phasen (Start-Token, Ende der Kalibrierung).
  - Eine Abweichung über 5 Minuten oder widersprüchliche VM-Zeiten ergeben BLOCKIERT für jeden Test mit E0-Beleg.
  - Die Reihenfolge ist zusätzlich über die Phasen-Tokens gesichert.
  - Tests: `test_vm_clock_within_tolerance_passes`, `test_vm_clock_skew_blocks_every_vm_based_test`,
    `test_contradictory_vm_times_block`; §96: `test_vm_clock_is_checked_against_host_phases`.
- **Restrisiko N5:** Ein Administrator in der VM kann die Uhr innerhalb der Toleranz stellen und Tokens auslesen (Grenze
  des Modells, §97).

## 3 Restrisiken (Zusammenfassung)

1. **Reale Isolation NICHT DURCHGEFÜHRT.** Alle Ergebnisse sind simuliert.
2. Der Laufzeit-Fingerabdruck hasht keine Dateien in installierten Paketen (H2).
3. Der Schreibtest probiert und liest keine ACLs aus (M1).
4. Zwischen zwei Firewall-Prüfungen gibt es ein Fenster von 15 s (H1).
5. Nicht abgedeckte Pfade: ICMP, hvsocket, Nicht-IP, Broadcast/Multicast, IPv6-Link-Local, DNS über TCP/DoH,
   Wirksamkeit der Guards (M8, M9).
6. Die Ablaufzeit der Firewall-Regeln wird nicht automatisch durchgesetzt (N2).
7. Grenzen des Modells (§97): Administrator in der VM, kompromittierter Host, Absenderfälschung.

## 4 Tests

| Lauf | Ergebnis |
|---|---|
| `tests/test_s98_review_fixes.py` einzeln | **PASS** – 55 bestanden, 1 übersprungen (Symlink ohne Entwicklermodus) |
| Isolationssuites §86, §87, §90, §91, §92, §94, §96, §98 zusammen | **PASS** – 386 bestanden, 8 übersprungen (Zielumgebung bzw. Symlink), 0 fehlgeschlagen; 11:32 min |
| Benchmark-Tests §77–§85 | **NICHT DURCHGEFÜHRT** – Lauf von Claude Code wegen Speichermangel des Systems abgebrochen (kein Testergebnis); nicht eigenmächtig neu gestartet |
| Gesamtsuite (6 Teile) | **NICHT DURCHGEFÜHRT** – aus demselben Grund zurückgestellt; erwartet werden die 4 bekannten fremden Logo-Fehler in `tests/test_design_refresh.py` |
| Reale Abnahme V1–V13 | **NICHT DURCHGEFÜHRT** |

Alle Ergebnisse sind **simuliert** (FakeSystem, Loopback, synthetische Belege). Sie sind **kein Nachweis realer
Isolation**.
