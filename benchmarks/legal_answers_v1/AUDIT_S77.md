# §77 Audit des Antwortqualitäts-Benchmarks `legal_answers_v1`

Stand 18.10.2026, Ausgangsstand `c31fb99` (Branch `feature/feedback-diagnose`, synchron mit dem Remote; `main` `5735e5b`).
**Audit und Vorbereitung, kein Lauf.** Es wurden keine Modellantworten abgerufen, keine Cloud-Aufrufe ausgeführt und
keine Kosten verursacht.

Die drei Ebenen sind strikt getrennt:

| Ebene | Stand |
|---|---|
| Technische Validierung (Struktur, Rückführbarkeit auf Akte und Quelle, Sperre, Werkzeuge) | **PASS** (Nachweise unten) |
| Juristische Freigabe (Referenzlösungen, Varianten, Alternativergebnisse, Schwellen) | **NICHT ERTEILT** – offen, siehe J1–J6. Der Agent erteilt sie nicht. |
| Tatsächliche Modellqualität | **NICHT VERIFIZIERT** – keine einzige Modellantwort vorhanden |

## Geprüfte Dateien

- `benchmarks/legal_answers_v1/`:
  - `README.md`;
  - `rubric.json` (v1, ab jetzt v2);
  - `specs/dev/LA-D005.json`, `specs/dev/LA-D006.json`;
  - `specs/test/LA-T004.json`.
- Referenzfälle: `benchmarks/legal_e2e_v1/cases/dev/LE-D005.json`, `…/dev/LE-D006.json`, `…/test/LE-T004.json`, jeweils
  der Block `solution` und `reference`.
- Quellen: `source_register.json` (Q-RECHTSPRECHUNG-IM-INTERNET, Q-GESETZE-IM-INTERNET).
- Werkzeuge:
  - `scripts/legal_bench/answer_specs.py`;
  - `scripts/legal_bench/solution_score.py` (Normalisierung);
  - `tests/test_legal_answer_specs.py`.
- Ausgangs-Hashes vor jeder Änderung: `audit/s77_ausgangshashes_c31fb99.json`.

## Quellen und Testdaten

- **Quellenrechte:** Beide verwendeten Quellen sind im Register `nutzbar` (geprüft 17.10.2026, § 5 Abs. 1 UrhG). Die drei
  Fälle sind `abgeleitet` und tragen ECLI. Übernommen wurden nur Rechtsfrage und Ergebnis aus Leitsatz bzw. Tenor
  (LE-D006 zusätzlich Gründe Rn. 12 f.). Alle Personen, Daten und Beträge sind synthetisch.
- **Nicht verwendet:** OpenRewi, ZJS und Moot Courts, weil ihre Rechte ungeklärt sind. **PASS**
- **Test-Split:** `legal_e2e_v1/cases/test/LE-T001…T004` ist unverändert seit `fc9d3f4` (§71); die kanonischen Hashes
  sind identisch mit dem Ausgangsstand. Jetzt ist er zusätzlich gesperrt (`LOCK.json`). **PASS**
- **Echte Daten:** Es werden keine realen Mandantendaten verwendet; alle Antworttexte in den Tests sind handgeschriebene
  Testeingaben.

## Befunde

**N = nachweisbarer Fehler**, mit einem Nachweis, der sich reproduzieren lässt. **V = Verbesserungsvorschlag oder
bekannte Grenze.** **J = juristische Freigabe nötig.**

| # | Art | Befund | Nachweis | Maßnahme in §77 |
|---|---|---|---|---|
| N1 | Fehler | Die Vorprüfung suchte Begriffe als **Teilwort**: „zu“ (LA-D005 K1) traf „**Zum** Vorkaufsrecht kann ich nichts sagen“; das ergab einen falschen Ergebnis-Hinweis | Probe vor der Änderung; Test `test_non_answer_no_longer_produces_a_result_hint` | Suche als ganzes Wort bzw. ganze Wortfolge; Begriffe unter 3 Zeichen lehnt der Validator ab |
| N2 | Fehler | **Entscheidende Tatsachen der Referenzlösung fehlten** in den Spezifikationen: LA-D005 F3, F5; LA-D006 F2, F3, F4; LA-T004 F3, F5, F7. Antworten ohne diese Tatsachen wären in „Tatsachen“ nicht aufgefallen | `audit/s77_validator_gegen_specs_v1.txt` (gehärteter Validator gegen die §76-Fassung: 25 Befunde) | Kriterien K8 ff. ausschließlich aus vorhandenen Aktenwerten; der Validator verlangt jetzt Abdeckung oder einen begründeten Ausschluss (`not_assessed`) |
| N3 | Fehler | **Offener Punkt O2** hatte in allen drei Spezifikationen kein eigenes Kriterium. Das Unsicherheits-Kriterium vermischte O1 und O2: ein Begriff aus O2 genügte als „gefunden“ für beide | wie N2 | je offenem Punkt ein Kriterium; Varianten müssen einen Begriff genau dieses Punkts enthalten |
| N4 | Fehler | LA-T004: „Die Gerichtsbarkeit ist **nicht gegeben**“ löste keinen Verdacht aus, traf aber die Ergebnisvariante | Probe; Test `test_negated_jurisdiction_raises_suspicion` | G1 um [„Gerichtsbarkeit“, „nicht gegeben“] ergänzt, eine Umschreibung des Referenz-Fehlergebnisses „keine deutsche Gerichtsbarkeit“ (**J2**) |
| N5 | Fehler | **Rubrik ohne Zusammenfassungsregel:** Dass ein kritischer Fehler nicht ausgeglichen werden darf, war nur als Flag hinterlegt und nicht als Regel. Es gab keine Regel für n/a, vertretbare Alternativergebnisse und nicht entscheidbare Punkte | `rubric.json` v1 | Rubrik v2 mit `aggregation`: bestätigter kritischer Fehler ergibt FAIL_KRITISCH; n/a nur begründet; ohne Schwellen nie PASS |
| N6 | Fehler | **Kein Schutz** von Test-Spezifikation und Rubrik vor unbemerkter Änderung. Es gab nur das Flag `frozen`; die Test-Akte war nur indirekt über den Fingerabdruck der Spezifikation geschützt, und beide konnten gemeinsam geändert werden | Struktur §76 | `LOCK.json` (kanonische SHA-256 von Rubrik, Test-Spezifikation und Test-Akten). Neusperre nur mit Begründung. Test `test_lock_detects_a_changed_test_reference` |
| N7 | Fehler | Keine **Änderungshistorie** je Spezifikation; die Versionierung war nicht nachvollziehbar | Struktur §76 | `history` ist Pflicht und muss zur `version` passen |
| V1 | Grenze | Die Vorprüfung bleibt wortbasiert und versteht weder Verneinung noch Satzbezug. Beispiel: LA-T004 K1 [„Klage“, „erheben“] trifft auch „Die Klage ist nicht zu erheben“ | Test `test_negated_result_raises_a_suspicion_although_terms_match` (§76) | Nur Hinweis, nie Note; das Ergebnis bewertet immer eine Person (**J2**) |
| V2 | Vorschlag | Zulässige Antwortvarianten (`accept_any`) und Verdachtsformulierungen sind **Entwürfe des Agenten**. Ob sie vollständig sind, ist unbekannt | – | **J2** |
| V3 | Vorschlag | **Vertretbare Alternativergebnisse** sind nirgends definiert, etwa für LA-T004 „Zulässigkeit hängt von der Tätigkeit ab, Klage vorsorglich erheben“ | – | Feld `alternative_results` ist jetzt Pflicht, Status `juristische_pruefung_offen`. Nichts vom Agenten festgelegt (**J3**) |
| V4 | Grenze | Nur 3 Fälle (2 dev, 1 test). Das trägt keine statistische Aussage | – | dokumentiert |
| V5 | Vorschlag | Die **Referenzlösungen** (`expected_result`, §71) hat der Agent aus Leitsätzen abgeleitet; juristisch geprüft sind sie nicht | – | **J1** |
| V6 | Vorschlag | **Ausführung fehlt bewusst:** Ein späterer Lauf muss über den regulären, pseudonymisierten Lexono-Pfad laufen, mit Payload-Erfassung, Leak-Check und Fail-Closed, und braucht eine eigene Freigabe | `answer_run.py` hat keine Ausführungsfunktion (Test) | Freigabe-Checkliste C-14 |

Weitere Prüfungen ohne Befund:

- Normen und Fristen der Referenz sind vollständig abgedeckt.
- Die Fehlergebnisse der Referenz (`wrong_results`) sind alle als Verdacht erfasst; das prüft der Validator jetzt.
- Quellen sind nur freigegebene; die Fingerabdrücke der Akten sind unverändert.
- In LE-T004 ist F3 („Kündigung zum 30.04.2026“) seit §74 ein Beendigungstermin. Als **Tatsache** ist der Wert korrekt;
  das steht nicht im Widerspruch zur Spezifikation.

## Offene juristische Entscheidungen (keine Freigabe durch den Agenten)

| # | Entscheidung |
|---|---|
| J1 | Freigabe der drei Referenzlösungen (`solution.expected_result`, `wrong_results`, `decisive_facts`, `open_points`) |
| J2 | Freigabe aller Antwortvarianten und Verdachtsformulierungen, einschließlich der §77-Ergänzungen: K8–K12 und die G1-Ergänzung in LA-T004 |
| J3 | Vertretbare Alternativergebnisse je Fall festlegen oder ausdrücklich „keine“ festhalten (`alternative_results_status`) |
| J4 | Gewichte und PASS/FAIL-Schwellen je Dimension (`rubric.aggregation.weights/thresholds`, derzeit `null`) |
| J5 | Qualifikation der bewertenden Personen, Zweitprüfung bei kritischen Fehlern, Vorgehen bei abweichenden Bewertungen |
| J6 | Bestätigung der Regeln zu n/a und Unentscheidbarem (`rubric.aggregation.rule_text`) |

## Änderungen in §77 (alle vor jedem Lauf, keine Ergebnisse vorhanden)

- `rubric.json` v2: Zusammenfassungsregeln; Gewichte und Schwellen ausdrücklich OFFEN.
- Spezifikationen v2 mit Historie; Pflichtfelder `alternative_results`, `alternative_results_status`, `not_assessed`.
- `LOCK.json`: Erstsperre von Rubrik, `specs/test/LA-T004.json` und `legal_e2e_v1/cases/test/LE-T001…T004`.
- `scripts/legal_bench/answer_specs.py`: Wortgrenzen, Mindestlänge, Abdeckungspflicht, getrennte offene Punkte,
  Fehlergebnis-Abgleich, Historie, Rubrikprüfung, Sperre (`--update-lock --reason`).
- `scripts/legal_bench/answer_run.py` (neu, **ohne** Ausführungsfunktion):
  - `check` (Freigabe-Checkliste maschinell);
  - `plan` (Aufrufplan mit Fingerabdrücken);
  - Format des Antwortdatensatzes mit Hash;
  - blinder Prüfbogen mit Referenzbezug;
  - Zusammenfassung nach Rubrik mit Fehlerbericht und Fundstellen.
- `run_config.template.json`, `FREIGABE_CHECKLISTE.md`.

## Testresultate und Nachweise

| Prüfung | Ergebnis | Nachweis |
|---|---|---|
| Validator gegen §76-Fassung (gehärtet) | 25 Befunde (N2, N3, N5–N7 u. a.) | `audit/s77_validator_gegen_specs_v1.txt` |
| Validator gegen §77-Fassung v2 inkl. Sperre | **PASS** („3 Spezifikationen - OK“) | `audit/s77_validator_gegen_specs_v2.txt` |
| Freigabeprüfung der Vorlage | **LAUF NICHT FREIGEGEBEN** (22 offene Punkte, 0 Verstöße), Exit 2, wie erwartet | `audit/s77_check_vorlage.txt` |
| Aufrufplan der Vorlage | 6 Aufrufe (3 Spezifikationen × 2 Aufgaben × 1, Wiederholungen OFFEN), Kosten OFFEN, keine Ausführung | `audit/s77_plan_vorlage.json` |
| Fingerabdrücke der Akten und Spezifikationen | dokumentiert | `audit/s77_fingerprints.txt` |
| Test-Split unverändert gegenüber Ausgangsstand | **PASS** | `audit/s77_ausgangshashes_c31fb99.json`, `LOCK.json` |
| `tests/test_s77_answer_benchmark_audit.py` + `tests/test_legal_answer_specs.py` + Korpus- und Lösungstests | **PASS** (85) | pytest |
| Volle Suite | siehe `PRIVACY_REVIEW_ARCHITECTURE.md` §77 | – |

**Kein Beweis:** Keine Aussage über die juristische Qualität eines Modells, über vollständige juristische Zuverlässigkeit
oder über vollständige Leckfreiheit.
