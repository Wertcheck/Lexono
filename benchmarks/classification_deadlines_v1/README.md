# classification_deadlines_v1 – Korpus für Dokumentklassifikation und Fristerkennung (P2.2)

Ausschließlich **synthetische** Dokumente: erfundene Namen, Behörden und Aktenzeichen, Adressen unter `.example` bzw.
`.invalid`. Keine echten Mandanten-, Akten-, E-Mail- oder Falldaten.

## Splits

| Split | Datei | Dokumente | Verfasst von | SHA-256 (Version 1) |
|---|---|---|---|---|
| Entwicklung | `cases/dev.json` | 34 | Entwicklungssitzung; enthält die Reproduktionen bekannter Fehler | `92d6ec799bcd372d3a13a290527e420317d525016c60e90b8659c0a2f06138b0` |
| Unabhängiger Test | `cases/test.json` | 33 | eigener Agent, **blind**: ohne Einblick in Klassifikator, Extraktor, deren Tests, Dev-Split oder Architektur-Doku | `a2cb80158685d1db22d34a4ed0706e99da4bcd83de1e114dfdcd863ed54fe9f2` |

- **Testsplit eingefroren:** Er wurde vor jeder Codeänderung eingefroren und auf dem unveränderten Code `e2b3176`
  gemessen. Danach wurde er nur noch einmal am Ende gemessen; an ihm wurde nicht optimiert.
- **Hash-Prüfung:** `tests/test_classification_deadlines_corpus.py` prüft den Hash. Eine Änderung ist nur als neue,
  begründete Version zulässig (z. B. `test_v2.json`); die Ergebnisse von Version 1 bleiben erhalten.
- **Einschränkung der Blindheit:** Die Fallgruppen des Testsplits (z. B. „gültig bis“, „Kündigungsandrohung“,
  „Schlussrechnung erwähnt“) hat die Entwicklungssitzung im Auftrag an den Agenten vorgegeben. Den Text und die Labels
  hat der Agent allein verfasst. Die Entwicklungssitzung kannte danach nur die Tag-Namen und Zählungen aus seinem Bericht,
  bis zur Schlussmessung.
- **Herkunft der Dev-Reproduktionen:** Fehler aus dem früheren Legal-E2E-Testsplit (Unfallmitteilung, Urkunde,
  Eichschein) sind im Dev-Split mit **neuen** Texten nachgebaut. Das Feld `herkunft` nennt die Quelle.

## Labels je Dokument

- **`label.typ`:** Haupttyp aus der festen Lexono-Typliste. `label.zulaessig` enthält zusätzlich echte Alternativen.
  `label.art` ist `kern` (klarer Inhaltstyp) oder `rolle` (Brief, E-Mail oder Notiz ohne eigenen Typ, meist
  „Unbekannt“).
- **`fristen.frist`:** ausdrücklich genannte Fristenden und Fälligkeiten.
- **`fristen.termin`:** Termine.
- **`fristen.fristbeginn`:** Datum eines fristauslösenden Ereignisses (Zugang, Zustellung, Kenntnis, Bekanntgabe).
- **`fristen.fristdauern`:** Anzahl relativer Fristen ohne Enddatum.
- **`fristen.keine_frist`:** Daten, die **keine** Frist sind: Brief-, Ereignis-, Gültigkeits- und Geburtsdaten.
- **`fristen.nicht_bewertet`:** fachlich strittige Daten; sie gehen nicht in die Bewertung ein.
- **`format`:** `txt`, `docx`, `pdf`, `scan` oder `scan_degraded`. Gerendert wird mit `scripts/legal_bench/render.py`;
  `scan_degraded` ist ein schlechter Büroscan mit fester Zufallszahl.

## Messung und Bewertung

- **Lexono:** `python -m scripts.legal_bench.cd_measure --split dev|test|alle --out …`. Echter Verarbeitungspfad:
  Import, Text/OCR, Klassifikation, Fristenanalyse. Rein lokal.
- **Modellvergleich:** `python -m scripts.legal_bench.cloud_compare --split … --texts … --out …`. Siehe
  `PRIVACY_REVIEW_ARCHITECTURE.md` §41.
- **Gemeinsame Auswertung:** `python -m scripts.legal_bench.cd_compare_report --out vergleich.json`.
- **Bewertungsregeln, für alle Quellen identisch (`cd_measure.score`):**
  - Ein Typ ist richtig, wenn er in `zulaessig` steht.
  - Fristdaten werden je Art verglichen: richtig, übersehen, falsch. „Falsch“ wird unterschieden nach „andere Art“,
    „keine Frist als …“ und „ungeklärt“.
  - **Kritisch sind:** jede übersehene Frist und jede als Frist vorgeschlagene Nicht-Frist.
  - Ein nicht gesendetes Dokument (fail-closed) zählt als „Unbekannt“ ohne Daten.

## Ergebnisdateien

- **Baselines vor P2.2:**
  - `baseline_legal_e2e_e2b3176.json` (Legal-E2E-Korpus);
  - `results_lexono_dev_e2b3176.json`, `results_lexono_test_e2b3176.json`.
- **Historie (§40):**
  - `history_legal_e2e_4d7981c_before_p21.json`;
  - `history_legal_e2e_p21_final.json`.
- **Nach P2.2:** `results_lexono_p22.json`.
- **Modellvergleich:** `results_cloud_dev.json`, `results_cloud_test.json`, `vergleich.json`. Sie enthalten die
  pseudonymisierten Vorhersagen und die Token-Zählungen, **keine** Schlüssel.

## Grenzen der Aussagekraft

- **Kleine Fallzahlen:** 33 bzw. 34 Dokumente und 17–18 Fristen je Split. Unterschiede von einem bis zwei Dokumenten
  liegen im Rauschen.
- **Gleiche Urheberschaft:** Alle Texte stammen von Sprachmodellen derselben Familie. Echte Kanzleidokumente sind
  vielfältiger.
- **Labelkonventionen:** Sie weichen zwischen Dev und Test in Einzelfällen ab. Beispiele: Zahlungsaufforderung mit
  Kündigungsandrohung (Dev „Unbekannt“, Test „Mahnung“) und Wiedervorlage (Dev „Frist“, Test „nicht bewertet“).
  Dokumentiert, nicht nachträglich angeglichen.
- Aus diesen Messungen folgt weder vollständige Fehlerfreiheit noch Leckfreiheit.
