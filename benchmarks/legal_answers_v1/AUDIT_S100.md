# §100 Abschlussreview vor F1 (Commit)

Stand 20.10.2026. **Keine Sicherheitsfreigabe.** Ein erfolgreicher lokaler Review ist keine reale Sicherheitsabnahme.
F1, D1 und C-14 bleiben gesperrt, bis du entscheidest.

## 0 Ausgangslage (geprüft)

- **Branch:** `feature/feedback-diagnose`. **HEAD:** `b7e2daa3b1c0a67dcfd55f2852e781b033817fd5`.
- **`git status`:** 35 Einträge. Gegenüber dem Stand vor §99 (33) sind nur `AUDIT_S99.md` und dieser Bericht neu.
- **Geänderte eigene Dateien (5):**
  - `scripts/legal_bench/isolation_gate.py`
  - `benchmarks/legal_answers_v1/isolation/vm_abnahme.json`
  - `tests/isolation_test_support.py`
  - `tests/test_s86_isolation_design.py`
  - `tests/test_s87_isolation_gate.py`
- **Neue eigene Dateien (12 Code/Tests):**
  - `scripts/legal_bench/isolation_acceptance.py`, `isolation_evidence.py`, `isolation_host_control.py`,
    `isolation_real.py`, `owner_keys.py`, `owner_sign.py`
  - `tests/test_s90…`, `s91…`, `s92…`, `s94…`, `s96…`, `s98…`
- **Berichte:** `AUDIT_S88.md` bis `AUDIT_S100.md` (untracked).
- **Fremd bzw. nicht anzufassen:**
  - `PRIVACY_REVIEW_ARCHITECTURE.md` (geändert),
  - `assets/branding/lexono-logo.png` und `Lexono-Logo Dark Mode.png` (gelöscht),
  - zwei neue PNG-Dateien in `assets/ux-ui/`.
- **Arbeitsweise:**
  - Rein lesend; keine Code-, System-, Netz- oder Kontenänderung.
  - Keine VM, keine Schlüssel, keine externe Verbindung. Die Produktivdatenbank wurde nicht geöffnet.
  - Eigene Nachweise R1–R4 liefen als Wegwerf-Skripte im Job-Ordner (`review100/`, außerhalb des Repositorys) gegen
    Kopie-Verzeichnisse bzw. das **simulierte** Hyper-V aus §94.

**Einstufung:**
- *bestätigt* = selbst reproduziert oder eindeutig im Code;
- *plausibel* = abgeleitet;
- *nicht verifiziert* = hängt von realem Windows-/Hyper-V-Verhalten ab.

## 1 Neue Befunde dieses Reviews

### HOCH

**A1 – Der Fingerabdruck erkennt nicht jede ausführbare Codequelle (§98-Anforderung „unbekannte Codequellen →
BLOCKIERT“ nicht vollständig erfüllt).** *bestätigt (R2, R3)*

- **R2a:** Eine `.pyc` mit ungeprüftem Hash in `app/__pycache__/` (aus *anderer* Quelle kompiliert) wird statt der
  geprüften `.py` geladen. Ergebnis: `import app.mod` liefert „FREMDER CODE“, der Fingerabdruck bleibt gleich.
- **R2b:** Eine quellenlose `app/nur_pyc.pyc` ist importierbar; der Fingerabdruck bleibt gleich.
- **R3:** Ein Paketordner in der Repository-Wurzel (`cryptography/__init__.py`) überdeckt die installierte Bibliothek,
  weil das Repository vorn im Suchpfad steht. Der Fingerabdruck bleibt gleich.
- **Was heute schützt:**
  - R3 erscheint in `git status` und führt damit zu V2 `arbeitsbaum_sauber` FAIL – es sei denn, der Ordner liegt unter
    einem ignorierten Namen.
  - R2 fällt dort **nicht** auf, weil `__pycache__/` und `*.py[cod]` in `.gitignore` stehen.
  - Gegen R2 schützt nur der Schreibschutz. Der Schreibtest probiert aber keine `__pycache__`-Ordner (siehe A4).
- **Nicht betroffen:** Verzeichnis-Junctions werden mitgehasht. Eine untracked Datei darin verhindert das Signieren
  (R1 *bestätigt*).
- **Empfehlung:**
  - Fingerabdruck und Schreibtest auf alle ladbaren Formen ausdehnen: `*.pyc`, `*.pyd`, `*.dll`, `__pycache__`,
    Paketordner in der Wurzel.
  - In der VM mit `-B` bzw. `PYTHONDONTWRITEBYTECODE` und `sys.flags.dont_write_bytecode` laufen.
  - Vor dem Lauf `__pycache__` prüfen: leer oder nur Caches im Modus `checked-hash`.
  - `git status --ignored` als V2-Kriterium, damit ignorierte, aber importierbare Dateien auffallen.

### MITTEL

| # | Befund | Einstufung | Nachweis | Empfehlung |
|---|---|---|---|---|
| A2 | Nach einem Stromausfall führt der in AUDIT_S98 §2 dokumentierte Weg (`startcheck` → `fw-entfernen --lauf` → `startcheck ok`) **nicht** zu `ok: true`. Der unterbrochene Vorgang `fw-setzen` behält im Journal den Status `geplant`; `fw-entfernen --lauf` legt nur einen *neuen* Vorgang an. Für den alten Vorgang gibt es keinen Abschluss, `startcheck` bleibt dauerhaft „nicht ok“. Das ist fail-closed, aber der einzige Ausweg wäre, das append-only-Journal von Hand zu bearbeiten | bestätigt (R4, simuliert) | Regeln danach 0, `startcheck 2 ok: False`, offener Vorgang `fw-setzen` | `--lauf`-Rückbau als Abschluss des offenen Vorgangs journalisieren (Verweis `abschluss_von=<vorgang_id>` nach nachgelesenem Ist-Zustand); Test für den vollständigen Wiederherstellungsweg |
| A3 | V13/N5 nur teilweise behoben: `vm_clock_problem` prüft die VM-Uhr zum **Zeitpunkt der Belege**. Das Gate in der VM prüft die Gültigkeit der Bestätigung beim **Gebrauch** weiterhin allein gegen die VM-Uhr (`now()`). Wer die VM-Uhr zurückstellen kann, verlängert eine abgelaufene Bestätigung. Dass das Benchmark-Konto kein Recht zum Stellen der Uhr hat (`SeSystemtimePrivilege`) und die Zeitsynchronisierung aktiv ist, prüft kein Kriterium | bestätigt (Code) | `isolation_gate._problems` (`valid_from <= now <= valid_to`) | V2-Kriterium „Konto ohne Uhr-Recht“; Gültigkeit zusätzlich an Host-Token oder Laufnummer binden; AUDIT_S98 „N5 behoben“ als „teilweise“ lesen |
| A4 | M1 nur teilweise behoben: Der Schreibtest prüft nicht `__pycache__`-Ordner, Unterordner von `site-packages` (Paketinhalt), `.venv\Scripts`, `pyvenv.cfg` und die Basis-Standardbibliothek. Auf diesem Host liegt die Basis-Python-Installation im Benutzerprofil (`AppData\Local\Programs\Python\Python313`); `pyvenv.cfg` zeigt dorthin. Gilt das in der VM ebenso, ist die Standardbibliothek für das Konto beschreibbar und **nicht** im Fingerabdruck (nur die Versionsnummer) | bestätigt (Code, Host-Pfade gelesen); VM nicht verifiziert | `observe_write_access`, `sys.path` | Python systemweit (Programme) installieren; Schreibtest und Fingerabdruck auf `sys.base_prefix`, `Scripts`, `pyvenv.cfg` und Paketordner ausdehnen; besser ACLs auslesen |

### NIEDRIG

| # | Befund | Einstufung | Empfehlung |
|---|---|---|---|
| A5 | AUDIT_S98 behauptet, `startcheck` verweigere den Start, solange ein Vorgang offen ist. Tatsächlich ist `startcheck` ein **manueller** Schritt: `host-lauf`, `acl-setzen` und `fw-setzen` lesen das Journal nicht (`host-lauf` läuft sogar ohne Journal). Reste verhindern den Start trotzdem (leere ACL bzw. Gruppe verlangt); offene, aber rückstandsfreie Vorgänge nicht | bestätigt (Code) | `journal_offen` in `acl-setzen`/`fw-setzen`/`host-lauf` erzwingen oder die Aussage korrigieren |
| A6 | Die periodische Firewall-Prüfung wird nur auf Fehler bewertet, nicht auf **Lückenlosigkeit**. Hängt eine Prüfung oder endet der Thread, fehlen Einträge unbemerkt; Pflicht sind nur `nachher_start` und `nachher_ende` | bestätigt (Code) | Anzahl und Abstand der Zwischenprüfungen gegen die Phasendauer prüfen (Lücke > 2 × Intervall → BLOCKIERT) |
| A7 | `Remove-NetFirewallRule -DisplayName …` wirkt auf **alle** Regeln dieses Anzeigenamens, auch außerhalb der Gruppe; Anzeigenamen sind unter Windows nicht eindeutig. Durch `run_id` im Namen unwahrscheinlich | plausibel / nicht verifiziert | über `-Name` (eindeutig) oder `Get-NetFirewallRule -Group … \| Where …` entfernen |
| A8 | `sign_attestation` verlangt `commit` und `sha256`, aber nicht `laufzeit_sha256` (das Gate lehnt dann ab: fail-closed, aber eine unbrauchbare Signatur entsteht). Außerdem muss die **Laufzeitumgebung von Host und VM identisch** sein (Pakete, `site-packages`-Einträge, `.pth`). Das ist nirgends als Bedienvoraussetzung beschrieben und führt real wahrscheinlich zu BLOCKIERT | bestätigt (Code); real nicht verifiziert | `laufzeit_sha256` beim Signieren verlangen; gleiche venv (Lock-Datei) als D1-Schritt dokumentieren |
| A9 | Testqualität: `test_v7_acceptance_requires_a_resolver_and_a_real_query` prüft nur den Quelltext (`inspect.getsource`). In `test_s98_review_fixes.py` ist der Import `Path` ungenutzt, und `test_unreadable_runtime_closes_the_gate` hat eine verschachtelte Konstruktion, die das Gate über ein ersetztes `tool` prüft statt über den echten Pfad | bestätigt | Verhaltenstest für V7; aufräumen |
| A10 | Wird eine getrackte Datei im Host-Arbeitsbaum **gelöscht**, signiert der Host einen Stand ohne sie; die VM (Checkout mit Datei) weicht ab, also ist das Gate geschlossen | bestätigt (Code), fail-closed | gelöschte Dateien beim Signieren erkennen (`git ls-files` gegen den Arbeitsbaum) |

## 2 Befund → Fix → Test (§95 und §97)

**Status-Legende:**
- **behoben** = Code geprüft und Regressionstest grün (lokal bzw. simuliert);
- **teilweise** = Fix vorhanden, aber Lücke bestätigt;
- **Restrisiko** = ausdrücklich dokumentiert, kein Fix;
- **offen** = weder Fix noch Testnachweis.

### §95

| Befund | Fix (Code geprüft) | Regressionstest | Status |
|---|---|---|---|
| H1 Treffer am Listener-Ende verloren | Leeren, Ruhezeit ≥ 2 s, `abschluss`, VM-Abschluss-Token | `test_udp_datagrams_just_before_exit_are_not_lost` u. a. (§96) | behoben; Pakete nach dem Schließen naturgemäß unbeobachtbar |
| H2 Ablehnung als Sperre | dreiwertige Proben; nur `zeitueberschreitung` sperrt; Gegenprobe verlangt `erreicht` (`_eval_gegenprobe`) | `test_probe_results_are_unambiguous`, `…calibration_without_reach…` | behoben |
| M1 Treffer außerhalb der Phasen | `ausserhalb_der_phasen` → FAIL (`_eval_kanarien`) | `test_hits_outside_phases_are_reported_and_fail` | behoben |
| M2 V9 nur Adapterzahl | `adapter_am_abnahme_switch` (Name und Typ `Internal`) | `test_v9_checks_the_actual_switch` | behoben; reale Formate nicht verifiziert |
| M3 V11 ohne Datenträger | Kind-/Eltern-Kette, Schreibschutz-Attribut, Rücksetzung vor der Gegenprobe (`_v11_cross`) | `test_v11_is_bound_to_the_vm_disk` u. a. | behoben; Readonly ist kein ACL-Schutz (Restrisiko) |
| M4 Fingerabdruck und Arbeitsbaum | §96/§98: alle `*.py` in Wurzel, `app/**` und `scripts/**`, Konfiguration, Laufzeit, Commit-Abgleich (`git cat-file`) | §96 und §98 Fingerabdruck-Tests | **teilweise** – A1, A4, A10 |
| M5 Policy Store | `-PolicyStore ActiveStore` | `test_guest_firewall_is_read_from_the_active_store` | behoben (statisch); real nicht verifiziert |
| M6 Uhren VM/Host | Phasen-Tokens; `vm_clock_problem` | §96, §98 Uhren-Tests | **teilweise** – A3 |
| M7 Rückbau und Reste | Firewall-Gruppe, Journal, `--lauf`, `startcheck`, Statusdateien | §96, §98 Rückbau-Tests | **teilweise** – A2, A5 |
| M8 Host-Dienste vorher | `host_dienste_in_vorher_phase_geschuetzt` | `test_v9_host_services_during_counter_probe` | behoben (lokal); eine Systementscheidung zu vEthernet ist offen (D1) |
| N1 V13 FAIL statt BLOCKIERT | Kriterium `blockiert` | `test_v13_without_trust_chain_is_blocked_not_failed` | behoben |
| N2 Teilstring „abgelaufen“ | exakte Meldung | `test_v13_is_semantic` | behoben |
| N3 UDP ohne Aussage | Echo | `test_s86_udp_probe_is_meaningful…` | behoben |
| N4 native Probe nur IPv4 | IPv6-Code vorhanden | nur in der Zielumgebung | **offen** (NICHT DURCHGEFÜHRT) |
| N5 V12 tautologisch | Hash gegen Kanarienlog (`_v12_cross`) | `test_v12_report_must_match_the_canary_log` | behoben |
| N6 V2-Versuch zuerst | zuletzt, mit Rücknahme | `test_change_attempt_runs_after_all_probes` u. a. | behoben (laut Test; Reihenfolge nicht erneut im Code nachgelesen) |
| N7 Benchmark-Konto | `laeuft_als_benchmark_konto` | `test_v2_…[falsches_konto]` | behoben |
| N8 Wiederverwendung 24 h | – | – | Restrisiko (gewollt, dokumentiert) |
| N9 geklonte MachineGuid | – | – | Restrisiko (dokumentiert) |
| N10 Anker-Schreibtest nur Datei | – | – | Restrisiko; Signatur und Wurzel-Pin im Code bleiben der Schutz |

### §97

| Befund | Fix (Code geprüft) | Regressionstest (§98) | Status |
|---|---|---|---|
| H1 Firewall/Listener nach „nachher“ | Kontrollen `vorher_ende`, `nachher_start`, `nachher_waehrend` (15 s), `nachher_ende`; sha-Gleichheit vorher/nachher; `_kontroll_problem` → BLOCKIERT | `test_firewall_removed_during_after_phase_blocks_canary_tests` u. a. | behoben; Rest: A6 und das 15-s-Fenster |
| H2 Fingerabdruck unvollständig | Globs, Laufzeit, Commit-Batch | `test_fingerprint_covers_all_executable_code` u. a. | **teilweise** – A1, A4 |
| M1 Schreibtest schmal | Codeordner, `site-packages`, `.pth` | `test_write_test_covers_app_dirs_site_packages_and_pth` | **teilweise** – A4 |
| M2 Prozess-Timeout als Sperre | `prozess_timeout`/`prozessfehler` | `test_hanging_probe_process_is_not_a_block` | behoben |
| M3 DNS-Probe ohne Aussage | `dnsquery` + nslookup strikt | `test_dns_query_probe_is_meaningful`, `test_nslookup_output…` | behoben; der V7-Test selbst ist schwach (A9) |
| M4 Abbruch bei `acl-setzen` | Rückbau bei `BaseException`, Journal | `test_acl_set_interrupted_at_each_step_is_rolled_back` | behoben (simuliert) |
| M5 alte Läufe | `--lauf` nur mit Journalbeleg | `test_leftovers_of_an_old_run_are_removed_selectively` | **teilweise** – A2 |
| M6 Status überschrieben | eine Datei je Lauf; Rückbau aus dem Ist-Zustand | `test_status_files_of_several_runs_are_kept` | behoben |
| M7 leere Portlisten | `None` = unklar | `test_empty_or_unreadable_port_lists_are_not_safe` | behoben |
| M8 Hyper-V-Kanäle | `integrationsdienste_minimal`; hvsocket unter „nicht abgedeckt“ | `test_v9_adapter_protection_and_integration_channels` | **teilweise** / Restrisiko (dokumentiert) |
| M9 Nicht-IP, Broadcast, Guards | Kriterien für MAC-Spoofing, DHCP- und Router-Guard; Liste „nicht abgedeckt“ | `test_v9_…`, `test_uncovered_paths_are_reported_in_every_verdict` | **teilweise** / Restrisiko (dokumentiert) |
| N1 Kinddatenträger | `startcheck` | `test_startcheck_reports_missing_child_disk` | behoben |
| N2 Ablauf nur Text | – | – | Restrisiko (Windows-Änderung außerhalb der Grenzen) |
| N3 nslookup-Teilstring | exakte Meldungen | `test_nslookup_output_is_interpreted_strictly` | behoben |
| N4 fremder Treffer als Kontrolle | Nonce im Paket | `test_control_packets_are_identified_by_content_not_target` | behoben |
| N5 VM-Uhr | `vm_clock_problem` | Uhren-Tests §96, §98 | **teilweise** – A3 |
| N6 Routen | `Get-NetRoute`, `keine_routen_ueber_fremde_gateways` | `test_v9_routes_via_foreign_gateways_fail` | behoben |

**Ausdrücklich geprüft (Code gelesen, lokal belegt):**
- Listener-Abschluss: `_abschluss_ok`.
- Phasenübergänge: Kontrollen nach `mark`, Treffer außerhalb der Phasen ergeben FAIL.
- Firewall-Überwachung.
- V9: fail-closed bei unklaren Regeln, Diensten und Guards.
- V11-Kette.
- V13: ohne Kette BLOCKIERT.

## 3 Fingerprint und Commit-Grenze – Ergebnis

| Eigenschaft | Ergebnis |
|---|---|
| `app/**/*.py`, `scripts/**`, `tests/conftest.py`, Wurzel-`*.py`, `pyproject.toml`, Konfiguration in `app/` | abgedeckt (401 Dateien) |
| `.pth`, Editable-Finder, `sitecustomize`, Einträge in `site-packages`, Import-Umgebung | abgedeckt (Laufzeit-Fingerabdruck) |
| Inhalt installierter Pakete | **nicht** abgedeckt (Restrisiko, dokumentiert) |
| `.pyc` (Cache und quellenlos), Wurzel-Pakete, `.pyd`/`.dll` | **nicht** abgedeckt (A1) |
| Basis-Standardbibliothek | **nicht** abgedeckt, nur die Version (A4) |
| Symlink-Datei | abgelehnt (Test übersprungen: ohne Entwicklermodus nicht anlegbar) |
| Junction | mitgehasht; untracked Inhalt verhindert das Signieren (R1) |
| Untracked bzw. gitignorte Fingerabdruck-Dateien | Signieren verweigert; heute 12 untracked Dateien, 0 gitignored |
| Signierwerkzeug akzeptiert nur den Commit-Inhalt | ja, für die erfassten Dateien; gelöschte Dateien siehe A10 |

**Vertrauensgrenze:** Die VM berechnet ihren Fingerabdruck mit ihrem eigenen Code. Wer diesen Code ändern kann, kann
auch das Ergebnis fälschen. Das ist nur durch Golden Image und Schreibschutz begrenzt (§95 M4, §97 Grenzen des Modells).

## 4 Rückbau und Systemrisiken – nachgewiesen und angenommen

- **Nachgewiesen (simuliert):**
  - Abbruch bei jedem Setzschritt baut zurück.
  - Ein gescheiterter Rückbau wird als `unvollstaendig` journalisiert.
  - Stromausfall hinterlässt einen offenen Vorgang.
  - Fremde Regeln bzw. Einträge werden nicht entfernt.
  - Statusdateien werden nicht überschrieben.
- **Lücke:** Der Wiederherstellungsweg endet in einer Sackgasse (A2). Er ist nicht erzwungen (A5).
- **Annahmen (nicht verifiziert):**
  - Semantik der Hyper-V Extended ACL (Richtung, Gewicht, IPv6 ND/DHCP),
  - Parameter von `New-`/`Remove-NetFirewallRule` und Eindeutigkeit der Anzeigenamen (A7),
  - ActiveStore, RST- und ICMP-Verhalten,
  - echte PowerShell-Formate.
- **Nicht abgedeckt:** ICMP und andere IP-Protokolle, Ziele außerhalb der Kanarien, Nicht-IP/Broadcast/Multicast und
  IPv6-Link-Local, DNS über TCP/DoT/DoH/IPv6, hvsocket/VMBus, Wirksamkeit der Guards, native IPv6-Probe.
- **Grenzen der Host-Belege:** E1 ist nicht unabhängig vom Owner. Ein kompromittierter Host, ein Administrator in der VM
  und Absenderfälschung bleiben außerhalb des Modells.

## 5 Tests und Qualität

| Prüfung | Ergebnis | Status |
|---|---|---|
| §77–§85 (AUDIT_S99) | 315 bestanden | **PASS** |
| Isolationssuites §86–§98 (AUDIT_S98) | 386 bestanden, 8 übersprungen | **PASS** |
| Gesamtsuite (AUDIT_S99) | 5626 bestanden, 4 fehlgeschlagen, 1 übersprungen | **FAIL** nur durch die 4 bekannten fremden Logo-Tests |
| Erwartete Fehlschläge | 10 xfail und 1 xpass, alle in Datenschutztests außerhalb von §90–§98 | nachvollziehbar (siehe unten) |
| Ersatz-Statikprüfung (Syntax, ungenutzte Importe; im Speicher, keine `.pyc`) | 16 Dateien Syntax ok; 1 ungenutzter Import (A9) | **PASS** mit Hinweis |
| Lint (ruff) | nicht installiert, keine Installation erlaubt | **NICHT DURCHGEFÜHRT** |
| Reale Abnahme V1–V13 | – | **NICHT DURCHGEFÜHRT** |

**Erwartete Fehlschläge:**
- Alle Markierungen nennen einen fachlichen Grund, z. B. Gattungswort-Namen, Probe-Schwelle oder OCR-Prüfziffer. Die
  strikt markierten (`strict=True`) würden ein unerwartetes Bestehen melden.
- Der eine xpass ist `test_known_limit_name_that_is_also_a_common_noun[Richter]` (`strict=False`, Grund „Richter/Fürst:
  Gattungswort ohne unabhängiges Signal“). Der Name wird inzwischen maskiert, die Grenze besteht also für „Richter“ nicht
  mehr. Das ist kein Fehler.
- Empfehlung, getrennt von F1: die Markierung für „Richter“ prüfen und gegebenenfalls aufheben.

**Die 4 bekannten fremden Logo-Fehler** (`tests/test_design_refresh.py`):
- `test_full_logo_lockup_assets_have_a_real_alpha_channel`
- `test_dark_logo_icon_is_byte_identical_to_the_light_logo_icon`
- `test_dark_logo_has_identical_overall_geometry_to_light_logo`
- `test_dark_logo_wordmark_is_recolored_to_white`

Ursache sind die fremden Logo-Änderungen im Arbeitsbaum. Sie wurden nicht behoben.

**In diesem Review nicht ausgeführt:**
- die Suiten erneut (die Ergebnisse aus §98/§99 gelten für denselben, unveränderten Arbeitsbaum),
- Lint, reale Abnahme, Build/Installation.

## 6 Empfehlung zu F1

**F1 (Commit des Stands §90–§100 auf diesem Branch) ist vertretbar** – als festgeschriebener **Zwischenstand**, nicht als
Sicherheitsabnahme. Begründung:
1. F1 schaltet nichts frei. Das Gate bleibt geschlossen (`PINNED_ROOT_FINGERPRINTS` leer), C-14 bleibt gesperrt, die
   Endpunktsperre bleibt unberührt. Ein reales Signieren ist ohne Wurzelschlüssel (D4) unmöglich.
2. F1 ist Voraussetzung für die Commit-Bindung. Ohne Commit kann `owner_sign` nie signieren, weil 12 Dateien untracked
   sind.
3. Die offenen Befunde A1–A4 verhindern kein falsches PASS **ohne** reale VM. Sie müssen aber vor jeder realen
   Signatur behoben sein. Ein späterer Fix-Commit ist damit vereinbar; die Signatur bindet ohnehin genau einen Commit.

**Bedingungen für F1:**
- **Nur eigene Dateien committen:** die 5 geänderten und 12 neuen Code-/Testdateien aus §0 sowie die Berichte
  AUDIT_S88–S100 (vorher prüfen, dass AUDIT_S88 aus dieser Reihe stammt).
- **Nicht committen:** die 4 fremden Logo-Dateien und `PRIVACY_REVIEW_ARCHITECTURE.md`.
- **Kein Merge nach `main`**, kein Push ohne deine gesonderte Freigabe.
- **Commit-Nachricht** kennzeichnet ausdrücklich: „nicht sicherheitsabgenommen; offene Befunde AUDIT_S100 A1–A10;
  V1–V13 real nicht durchgeführt“.

**Vor D1 bzw. der ersten realen Signatur (nicht F1-blockierend):**
- A1 (`.pyc` und Wurzel-Pakete) beheben,
- A3 (Uhr beim Gebrauch) und A4 (Schreibtest, Standardbibliothek) beheben,
- A2 (Wiederherstellungsweg) beheben,
- danach einen erneuten Review.

A5–A10 nach Bedarf.

**Nicht vertretbar wäre:** F1 als Freigabe darzustellen, auf `main` zu mergen oder auf dieser Basis D1 bzw. eine reale
Signatur zu starten.

| Punkt | Status |
|---|---|
| Lokaler Review §90–§98 | durchgeführt; 1 HOCH, 3 MITTEL, 6 NIEDRIG neu |
| F1 | vertretbar unter den Bedingungen in §6 – **deine Entscheidung** |
| D1, reale Abnahme, Sicherheitsfreigabe | **NICHT DURCHGEFÜHRT** / keine Freigabe |
| C-14 | **GESPERRT** |
