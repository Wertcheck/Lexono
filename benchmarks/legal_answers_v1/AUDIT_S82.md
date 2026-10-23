# §82 Freigabevorbereitung und Entscheidungsmatrix

Stand 19.10.2026, Ausgangsstand `61dcfa8` (§81, verifiziert: HEAD = Remote), Branch `feature/feedback-diagnose`; `main`
unverändert (`5735e5b`).

**Grenzen dieser Arbeit:**
- **Vorbereitung, keine Entscheidung.** Der Agent hat keine Entscheidung getroffen, keine Freigabe erteilt und keine
  Person als zuständig eingetragen.
- **C-14 bleibt GESPERRT.** Die Endpunktsperre (`RealCloudDisabled`), die Freigabestatus und die Datenschutzprüfungen
  sind unverändert.
- Es gab keine Modell- oder Cloud-Aufrufe, keine Kosten und keinen Zugriff auf die produktive Datenbank.
- Es war eine reine Dokumentations- und Konsistenzprüfung ohne UI- oder E2E-Test, deshalb gibt es **keine
  Screenshots**.

| Prüfschritt | Ergebnis |
|---|---|
| Ausgangsstand verifiziert (`61dcfa8`, Working Tree nur mit den 4 fremden Logo-/UX-Dateien) | **PASS** |
| Entscheidungsmatrix J1–J6, F-1, C-7, C-11 (`ENTSCHEIDUNGSMATRIX.md`, `entscheidungsmatrix.json`) | **PASS** – erstellt, Kennzahlen und Pfade per Test gegen die Dateien geprüft |
| Widerspruchsprüfung Dokumente/Gates/Checklisten | **PASS** – 2 Befunde (W1 korrigiert, W2 dokumentiert, nicht entschieden) |
| Nichtinvasive Validierung (`tests/test_s82_decision_matrix.py`) | **PASS** |
| Juristische Richtigkeit von Referenzen, Varianten, Schwellen | **NICHT VERIFIZIERT** – liegt bei der qualifizierten juristischen Person |
| Zuständige Personen | **NACHWEIS FEHLT** – in keinem Dokument benannt, nur Rollen |
| Volle Suite | **PASS** bis auf die 4 bekannten, fremden Logo-Tests |

## Ergebnis der Matrix

Ausführlich in `ENTSCHEIDUNGSMATRIX.md`; Kurzfassung:

| Punkt | Status | Was fehlt für den Abschluss |
|---|---|---|
| J1 Referenzlösungen | ENTSCHEIDUNG VORBEREITET | juristische Prüfung, benannte Person |
| J2 Antwortvarianten (76) und Verdachtsformulierungen (6 gefährliche Fehler) | ENTSCHEIDUNG VORBEREITET | juristische Prüfung je Kriterium |
| J3 Alternativergebnisse | OFFEN | inhaltliche Festlegung; bewusst keine Vorschläge des Agenten |
| J4 Gewichte und Schwellen | OFFEN | Festlegung; es gibt keine Kalibrierungsdaten |
| J5 Bewertende Personen | NACHWEIS FEHLT | Personen, Qualifikation, Verfahren bei Abweichung |
| J6 Regeln zu n/a und Unentscheidbarem | ENTSCHEIDUNG VORBEREITET | Bestätigung; Zuordnung zu einem Freigabefeld (W2) |
| F-1 Nachweis der Freigaben | ENTSCHEIDUNG VORBEREITET | Verfahren: Signatur, externer Nachweis oder bewusster Verzicht; Vier-Augen-Prinzip |
| C-7 Kosten | NACHWEIS FEHLT | Modell und Version, Preise mit Quelle, Datum, Währung und USt., Zusatzgebühren, Budget, Abgleich |
| C-11 Ablage | ENTSCHEIDUNG VORBEREITET | Ort, Verfahren A–D, Zugriff, Aufbewahrung, Löschung, Sicherung; IT-Zustand **NICHT VERIFIZIERT** |

Die Zuständigkeit ist nur als **Rolle** belegt (`FREIGABE_CHECKLISTE.md`): qualifizierte juristische Person für
C-12, C-13 und C-16; Owner/juristisch für C-15; Owner für C-7 und F-1; Owner/Datenschutz für C-11.

## Widerspruchsprüfung

**Geprüft wurden:**
- `README.md`, `FREIGABE_CHECKLISTE.md`, `AUDIT_S77.md`–`AUDIT_S81.md`;
- `rubric.json` und die drei Spezifikationen;
- `run_config.template.json`, `answer_run.check` bzw. `legal_state`, `answer_driver.assert_local_only`.

| # | Befund | Behandlung |
|---|---|---|
| W1 | `FREIGABE_CHECKLISTE.md` sagte noch „Eine Ausführungsfunktion gibt es bewusst nicht“ (Stand §77). Seit §80 gibt es den Treiber; er lässt nur Mock-Provider bzw. den lokalen Prüfendpunkt zu | **korrigiert**: Satz ersetzt und als §82-Korrektur gekennzeichnet; der Sachstand „echte Endpunkte gesperrt, C-14 GESPERRT“ ist unverändert |
| W2 | J6 (Regeln zu n/a und Unentscheidbarem) steht in `AUDIT_S77.md`, ist aber **keinem** Checklistenpunkt und keinem Freigabefeld zugeordnet; das Gate prüft J6 deshalb nicht | **dokumentiert, nicht entschieden**: Hinweis in der Checkliste und in der Matrix; die Zuordnung ist eine Owner-Entscheidung |

**Ohne Widerspruch:**
- Alle drei Spezifikationen haben den Status `entwurf_juristische_freigabe_offen`.
- In `rubric.json` sind `weights`/`thresholds` `null`, und `thresholds_status` ist „OFFEN“.
- `alternative_results_status` steht dreimal auf `juristische_pruefung_offen`.
- In der Checkliste sind C-1–C-17 (außer C-9, das als fest „true“ gilt) und F-1 OFFEN.
- In der Vorlage sind alle Freigaben `null`, `approved` ist `false`, und `check` lehnt ab.
- `legal_state` meldet die drei Spezifikationen als offen und die Rubrik als offen, und `check` behandelt jede
  eingetragene juristische Freigabe als Widerspruch (§81).
- LA-T004 ist `frozen: true`, wie das README sagt, und LOCK ist gültig.

## Änderungen

| Datei | Änderung |
|---|---|
| `benchmarks/legal_answers_v1/ENTSCHEIDUNGSMATRIX.md` (neu) | Matrix mit Fragen, Nachweisen, Optionen samt Vor- und Nachteilen, Folgen, Risiken, Rollen, Fehlendem und Abschlusskriterien |
| `benchmarks/legal_answers_v1/entscheidungsmatrix.json` (neu) | maschinenlesbare Fassung mit Kennzahlen der Spezifikationen |
| `benchmarks/legal_answers_v1/FREIGABE_CHECKLISTE.md` | W1 korrigiert, Hinweis W2 ergänzt; **kein** Status geändert |
| `benchmarks/legal_answers_v1/README.md` | Verweise auf Matrix und AUDIT_S82 |
| `tests/test_s82_decision_matrix.py` (neu) | nichtinvasive Konsistenzprüfung (siehe unten) |

Nicht geändert wurden: Spezifikationen, Rubrik, LOCK, Vorlage, Gates, Treiber, Endpunktsperre, Datenschutzprüfungen und
App-Code.

## Validierung

`tests/test_s82_decision_matrix.py` liest nur Dateien und ruft reine Funktionen auf. Er schlägt fehl, sobald:
- ein Punkt als erledigt markiert oder eine Person eingetragen wird;
- eine Kennzahl der Matrix nicht mehr zu den Spezifikationen passt (Kriterien, Varianten, gefährliche Fehler,
  Fehlentscheidungen, offene Punkte, Alternativergebnisse);
- ein Nachweispfad fehlt;
- Markdown und JSON beim Status voneinander abweichen;
- die Checkliste einen Punkt nicht mehr als OFFEN führt;
- die Vorlage bzw. das Gate einen Lauf zulässt;
- die Endpunktsperre einen echten Endpunkt zulässt.

Ändert sich eine Entscheidung tatsächlich, muss die Matrix bewusst nachgezogen werden; der Test schließt nichts selbst.

| Test | Ergebnis |
|---|---|
| `tests/test_s82_decision_matrix.py` (neu) | 12 bestanden |
| §78–§82-Benchmarkdateien gemeinsam | 210 bestanden |
| Spezifikations-Validator (`python -m scripts.legal_bench.answer_specs`) | „3 Spezifikationen - OK“ (LOCK gültig) |
| Volle Suite in 6 Teilen | 5626 bestanden; 4 FAILED nur in `tests/test_design_refresh.py` (Logo-Tests, fremde Asset-Änderungen, bekannt) |

## Unsicherheiten

- **Juristische Inhalte:** Ob J1–J3 inhaltlich tragen, ist **NICHT VERIFIZIERT**. Die Matrix beschreibt nur die Fragen
  und Optionen.
- **J4:** Ohne Modellantworten gibt es keine Erfahrungswerte; jede Schwelle wäre a priori.
- **C-11:** Ob BitLocker, EFS oder ein DRA auf diesem Gerät eingerichtet sind, ist **NICHT VERIFIZIERT**; die Prüfung
  bräuchte Administratorrechte und wurde nicht durchgeführt.
- **C-7:** Zusatzgebühren und Wechselkurse sind im Kostenbuch nicht abgebildet; ob das genügt, entscheidet der Owner.
- **Status-Erkennung:** `legal_state` erkennt „offen“ am Status-Text der Spezifikationen („entwurf“ bzw. „offen“). Wie
  ein freigegebener Status heißt, gehört zu J1–J3.
