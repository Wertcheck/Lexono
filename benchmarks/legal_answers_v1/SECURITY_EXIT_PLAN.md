# Sicherheits-Abschlussplan Isolations-/Signierkette (Lexono)

Stand 21.10.2026. Branch `feature/feedback-diagnose`, HEAD `c9cecc3` (Zwischencommit §102–§109) plus uncommittete
Änderungen §110/§112. Dieser Plan konsolidiert die Befunde A1–A21 aus AUDIT_S100 und AUDIT_S103–S112.

> **Nachtrag 25.10.2026 (nur Stand, keine inhaltliche Änderung):**
> - Die §110/§112-Änderungen sind seit 21.10.2026 als Commit `8f0b702` („checkpoint §110-§112 — NOT SECURITY
>   APPROVED“) festgehalten; dieser Plan selbst ist Teil dieses Commits.
> - Der in §4 genannte Schritt „Commit des §110/§112-Stands (als weiterer Zwischenstand)“ ist damit erledigt. Der Beginn
>   der realen D1-Vorbereitung ist weiterhin **nicht** freigegeben.
> - Aktueller HEAD: `8a219ec`. Die Befundmatrix A1–A21 und die Kriterien §2/§3 sind unverändert.
> - **Keine Sicherheitsfreigabe.** D1, Signierung und C-14 bleiben **GESPERRT**.

**Status dieses Dokuments:** Arbeitsstand, **NICHT SICHERHEITSABGENOMMEN**. Es hebt keine Sperre auf. D1, Signierung
und C-14 bleiben **GESPERRT**. Nach diesem Plan wird **keine** weitere nummerierte Prüfrunde automatisch begonnen; neue
Arbeit nur bei einem konkreten kritischen/hohen Befund oder einer nicht erfüllten Pflichtbedingung (§4).

**Grundsätzliche Beweisgrenze:** Alle „BEHOBEN“-Status sind durch **lokale Unit-Tests bzw. Simulation** (simuliertes
Hyper-V, Loopback, synthetische Belege, tmp-ACLs) belegt, einige durch **reale lokale** Windows-Messungen (als
„real-lokal“ gekennzeichnet). **Keine** reale VM-Abnahme (V1–V13), **keine** echte Owner-Signatur. **Host-erzeugte
Belege (E1) sind kein Nachweis gegen einen kompromittierten Host**; ein Administrator in der VM und ein kompromittierter
Host liegen außerhalb des Vertrauensmodells (AUDIT_S95).

## 1 Befundmatrix A1–A21

| # | Thema | Status | Beleglage | Restrisiko / erforderliche Maßnahme |
|---|---|---|---|---|
| A1 | Codequellen/Bytecode-Isolation | BEHOBEN (lokal) | Unit + real-lokal (Kindprozess) | Paketinhalt ungehasht → hängt an A16/venv-Schreibschutz |
| A2 | Recovery/Journal | BEHOBEN (sim.) | Unit/Sim | Journalverlust → manuelle Entscheidung |
| A3 | Zeitanker (VM-Zähler/Boot-ID) | TEILWEISE | Unit; Zählerverhalten unbelegt | **real messen**: Pause/Snapshot/Save, Boot-ID-Lesbarkeit |
| A4 | Schreibrechte (Umfang) | BEHOBEN (real-lokal) | real-lokal (tmp) | echte venv nicht geprüft → bei D1 |
| A5 | Startcheck erzwungen (Aufrufweg) | BEHOBEN | Unit/Sim | journalloser Aufruf (kein Produktionspfad); 12-h-Heuristik |
| A6/A18/A19 | Zwischenprüfungs-Abdeckung | BEHOBEN | Unit/Sim + Nachweise | host-produzierte Zeit (Modellgrenze) |
| A7 | FW-Rückbau gruppenweise | BEHOBEN (Code) | Code/Sim | **Windows-Semantik real prüfen** (`Remove-NetFirewallRule -Group`) |
| A8 | Signierintegrität/TOCTOU | BEHOBEN (Logik) | Unit | TOCTOU nicht atomar; **keine echte Signatur** (D4) |
| A9 | Testqualität | BEHOBEN | Code | Unit/Sim ≠ reale Isolation |
| A10 | Dateivollständigkeit beim Signieren | BEHOBEN (Logik) | Unit (git-Stub) | vertrauenswürdiges git/Dateisystem vorausgesetzt |
| A11 | ACL-Lauf-Zuordnung | BEHOBEN (sim.) | Unit/Sim | journalgebunden |
| A12 | Windows-Schreibprüfung (WinAPI) | BEHOBEN (real-lokal) | real-lokal (tmp, Read-only-Attr., Deny-ACL) | echte venv nicht geprüft |
| A13 | Beleggültigkeit beim Gebrauch | TEILWEISE | an A3 gekoppelt | mit A3 real klären |
| A14/A15 | Importpfade/Endungen | BEHOBEN | Unit + real-lokal | – |
| A16 | ACL-Rechte (WRITE_DAC/WRITE_OWNER), dist-info | BEHOBEN (lokal) | real-lokal + Stub | Owner-Rights-ACE (S-1-3-4) & echte venv NICHT VERIFIZIERT; **venv fremdem Eigentümerkonto** |
| A17 | E2E-Fallback | BEHOBEN | Unit | – |
| A20 | Startcheck-Zustandsbindung | TEILWEISE/akzeptiert | Code | Einzel-Owner-Modell (CLAUDE.md); durch Leerprüfung+A11 abgesichert |
| A21 | pytest-Plugin-Autoload | BEHOBEN (lokal) | Unit | Wirkung real NICHT VERIFIZIERT; hängt an venv-Schreibschutz |

Alle A1–A21 sind damit **behoben oder als ausdrückliches Restrisiko dokumentiert**. Keine offenen
kritischen/hohen Code-Befunde.

## 2 Trennung der offenen Punkte

### 2.1 Zwingende Voraussetzungen vor einer realen Abnahme (D1)
Objektiv, binär prüfbar:
1. **Commit & Signaturbasis:** Der freizugebende Stand ist committet; `owner_sign.committed_stand(HEAD).sha256` ist
   nicht None (A10). D4 (Wurzelschlüssel) liegt vor; `PINNED_ROOT_FINGERPRINTS` gesetzt.
2. **venv-Schreibschutz (A16/A4/A1/A21):** Repository, venv (inkl. Scripts, site-packages, dist-info, `.pth`) und die
   Python-Standardbibliothek gehören einem **getrennten Eigentümerkonto** und sind für das Benchmark-Konto weder im
   Inhalt noch per WRITE_DAC/WRITE_OWNER änderbar. Nachweis: `observe_write_access` liefert `beschreibbar == []` und
   `nicht_pruefbar == []` über alle Pfadgruppen (`repository`, `venv`, `basis`).
3. **Bytecode-/Plugin-Isolation (A1/A21):** In der VM sind `PYTHONDONTWRITEBYTECODE=1` und ein nicht existierender,
   geschützter `PYTHONPYCACHEPREFIX` gesetzt; `run_pytest` erzwingt `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`; der Satz
   freigegebener Plugins ist leer oder explizit geprüft.
4. **Python systemweit** installiert (nicht im Benutzerprofil des Benchmark-Kontos).
5. **Gate schließt ohne gültige Kette:** ohne Schlüsselliste/Anker → BLOCKIERT; Trockenlauf-Belege werden produktiv
   abgelehnt (verifiziert).
6. **Startcheck grün** unmittelbar vor dem Lauf (`startcheck.ok == True`, keine Reste, VM aus, Kinddatenträger,
   Journal ohne offene Vorgänge).

### 2.2 Erst nach realen Tests bewertbar (NICHT VERIFIZIERT)
Diese Eigenschaften sind lokal nicht belastbar prüfbar und müssen in der realen VM/auf dem Host gemessen werden:
- **A3/A13 Timing:** Verhalten von `GetTickCount64` bei VM-Pause/Snapshot/Save; Boot-ID-Lesbarkeit.
- **A7/A12 Windows-Semantik:** Hyper-V Extended ACL (Richtung/Gewicht/IPv6), Firewall-/`Remove-NetFirewallRule -Group`-
  Verhalten, ActiveStore; Schreibtest gegen die **echte** venv.
- **A16 Owner-Rights-ACE (S-1-3-4):** ob der implizite WRITE_DAC des Eigentümers eingeschränkt ist.
- **Hyper-V-Kanäle** (hvsocket/VMBus), Nicht-IP/Broadcast/Multicast, DHCP/Router/MAC-Guard-Wirksamkeit (als „nicht
  abgedeckt“ im Urteil geführt).
- **Reale Isolation V1–V13** insgesamt.

### 2.3 Akzeptierte Restrisiken (dokumentierte Entscheidung)
- **A20 Einzel-Owner:** Kein Schutz gegen zwei gleichzeitige Operatoren mit `LEXONO_ISOLATION_ACCEPTANCE=1` am selben
  Host. Akzeptiert, da Einzel-Owner-Betrieb (CLAUDE.md: keine IT-Abteilung). Mitigation: per-Ressourcen-Leerprüfung +
  A11.
- **A8 TOCTOU:** Zwei-Lesungen-Prüfung, nicht atomar; ein Schreibzugriff genau zwischen zweiter Lesung und
  Signaturberechnung bliebe unentdeckt. Akzeptiert als Teil der Host-Vertrauensgrenze.
- **A1/A16 Paketinhalt:** venv/Standardbibliothek werden nicht gehasht (nur Distribution Name==Version, `.pth`,
  Einträge). Akzeptiert unter der Bedingung des venv-Schreibschutzes (§2.1 Nr. 2).
- **Host-/Admin-Vertrauen:** Alle E1/E0-Belege setzen einen nicht kompromittierten Host und keinen Administrator in der
  VM voraus (Modellgrenze).

### 2.4 Optionale Verbesserungen (außerhalb des vereinbarten Umfangs)
- Wheel-/`RECORD`-Hash-Prüfung des Paketinhalts (schlösse den A1/A16-Paketinhalt-Rest).
- Atomare Signatur (Datei-Snapshot/Lock) statt TOCTOU-Doppellesung (A8).
- Unabhängige, vom Host entkoppelte Zeitquelle für A3/A13.
- Explizite ACL-Auswertung (`GetEffectiveRightsFromAcl`) zusätzlich zur Zugriffsprobe (A16).

## 3 Objektive Abschlusskriterien

Die Isolations-/Signierkette gilt als **abnahmebereit vorbereitet** (nicht: freigegeben), wenn:
1. alle Punkte aus §2.1 nachweislich erfüllt sind;
2. die lokalen Isolationssuiten §86–§112 vollständig grün sind (zuletzt: Teil 1 342 bestanden/8 übersprungen, Teil 2
   198/1 — unter `PYTHONDONTWRITEBYTECODE=1`);
3. jedes §2.2-Element entweder real gemessen und bestanden oder ausdrücklich als Restrisiko vom Owner akzeptiert ist;
4. die §2.3-Restrisiken vom Owner schriftlich akzeptiert sind.

Die **Sicherheitsfreigabe** (Aufhebung von D1/Signierung/C-14) erfolgt erst, wenn zusätzlich:
- die reale Abnahme V1–V13 durchgeführt und bestanden ist,
- eine echte Owner-Signatur (D4) vorliegt,
- ein erneuter unabhängiger Review **nach** D1 keinen kritischen/hohen Befund offen lässt.

## 4 Weiteres Vorgehen

- **Kein** automatischer Start einer weiteren §-Prüfrunde.
- Neue nummerierte Arbeit nur bei: (a) einem konkreten kritischen/hohen Befund, oder (b) einer in §2.1 nicht erfüllten
  Pflichtbedingung, oder (c) ausdrücklicher Owner-Entscheidung, D1 vorzubereiten.
- Der nächste sinnvolle Schritt ist eine **Owner-Entscheidung**: Commit des §110/§112-Stands (als weiterer
  Zwischenstand) und/oder Beginn der realen D1-Vorbereitung gemäß §2.1 — beides nur auf ausdrückliche Freigabe.

**Keine Sicherheitsfreigabe. D1, Signierung und C-14 bleiben GESPERRT.**
