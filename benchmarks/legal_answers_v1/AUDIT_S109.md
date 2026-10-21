# §109 Unabhängiger Sicherheitsreview der §108-Änderungen und A1–A20

Stand 21.10.2026. **Keine Sicherheitsfreigabe.** Statischer Review und simulierte/Unit-Tests beweisen **keine reale
Isolation**. D1, Signierung und C-14 bleiben **GESPERRT**.

## 0 Ausgangszustand (geprüft)

- **Branch:** `feature/feedback-diagnose`. **HEAD:** `8e94bc1e49792bf83d32462f04a4af12f88e9840`. **Staging:** leer.
- **Working Tree:** 26 vorhandene (geänderte/neue) Dateien plus die gelöschten Logos; §102–§108 uncommittet. Fremde
  Dateien (`PRIVACY_REVIEW_ARCHITECTURE.md`, zwei gelöschte Logos, zwei UX-Bilder) unangetastet.
- **Methode:** rein lesend; keine Statusangabe aus den Vorberichten übernommen, sondern am Code geprüft. Eigene
  Nachweise als Wegwerf-Skripte im Job-Ordner (`review109/`). SHA-256 aller Arbeitsbaumdateien vor/nach dem Review
  verglichen (§6).

**Beleg-Klassen:** [Code] gelesen · [Test] vorhandener Test · [Nachweis] eigener lokaler Nachweis.

## 1 A18/A19 – unabhängig geprüft

**Codepfad:** `isolation_evidence._abdeckung_problem` → aufgerufen in `_eval_kanarien` nach `_kontroll_problem`;
`_eval_kanarien` ist der Evaluator für die Kanarientests (V4–V8, V12) und fließt über `evaluate` ins Gate.

**Geprüfte Eigenschaften:**
- **Fensterbindung [Code]:** Abdeckung wird an `aktiv_von..aktiv_bis` gemessen; fehlendes/ungültiges Fenster oder
  `aktiv_bis < aktiv_von` → BLOCKIERT.
- **dauer_s/Fenster-Konsistenz (A18) [Nachweis]:** `|dauer_s − Fenster| ≤ 45 s`; `dauer_s=2` bei 30-min-Fenster →
  BLOCKIERT.
- **Lückenprüfung (A19) [Nachweis]:** Anker Fensteranfang/-ende + Kontrollen; größte Lücke ≤ 45 s. Grenzfall exakt
  geprüft: Lücke == 45 s → PASS, Lücke == 46 s → BLOCKIERT. Teil-Hang (Kontrollen nur in den ersten 45 s) → BLOCKIERT.
- **Getrennt Firewall/Listener [Code/Nachweis]:** eine Lücke in nur einem Kanal blockiert
  (`test_listener_gap_blocks_even_if_firewall_is_complete`).
- **Zeitstempel/Monotonie/Fenstergrenze [Test]:** fehlend, ungültig, umsortiert, außerhalb des Fensters → je BLOCKIERT.
- **Unabhängigkeit A18/A19 [Nachweis]:** selbst wenn `dauer_s` exakt an der Toleranzgrenze zum Fenster liegt, bleibt
  die Lückenprüfung innerhalb des Fensters wirksam (Beispiel blockiert wegen Rest-Lücke).
- **Kurze/leere Phasen [Code]:** keine Kontrollen + Fenster ≤ 45 s → PASS; Fenster > 45 s ohne Kontrollen → BLOCKIERT
  (ein Start-/Endcheck allein kompensiert nicht).

**Synthetik/Dry-Run/Modus [Code]:** Synthetische und Dry-Run-Belege tragen `umgebung.modus = "trockenlauf"`. `evaluate`
lehnt sie produktiv ab (`modus not in ("real",)` ohne `allow_dry_run`; Zeile 1044–1045). Eine synthetische/Dry-Run-
Kontrolle kann also NIE als reale Kontrolle in einem Produktiv-Gate zählen. Das ist die maßgebliche Trennung.

**Vertrauensgrenze (zentral):** Firewall- und Listener-Kontrollen samt `dauer_s` und Zeitstempeln sind **E1 (Host)**.
A6/A18/A19 sichern die **Integrität der Host-eigenen Überwachung** (die Kanarien liegen auf dem Host) — sie sind KEIN
unabhängiger VM-Beweis und können einen kompromittierten Host nicht erkennen (Modellgrenze, AUDIT_S95). Für ehrliche
Betriebsfehler (hängender/abgestürzter Thread, Uhrensprung, widersprüchliche Angaben) greifen sie. **Urteil A18/A19:
BEHOBEN** (unter dieser ausdrücklichen Modellgrenze).

## 2 A20 und A5 – zusammen geprüft

**Codepfade:** `startcheck_frisch`, `_require_startcheck` (in `acl_setzen`/`fw_setzen`/`image_zuruecksetzen`),
`acl_setzen`/`fw_setzen` Leerprüfung (`before`), `aktiver_acl_besitzer` (A11).

**A5 [Code/Test]:** `_require_startcheck` greift bei geführtem Journal und erzwingt einen aktuellen, erfolgreichen
Startcheck für JEDEN Aufrufweg (CLI und direkter Methodenaufruf). Der Test-Opt-out `startcheck_pflicht=False` kommt
**ausschließlich in Tests** vor — im Produktionscode (`scripts/`) gibt es KEIN Vorkommen (verifiziert per grep). Die
CLI konstruiert `HostControl` stets mit Journal (Default `startcheck_pflicht=True`); `host_lauf` mutiert nie
(`ausfuehren=False`). **A5: BEHOBEN**; Restrisiko: journalloser Aufruf (kein Produktionspfad), 12-h-Heuristik.

**A20 [Code/Gegenbeispiel]:** `startcheck_frisch` bindet an `run_id` + 12-h-Fenster, nicht an einen Zustands-Hash. Der
Zustand zum Operationszeitpunkt ist jedoch unabhängig abgesichert:
- `acl_setzen`/`fw_setzen` setzen nur bei LEERER Ist-Ressource (sonst Abbruch) → Reste eines konkurrierenden Laufs
  blockieren das Setzen;
- `acl_entfernen` ist über `aktiver_acl_besitzer` an den Lauf gebunden → fremde aktive ACL → BLOCKIERT.
Gegenbeispiel „veralteter Beleg“: ein ok-Startcheck vor >12 h → `startcheck_frisch` False (geprüft §106). Gegenbeispiel
„fremder Eingriff danach“: mutierender Vorgang eines anderen Laufs nach dem Startcheck → False. Verbleibend: zwei
Operatoren mit `LEXONO_ISOLATION_ACCEPTANCE=1` am selben Host gleichzeitig — außerhalb des Einzel-Owner-Modells
(CLAUDE.md: keine IT-Abteilung). **A20: TEILWEISE/OFFEN** — akzeptiertes Restrisiko, da die per-Ressourcen-Leerprüfung
+ A11 den praktisch relevanten Fall abdecken und eine zusätzliche Zustandsbindung den regulären Ablauf (startcheck →
fw-setzen → acl-setzen) bräche.

## 3 A1–A17 – erneut bewertet

| # | Urteil | Kurzbegründung (Codepfad/Beleg) |
|---|---|---|
| A1 Codequellen/Bytecode | BEHOBEN (lokal) | `code_source_problems`+`bytecode_isolation`; Restrisiko Paketinhalt |
| A2 Recovery | BEHOBEN (sim.) | `_close_open`, `journal_offen`, `journal_quittieren`; §102/§104-Tests |
| A3 Zeitanker | TEILWEISE | `vm_zeitbasis`/`zeitanker_probleme`; Pause/Snapshot/Boot-ID NICHT VERIFIZIERT |
| A4 Schreibrechte | BEHOBEN (real-lokal) | `write_access`; ACL-Rechte (WRITE_DAC/OWNER/DELETE) offen; echte venv nicht geprüft |
| A5 Startcheck | BEHOBEN | §2; Restrisiko journalloser Pfad |
| A6 Zwischenprüfungen | BEHOBEN | §1 (A18/A19 inbegriffen) unter Host-Modellgrenze |
| A7 FW-Rückbau gruppenweise | BEHOBEN (Code) | `_fw_remove_command -Group`; Windows-Semantik NICHT VERIFIZIERT |
| A8 Signierintegrität | BEHOBEN (Logik) | `sign_attestation` Laufzeit-Pflicht+TOCTOU; keine echte Signatur; TOCTOU nicht atomar |
| A9 Testqualität | BEHOBEN | Quelltexttests durch Verhaltenstests ersetzt |
| A10 Dateivollständigkeit | BEHOBEN (Logik) | `committed_stand`+`im_fingerprint`+`ls-tree`; git vertrauenswürdig vorausgesetzt |
| A11 ACL-Lauf-Zuordnung | BEHOBEN (sim.) | `aktiver_acl_besitzer` |
| A12 Windows-Schreibprüfung | BEHOBEN (real-lokal) | `_win_create`/`_classify`; echte venv nicht geprüft |
| A13 Beleggültigkeit | TEILWEISE | an A3 gekoppelt |
| A14 importierbare ausgeschl. Ordner | BEHOBEN | `_importable_excluded` |
| A15 Endungen case-insensitiv | BEHOBEN | `.lower().endswith` |
| A16 weitere Schreibschutzlücken | TEILWEISE | Ordner-Anlage geprüft; ACL-Rechte/dist-info offen |
| A17 E2E-Fallback | BEHOBEN | `spec_from_file_location` ohne sys.path-Mutation |

Kein neuer Fail-open-Pfad gefunden; alle offenen Punkte sind fail-closed oder dokumentierte Modellgrenzen.

## 4 Neue Befunde

Keine neuen Code-Schwachstellen (A21 ff.) gefunden. Beobachtungen (keine eigenständigen Befunde, bereits von den
Modellgrenzen abgedeckt):
- **B-1 (Beobachtung):** A6/A18/A19 und A3/A13 teilen die **gemeinsame Ursache** „Host liefert Zeit-/Dauerangaben“.
  Das ist konsistent als Modellgrenze dokumentiert; eine echte Minderung bräuchte eine vom Host unabhängige Zeitquelle,
  die im Konzept (Owner-/Host-Vertrauen) nicht vorgesehen ist.
- **B-2 (Beobachtung):** A8-TOCTOU und A10 setzen ein vertrauenswürdiges `git`/Dateisystem voraus; ebenfalls
  Modellgrenze.

## 5 Tests und Beweisgrenzen

| Prüfung | Ergebnis | Klasse |
|---|---|---|
| `tests/test_s106_review_fixes.py` (A5/A6/A18/A19) | 22 bestanden | [Test] |
| Isolationssuiten §86–§106 | in §108 vollständig grün (Baum seither unverändert, SHA-256 geprüft) | [Test], übernommen |
| Eigene Grenzfall-/Konsistenz-Nachweise (MAX, A18, Modus) | wie §1/§2 | [Nachweis] |
| Gesamtsuite, Lint (ruff fehlt), echte Signatur, reale Abnahme V1–V13 | NICHT DURCHGEFÜHRT | — |

Grüne Tests belegen nur das erwartete Verhalten des Codes; **keine reale Isolation**. Die A18/A19-Garantien gelten nur
für ehrlich gemeldete Host-Zeitangaben.

## 6 Voraussetzungen und Abschluss

**Zwischencommit (NICHT SICHERHEITSABGENOMMEN):** vertretbar. In der Commit-Nachricht nennen: A20 (TEILWEISE), A3/A13
(Timing NICHT VERIFIZIERT), A7/A8/A10/A12 (reale Semantik NICHT VERIFIZIERT), A16 (ACL-Rechte offen), sowie die
Host-Modellgrenze für A6/A18/A19. Nur eigene Dateien; fremde unangetastet.

**Vor D1 / realer Abnahme:** A3/A13-Timing (Pause/Snapshot/Boot-ID), A7/A12-Windows-Semantik und A4/A12 auf der echten
venv real prüfen; A16 (ACL-Rechte) ergänzen; identische venv Host/VM, Python systemweit, Isolationsvariablen gesetzt;
Abdeckungs- und Startcheck-Ablauf real durchspielen.

**Sicherheitsfreigabe:** erst nach realer Abnahme V1–V13, echter Owner-Signatur (D4) und erneutem unabhängigem Review.

**Abschlussprüfung:** SHA-256 aller 26 Arbeitsbaumdateien vor/nach dem Review identisch; `git status` zusätzlich nur
`?? benchmarks/legal_answers_v1/AUDIT_S109.md`; Staging leer; HEAD unverändert `8e94bc1`. **Keine Sicherheitsfreigabe.**
