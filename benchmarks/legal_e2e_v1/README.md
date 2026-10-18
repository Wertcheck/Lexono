# Realistic Legal Benchmark & E2E (`legal_e2e_v1`)

Versionierter Korpus synthetischer Akten plus E2E-Runner über die echten Lexono-Pfade. Ergebnisse und Bewertung:
`PRIVACY_REVIEW_ARCHITECTURE.md` §34.

## Inhalt

| Pfad | Zweck |
|---|---|
| `cases/dev/LE-D*.json` | Entwicklungsfälle – dürfen zur Abstimmung von Regeln genutzt werden |
| `cases/test/LE-T*.json` | unabhängige Testfälle – **nicht** zur Regelabstimmung verwenden |
| `source_register.json` | Register öffentlicher Quellen (Praxisfälle, Moot Courts, Entscheidungen) mit Herkunft, URL, Art, Umfang, Nutzungsrechten |
| `results.json` | letzter Lauf mit Herkunft (Commit, Korpus-Hash, Code-Fingerabdrücke) |

Ein Fall enthält Parteien, Akte, Dokumente (Format `txt`/`docx`/`pdf`/`scan`; der Text ist die Quelle der Wahrheit, die
Dateien werden zur Laufzeit gerendert, Scans als reine Bild-PDFs) und Referenzdaten: Fakten, Chronologie, Fristen,
Zitate, Widersprüche, Datenschutzanforderungen (`protect`/`keep`) und Aufgaben (`chat`, `schriftsatz`, `suche`,
`fristen`).

Fallversion 2 (14.10.2026): echte Umlaute statt „ae/oe/ue/ss“ – realistischer, und v1 erhöhte die Fehlalarme der
Namenserkennung (§34.6).

## Regeln (vom Validator erzwungen)

- Synthetische Fälle (`synthetic: true`, keine `source_refs`) oder **abgeleitete** Fälle (§71: `synthetic: false`,
  `derivation: "abgeleitet"`). Ein abgeleiteter Fall braucht für jede Quelle einen Registereintrag mit Status `nutzbar`,
  Prüfdatum und Begründung – die Einstufung trifft eine verantwortliche Person, nicht der Agent. Freigegeben sind
  derzeit nur „Gesetze im Internet“ und „Rechtsprechung im Internet“ (§65). Alle anderen Einträge, darunter OpenRewi
  (CC BY-SA 4.0, ShareAlike), ZJS (© ohne Lizenz) und Moot Courts, sind `nicht_geprueft` und unbenutzt.
- Abgeleitete Fälle übernehmen aus der Entscheidung nur die Rechtsfrage und das Ergebnis aus dem amtlichen Leitsatz.
  Personen, Firmen, Daten, Beträge und Dokumente sind vollständig erfunden. Es gibt keine echten Mandantendaten und
  keine Sachverhaltsteile der Originalparteien.
- Referenzlösung (`solution`, nur in abgeleiteten Fällen): Die Referenzentscheidung steht als belegtes Zitat mit
  passender ECLI im Fall. `result_terms` stehen im erwarteten Ergebnis. `decisive_facts` sind existierende Fakten-IDs.
  `required_norms` und die Begriffe der `open_points` stehen wörtlich in den Akten, `deadlines` in den Referenzfristen.
  Eine Analyse-Aufgabe ist Pflicht.
- Jeder Referenzwert steht wörtlich im angegebenen Dokument (keine erfundenen Werte, Fristen oder Fundstellen).
- Zitate außer Normen brauchen eine belegte Quelle; der Korpus enthält derzeit nur reale Normen.
- Jede geschützte Partei steht in `privacy.protect` und in mindestens einem Dokument.
- Widersprüche: mindestens zwei Dokumente mit unterschiedlichen Werten.
- Split = Ordner, ID-Präfix `LE-D`/`LE-T`; Personennamen sind fallübergreifend eindeutig (Mandantentrennung messbar).

## Aufruf

```
python -m scripts.legal_bench.corpus                  # Korpus validieren
python -m scripts.legal_bench.e2e [--split dev|test] [--out PFAD]
python -m pytest tests/test_legal_benchmark_corpus.py tests/test_legal_benchmark_solutions.py
```

### Bewertung gegen die Referenzlösung (§71, `scripts/legal_bench/solution_score.py`)

Die Kriterien werden getrennt bewertet, ohne Textähnlichkeit:

| Kriterium | bestanden, wenn |
|---|---|
| `ergebnis` | alle `result_terms` vorkommen und kein `wrong_results`-Satz (z. B. „Ein Vorkaufsrecht besteht nicht“) |
| `normen` | jede `required_norms`-Norm genannt (Gedankenstrich-/Leerzeichen-normalisiert) |
| `fakten` | jeder Wert der entscheidenden Fakten genannt |
| `fristen` | jede Lösungsfrist genannt |
| `offene_punkte` | je offenem Punkt mindestens ein Begriff aus `any_terms` |

`score_answer(case, antwort)` bewertet eine Antwort. `payload_basis(case, payload)` prüft, ob der tatsächlich gesendete
Payload die Grundlage der Referenzlösung enthält. Das ist eine Voraussetzung für eine richtige Antwort, aber kein
Nachweis dafür. Die Prüfung arbeitet mit exakten Werten: Andere Beugungen („denselben Gesellschaftern“ statt
„dieselben Gesellschafter“) zählen als fehlend.

Die Freigabe-Probe (Modus C) prüft, ob ein auffälliges Dokument im Zustand „Entscheidung erforderlich“ im Chat-Payload
fehlt und ein freigegebenes darin steht.

### Lauf in der installierten App

Der Lauf wird über die echte Oberfläche ausgeführt: Mandant, Akte und Beteiligte über die Formulare, Dokumente über den
Upload, die Freigabe per Klick, Chat und Schriftsatz-Generator. Der Lauf nutzt das QA-Skript außerhalb des Repos und eine
isolierte Datenkopie mit lokalem Aufzeichnungsserver und Testschlüssel. Methode und Ergebnisse stehen in
`PRIVACY_REVIEW_ARCHITECTURE.md` §71.

Der Runner arbeitet offline (`HF_HUB_OFFLINE=1`, lokales Embedding-Modell, vendored Tesseract) und sendet nichts: der
Schreib-Provider erfasst den Payload unmittelbar vor der Cloud-Grenze. Kritische Befunde (Klartext im Payload, Daten
anderer Akten, Suchtreffer fremder Akten, Platzhalterreste im Export, persönliche Daten als Frist) stehen einzeln unter
`kritisch` und gehen in keinen Durchschnitt ein. Zusätzlich gemessen: Erhalt von `keep`-Werten, Chronologie, Normen
und beiden Seiten jedes Widerspruchs im Schriftsatz-Payload sowie maskierte Originale, die kein Referenz-Schutzwert
sind (Fehlalarm-Kandidaten, aus den Gateway-Zuordnungen).

Weitere Proben (je Lauf, getrennt ausgewiesen):

| Probe | misst | kritisch, wenn |
|---|---|---|
| Quellenintegrität (`InventingWriter`) | ob erfundene Fundstellen einer simulierten Modellantwort im Entwurf als ungeprüft markiert werden | – (Befund §34.8, Owner-Entscheidung) |
| Aktenzuordnung (`MatterAssignmentService`, Produktionsschwellen) | Korpus-E-Mails, „Az.“/„Ihr Zeichen“, Konflikt Absender A ↔ Aktenzeichen B | eine Nachricht automatisch einer fremden Akte zugeordnet wird |
| fremder Chat-Anhang | Dokument der Akte B im Chat der Akte A | ein Wert der Akte B im Payload steht |
| schlechter Scan (`render_degraded_scan`) | Referenzwerte im OCR-Text bei 110 dpi, Schräglage, Rauschen, JPEG | – (Qualitätsmaß) |
| Laufzeit / Payload-Größe | Median/Maximum je Stufe, Zeichen je Aufgabenart | – (Kosten NICHT VERIFIZIERT) | Antwortqualität eines echten Modells, Kosten und der installierte
Build werden hier **nicht** gemessen (NICHT VERIFIZIERT).
