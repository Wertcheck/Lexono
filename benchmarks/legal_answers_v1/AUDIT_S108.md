# §108 Behebung A18/A19 (Abdeckung der Zwischenprüfungen); A20 untersucht

Stand 21.10.2026. Branch `feature/feedback-diagnose`, HEAD `8e94bc1` (geprüft). Die Änderungen sind **uncommittet**.
**Keine Sicherheitsfreigabe.** D1, Signierung und C-14 bleiben **GESPERRT**.

**Grenzen:** Nur lokale Code-, Test- und Dokumentänderungen. Keine System-, Hyper-V-, Netz-, Firewall-, Konto- oder
Rechteänderung. Keine VM, keine echten Schlüssel/Signaturen, keine externen Aufrufe, keine Produktivdatenbank.
Unit-Tests und Simulation sind **kein Nachweis realer Isolation**.

**Beleg-Klassen:** [Code] gelesen · [Unit] Unit-/Regressionstest · [Nachweis] eigener lokaler Nachweis · [Sim]
simuliertes Hyper-V.

## 1 Geänderte Dateien

| Datei | Befund | Art |
|---|---|---|
| `scripts/legal_bench/isolation_evidence.py` | A18, A19 | `_abdeckung_problem` neu: fenstergebunden, Lückenprüfung statt Zählung; `NACHHER_KONTROLLPUNKTE` |
| `scripts/legal_bench/isolation_acceptance.py` | A18, A19 | synthetische Kontrollen decken das Fenster ab, `dauer_s == Fensterlänge`; Dry-Run-Firewallkontrollen mit Zeitstempel |
| `tests/test_s106_review_fixes.py` | A18, A19 | Unit- und Ende-zu-Ende-Tests auf die fensterbasierte Logik umgestellt und erweitert |

A20 erfordert keine Codeänderung (siehe §2.3).

## 2 Befunde

### A18 – Phasendauer gegen das Phasenfenster — **BEHOBEN**

**Ursprung:** AUDIT_S107 §3 A18. Die erforderliche Abdeckung wurde allein aus der frei vorgegebenen `dauer_s`
abgeleitet; eine zu klein gemeldete Dauer hob die Prüfung aus.

**Änderung [Code]:** `_abdeckung_problem` leitet die Abdeckung jetzt aus dem **beobachteten Phasenfenster**
`aktiv_von..aktiv_bis` ab. Fehlt/ist das Fenster ungültig oder liegt `aktiv_bis` vor `aktiv_von` → BLOCKIERT.
Zusätzlich muss die (monoton gemessene) `dauer_s` zum Fenster passen: `|dauer_s − (aktiv_bis − aktiv_von)| ≤
MAX_KONTROLL_LUECKE_S` (= 45 s, aus `KONTROLL_INTERVALL_S = 15 s`, kein erfundener Grenzwert); sonst BLOCKIERT.

**Belege:**
- [Nachweis] `dauer_s=2` bei 30-min-Fenster → „Phasendauer 2s widerspricht dem Phasenfenster 1800s“.
- [Unit] `test_duration_inconsistent_with_window_blocks`, `test_missing_window_blocks`,
  `test_missing_phase_duration_blocks` (fehlend/ungültig).

**Restrisiko:** Beide Werte stammen vom Host (E1). Ein kompromittierter Host könnte Fenster UND `dauer_s` konsistent
fälschen (Modellgrenze, AUDIT_S95). Für den ehrlichen Betrieb und für widersprüchliche Angaben greift die Prüfung.

### A19 – zeitliche Abdeckung statt reiner Anzahl — **BEHOBEN**

**Ursprung:** AUDIT_S107 §3 A19. Die Prüfung war count-basiert; gebündelte Prüfungen am Anfang mit späterer Lücke
(Teil-Hang) blieben unerkannt.

**Änderung [Code]:** Für Firewall **und** Listener getrennt werden die Kontrollpunkte `nachher_start`,
`nachher_waehrend`, `nachher_ende` nach Zeit betrachtet:
- Zeitstempel müssen vorhanden, monoton und innerhalb des Fensters (± MAX_KONTROLL_LUECKE_S Toleranz) liegen.
- Mit den **Ankern** Fensteranfang und -ende darf zwischen zwei aufeinanderfolgenden Kontrollen keine Lücke
  > MAX_KONTROLL_LUECKE_S liegen. Dadurch müssen Start **und** Ende abgedeckt sein, und eine späte Lücke wird erkannt;
  gebündelte Kontrollen am Anfang kompensieren sie NICHT.

**Belege:**
- [Nachweis/Unit] `test_bundled_checks_then_trailing_gap_block` (Regression A19: nur erste 45 s abgedeckt → BLOCKIERT).
- [Unit] `test_full_window_coverage_passes`, `test_step_larger_than_max_blocks`,
  `test_end_check_does_not_compensate_missing_interior_checks`, `test_reordered_interior_checks_block`,
  `test_interior_check_without_timestamp_blocks`, `test_checks_outside_the_window_block`,
  `test_listener_gap_blocks_even_if_firewall_is_complete`, `test_short_phase_needs_no_interior_checks`.
- [Sim] `test_evaluation_blocks_long_phase_without_interior_checks` (Ende-zu-Ende: 35-min-Fenster nur mit Start/Ende →
  BLOCKIERT); `test_evaluation_passes_with_compliant_short_phase` (synthetischer Lauf deckt das Fenster ab → PASS).

**Realer Pfad [Code]:** `_fw_kontrolle` (host) und `listener.control_check` tragen Zeitstempel; `_host_phases` erzeugt
Start, periodische (`PeriodicCheck`, alle 15 s) und Ende-Kontrollen. Der Dry-Run setzt jetzt ebenfalls Zeitstempel in
die simulierten Firewallkontrollen.

**Restrisiko:** Die Toleranz von 45 s ist aus dem Intervall abgeleitet. Timestamps sind host-produziert (Modellgrenze).
**Reales Timing NICHT VERIFIZIERT.**

### A20 – Startcheck an run_id + Zeitfenster — **TEILWEISE (untersucht, dokumentiert; keine unsichere Teillösung)**

**Untersuchung [Code]:** `startcheck_frisch` bindet an `run_id` + 12-h-Fenster + „kein fremder mutierender Eingriff
danach“. Die Frage war, ob zusätzlich an den Ist-Zustand zu binden ist.

**Ergebnis:** Der Ist-Zustand zum Operationszeitpunkt ist bereits unabhängig abgesichert:
- `acl_setzen`/`fw_setzen` setzen **nur bei leerer** Ist-Ressource (Nachlesen `before`; nicht leer → Abbruch).
- `acl_entfernen` ist über `aktiver_acl_besitzer` an den Lauf gebunden (A11); fremde/mehrdeutige aktive ACL → BLOCKIERT.
- Ein konkurrierender Lauf hinterlässt Reste → die Leerprüfung blockiert das Setzen; `startcheck` meldet die Reste.

Eine zusätzliche Bindung des Startchecks an einen Zustands-Hash würde den normalen Ablauf (startcheck → fw-setzen →
acl-setzen, alle nach dem Startcheck) nicht sicherer machen, da jede reale Änderung den Zustand ohnehin ändert und die
per-Ressourcen-Leerprüfung der maßgebliche Zustandsnachweis ist. **Eine robuste, nicht den Ablauf brechende
Zustandsbindung ist mit vertretbarem Aufwand nicht möglich**, daher keine Codeänderung — der Punkt bleibt als
Restrisiko offen (Einzel-Owner-Modell laut CLAUDE.md: keine konkurrierenden Operatoren am selben Host).

**Restrisiko:** Zwei Operatoren mit `LEXONO_ISOLATION_ACCEPTANCE=1` am selben Host gleichzeitig sind außerhalb des
Modells. Die 12-h-Frist bleibt eine Heuristik.

## 3 Tests

| Lauf | Ergebnis | Status |
|---|---|---|
| `tests/test_s106_review_fixes.py` (A5/A6/A18/A19, erweitert) | 22 bestanden | **PASS** |
| Isolationssuiten Teil 1 (§86, §87, §90, §92, §102, §104, §105, §106) | 322 bestanden, 8 übersprungen | **PASS** |
| Isolationssuiten Teil 2 (§91, §94, §96, §98) | 198 bestanden, 1 übersprungen | **PASS** |
| Dry-Run / Gesamtablauf (simuliert) | im Teil 2 enthalten (§91) | **PASS** |
| Syntaxprüfung | ok | **PASS** |
| Übrige Benchmark-Tests §77–§85, Gesamtsuite, Lint, reale Abnahme V1–V13 | — | NICHT DURCHGEFÜHRT |

Alle Läufe einzeln im Vordergrund mit Speicherwächter (Start ≥ 4 GB frei, Stopp < 1,5 GB), `PYTHONDONTWRITEBYTECODE=1`;
der Stopp wurde nie ausgelöst. Zwischenfehler (synthetische/Dry-Run-Kontrollen ohne Zeitstempel bzw. ohne Fenster)
wurden korrigiert: synthetische Kontrollen decken das Fenster ab, Dry-Run-Firewallkontrollen tragen Zeitstempel.

## 4 Status A18–A20 und Gesamtbild

| # | Stand nach §108 |
|---|---|
| A18 | **BEHOBEN** (fensterbasiert, dauer_s/Fenster-Konsistenz); Restrisiko: Host-produzierte Werte (Modellgrenze) |
| A19 | **BEHOBEN** (echte Lückenprüfung, Anker Start/Ende); Restrisiko: Host-produzierte Zeitstempel, reales Timing NICHT VERIFIZIERT |
| A20 | **TEILWEISE/OFFEN** – durch per-Ressourcen-Leerprüfung + A11 praktisch abgesichert; keine sichere zusätzliche Zustandsbindung ohne Ablaufbruch; Einzel-Owner-Modell |
| A1–A17 | wie AUDIT_S104–S107 (behoben bzw. als Restrisiko dokumentiert); A3/A13 Timing, A7/A8/A10/A12 reale Semantik weiterhin NICHT VERIFIZIERT |
| Grenzen des Modells | unverändert: Administrator in der VM, kompromittierter Host, Absenderfälschung |

Damit sind A1–A20 behoben oder als ausdrückliches Restrisiko dokumentiert. Keine reale Abnahme fand statt.

## 5 Voraussetzungen (unverändert)

- **Commit** nur als Zwischenstand, **NICHT SICHERHEITSABGENOMMEN**, mit Nennung der NICHT-VERIFIZIERT-Punkte und A20.
- **D1 / reale VM-Abnahme:** identische venv Host/VM; Python systemweit; Isolationsvariablen gesetzt; Zähler-,
  Hyper-V-ACL-, Firewall- und Timing-Semantik real prüfen; Abdeckungs-/Startcheck-Ablauf real durchspielen.
- **Sicherheitsfreigabe:** erst nach realer Abnahme, echter Owner-Signatur (D4) und erneutem unabhängigem Review.

**Keine Sicherheitsfreigabe.** D1, Signierung und C-14 bleiben GESPERRT.
