# Juristische Ergebnisqualität echter Modellantworten (`legal_answers_v1`) – Konzept

Stand 18.10.2026 (§76, auditiert und erweitert in §77 – siehe `AUDIT_S77.md`). **Konzept, kein Lauf.** Es wurden keine Modellantworten abgerufen, keine Cloud-Aufrufe
ausgeführt und keine Kosten verursacht. Über die juristische Zuverlässigkeit von Lexono oder einem Modell sagt dieser
Benchmark derzeit **nichts** aus.

## Zweck und Abgrenzung

`benchmarks/legal_e2e_v1` misst, ob Lexono die **Grundlage** einer richtigen Antwort liefert: Extraktion, Fristen,
geschützte Inhalte und die Lösungsgrundlage im gesendeten Payload. Ob eine **Antwort** juristisch richtig ist, misst er
nicht.

Dieser separate Benchmark soll später echte Antworten auf dieselben Akten bewerten. Er nutzt eigene Spezifikationen
und eine eigene Rubrik, getrennt vom Fristen- und Datenschutz-Benchmark.

## Quellen und Nutzungsrechte

- Grundlage sind nur die drei **quellenbasierten Fälle** aus `legal_e2e_v1` mit Referenzlösung:
  - LE-D005: BGH VIII ZR 18/24;
  - LE-D006: BAG 6 AZR 32/24;
  - LE-T004: BAG 2 AZR 72/24.
- Verwendet werden ausschließlich die im Quellenregister freigegebenen Quellen (`nutzbar`, §65):
  - Rechtsprechung im Internet: amtliche Leitsätze, Tenor, angegebene Gründe; § 5 Abs. 1 UrhG;
  - Gesetze im Internet: Normtexte.
- Personen, Daten und Beträge sind synthetisch.
- **Nicht verwendet:** OpenRewi (CC BY-SA 4.0, ShareAlike – Entscheidung des Owners offen), ZJS (© ohne Lizenz),
  Moot Courts (Rechte ungeklärt). Fremde Falllösungen wurden nicht übernommen.
- Jede Spezifikation nennt `source_refs` und `source_scope`. Der Validator lässt nur Quellen des Falls mit Status
  `nutzbar` zu.

## Bestandteile

| Datei | Inhalt |
|---|---|
| `rubric.json` | sieben Dimensionen, Skala 0/1/2/n.a., Bewertungsart je Dimension, Regeln |
| `specs/dev/LA-D005.json`, `specs/dev/LA-D006.json` | Entwicklungs-Spezifikationen |
| `specs/test/LA-T004.json` | Test-Spezifikation, **eingefroren** (`frozen: true`) |
| `scripts/legal_bench/answer_specs.py` | Validator, Fingerabdruck, Vorprüfung |
| `tests/test_legal_answer_specs.py` | Regressionstests (Validator, Einfrieren, Fingerabdruck, Vorprüfung) |
| `AUDIT_S78.md` (§78) | Adversariale Prüfung von Freigabesperre, Sperre, Aufrufplan und Datenschutzpfad bis zur HTTP-Leitung |
| `AUDIT_S79.md` (§79) | B1 behoben (geordnete Blockierung bei Ausfall der Namenserkennung, App-Test), Ausführungswächter und offene Lücken E1–E5 |
| `scripts/legal_bench/answer_exec.py` (§79) | Wächter vor **jedem** späteren Modellaufruf (Freigabe, Commit, Lock, Plan, Limits, Siegel, Leak-Check) – **kein Lauf**, keine Kommandozeile, kein eigener Provider |
| `AUDIT_S80.md`, `scripts/legal_bench/answer_driver.py`, `answer_store.py` (§80) | Treiber je geplantem Aufruf über den regulären Lexono-Pfad, sichere Ablage außerhalb des Repositorys, Kostenbuch (geschätzt/tatsächlich), Wiederanlauf – nur Mock/lokaler Recorder, echter Endpunkt gesperrt |
| `AUDIT_S81.md` (§81) | Härtung von Freigaben (Planbindung, Datum, Widersprüche), Kosten (endliche Werte, plausible Usage, Aufrunden), Ablage (IDs, ACL-Prüfung) und Klärung unklarer Aufrufe; Entscheidungsvorlagen C-7, C-11, C-14, F-1 |
| `ENTSCHEIDUNGSMATRIX.md`, `entscheidungsmatrix.json`, `AUDIT_S82.md` (§82) | Entscheidungsvorbereitung J1–J6, F-1, C-7, C-11 (Fragen, Nachweise, Optionen, Rollen, Fehlendes) – **keine Entscheidung**, C-14 GESPERRT; Konsistenztest `tests/test_s82_decision_matrix.py` |
| `LOCK.json` (§77, §78) | Sperre von Rubrik, Test-Spezifikation, Test-Akten und der von ihnen genutzten Quellen-Registereinträge (kanonische SHA-256, `entries_sha256` gegen Handbearbeitung); Neusperre nur mit `--update-lock --reason` (≥ 20 Zeichen), Historie mit alten/neuen Hashes |
| `scripts/legal_bench/answer_run.py` (§77, §78 gehärtet) | Laufvorbereitung **ohne Ausführung**: `check` (offene Freigaben; jedes Feld der Vorlage Pflicht, Werte regelgeprüft, Exit 2 bei Ablehnung), `plan` (Aufrufplan mit Fingerabdrücken), Antwortdatensatz, blinder Prüfbogen, Zusammenfassung nach Rubrik |
| `run_config.template.json` (§77) | Vorlage einer Laufkonfiguration, alle Entscheidungen `null` (OFFEN) |
| `FREIGABE_CHECKLISTE.md`, `AUDIT_S77.md`, `audit/` (§77) | Freigabe-Checkliste, Auditbefunde, Nachweise |

Eine Spezifikation legt **vor jeder späteren Auswertung** fest:

- **Aufgaben:** die Analyse-Aufgaben des Falls, die bewertet werden.
- **Kriterien** je Dimension:
  - Ergebnis;
  - Rechtsgrundlagen (`norm`, muss in der Referenzlösung und im Aktentext stehen);
  - entscheidende Tatsachen (`fact`, mindestens eine Variante entspricht wörtlich einem Aktenwert);
  - Fristen (`deadline`, muss in der Referenz stehen);
  - Unsicherheiten (`open_point`).
- **Zulässige Antwortvarianten** (`accept_any`): Umschreibungen und Schreibweisen, vorab festgelegt.
- **Gefährliche Fehler** (`dangerous_errors`) mit Verdachtsformulierungen und **typische Fehlentscheidungen**
  (`known_wrong_decisions`).
- **Was eine qualifizierte juristische Prüfung braucht** (`requires_qualified_review`).

## Bewertung: getrennte Dimensionen, keine Wortlautnote

| Dimension | Frage | Bewertung |
|---|---|---|
| Ergebnis | Trifft die Antwort die Rechtslage laut Referenz? | **juristische Prüfung** |
| Rechtsgrundlagen | Tragende Normen und Entscheidung benannt **und richtig angewendet**? | teilautomatisch: Vorprüfung, Note durch die Person |
| Tatsachen | Entscheidende Tatsachen vollständig und unverfälscht? | teilautomatisch |
| Fristen | Maßgebliche Fristen mit Vorsorge (früheres Fristende)? | teilautomatisch; Fristberechnung juristisch |
| Unsicherheiten | Streitige, fehlende oder widersprüchliche Angaben benannt? | teilautomatisch |
| Unbelegte Behauptungen | Fundstellen, Tatsachen oder Zahlen, die weder in Akte noch Quelle stehen? | **juristische Prüfung** (Vorprüfung listet Kandidaten) |
| Gefährliche Fehler | Fehler, der bei Befolgen einen Rechtsverlust auslösen kann? | **juristische Prüfung**, **disqualifizierend** |

Die automatische **Vorprüfung** (`precheck`) liefert nur Hinweise:

- „hinweis_gefunden“ oder „nicht_gefunden“ je Kriterium;
- „verdacht“ je gefährlichem Fehler;
- eine Liste von Fundstellen-Kandidaten, die weder in Akte noch in Quelle stehen.

Sie vergibt **keine Note und kein Urteil**. Ein Test belegt die Grenze: „Dem Mieter steht das Vorkaufsrecht **nicht**
zu“ trifft die Ergebnisvariante und zugleich den Verdacht G1. Über das Ergebnis entscheidet deshalb immer eine
qualifizierte Person.

## Test-Split, Versionierung, Reproduzierbarkeit

- **Test-Split:** LA-T004 ist eingefroren. Varianten, Fehler und Schwellen werden nach dem ersten Lauf nicht mehr
  geändert. Abstimmung erfolgt nur an LA-D005 und LA-D006.
- **Referenzversion:** Jede Spezifikation trägt den `case_fingerprint`, einen SHA-256 über den kanonischen JSON-Inhalt
  des Falls. Ändert sich die Akte oder Referenz, lehnt der Validator ab („Referenz geändert“). Dann wird die
  Spezifikation geprüft und ihre `version` erhöht. Dazu kommen `rubric_version` und `created`.
- **Für einen späteren Lauf festzuhalten:**
  - Modell und Modellversion;
  - Parameter (Temperatur, max_tokens);
  - Prompt-Version und Lexono-Commit;
  - Payload-Hash, Zeitstempel;
  - mehrere Wiederholungen je Aufgabe;
  - Rohantworten unverändert und lokal abgelegt;
  - Bewertungsbögen mit Person, Datum und Rubrik-Version.
- **Voraussetzungen für einen Lauf (offen, Owner):**
  - ausdrückliche Freigabe und Kostenrahmen;
  - Versand nur pseudonymisiert über den regulären Datenschutzpfad (keine Abschwächung von Leak-Checks und
    Fail-Closed);
  - keine echten Mandantendaten;
  - mindestens eine qualifizierte juristische Person für die Bewertung, bei kritischen Dimensionen besser zwei
    (Übereinstimmung dokumentieren);
  - blinde Bewertung ohne Kenntnis des Modells.

Aufruf:

```
python -m scripts.legal_bench.answer_specs                # Spezifikationen validieren
python -m scripts.legal_bench.answer_specs --fingerprints  # Fingerabdrücke der Fälle ausgeben
python -m pytest tests/test_legal_answer_specs.py
```

## Grenzen der Aussagekraft

- **Umfang:** Nur drei Fälle (2 dev, 1 test). Das ist keine statistische Aussage über Modelle oder Rechtsgebiete.
- **Herkunft:** Rechtsfrage und Ergebnis stammen aus amtlichen Leitsätzen. Die Akten sind synthetisch und kürzer und
  eindeutiger als reale Akten. Reale Kanzleitexte: **NICHT VERIFIZIERT**.
- **Abhängigkeit:** Die Bewertung hängt von qualifizierten Personen ab; die Vorprüfung ist nur eine Hilfe.
- **Freigabe:** Die Spezifikationen haben den Status `entwurf_juristische_freigabe_offen`. Varianten, Fehler und
  Fehlentscheidungen sind von einer qualifizierten juristischen Person zu prüfen und freizugeben, bevor ein Lauf
  stattfindet.
