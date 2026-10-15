# pii_full_documents_v2 – schwer erkennbare Kennungen und Namen in vollständigen Dokumenten (§43.11)

Nur **synthetische** Dokumente. Erfundene Namen, Firmen, Anschriften und Nummern; E-Mail-Domains `.example`/`.invalid`.

## Splits

| Datei | Zweck | Dokumente | PII-Stellen | SHA-256 |
|---|---|---|---|---|
| `cases/dev.json` | Entwicklung (darf für Regeln genutzt werden) | 12 | 268 | `ded5418f34c034b3d753d2ed49b27f35d56306e367ce0fce3e4d73e77fd4866d` |
| `cases/test.json` | **blinder Test**, vor jeder Änderung einmal als Baseline und am Ende einmal gemessen | 24 | 606 | `1498fe064e6a9f9042d70ed4ed12f7f248b21f0858cef4dc965ab861f39d581c` |
| `extracted_texts_ocr_dev.json` / `_test.json` | Text aus Lexonos echtem Pfad (Rendern, Import, Text/OCR mit Tesseract) | – | – | `1c0fe60e…` / `c756cb0f…` |

**Verfasser und Blindheit:**
- Drei getrennte, blinde Agenten: Dev, Test A (Gericht und Behörden), Test B (Korrespondenz und Verträge).
- Kein Zugriff auf `app/`, `tests/`, `scripts/`, `benchmarks/`, `gateway/` und die Architektur-Dokumente.
- Die Soll-Markierungen wurden **vor** der ersten Messung festgelegt.
- Beide Splits wurden mechanisch geprüft und eingefroren; `tests/test_privacy_full_document_findings.py` prüft die
  Hashes und dass die Splits sich nicht überschneiden.

**Schwerpunkte:**
- Aktenzeichen und Kennungen ohne Kennwort (Betreff, Klammer, Tabelle);
- Straßen ohne Straßenwort („Am Weiher 11“, „Hoher Weg 8“) mit Zusätzen („Hinterhaus“, „2. OG rechts“);
- Geburtsnamen („geb. Winter“);
- Einwort-Nachnamen, die gewöhnliche Wörter sind, im Rubrum, in Anwesenheitslisten und Unterschriften;
- Kopf- und Fußzeilen, Tabellen, 8 Scans (davon 4 schlecht) und 4 Texte mit OCR-Verwechslungen („2O26“, „4l/26“).

**Neues Feld `keep_gewoehnlich`:** Wendungen, in denen ein Namenswort in gewöhnlicher Bedeutung vorkommt („im
Winter 2025“, „der Koch des Restaurants“). Fehlt eine solche Wendung nach der Pseudonymisierung, ist das
Übermaskierung.

## Messung

- `python -m scripts.legal_bench.pii_measure --cases cases/<split>.json --texts extracted_texts_ocr_<split>.json --out …`
  Erkennung je Kategorie, Klartext im Versand, Übermaskierung.
- `python -m scripts.legal_bench.review_strategy --cases … --texts … --out …`
  Rückfrage- und Maskierungsstrategien (Status quo, A, A mit Fehlbedienung, Hybrid H, B), siehe §43.11.

**Ergebnisdateien:**
- `results_*_ffe22a6.json` / `strategy_*_ffe22a6.json`: Code vor §43.11.
- `results_*_final.json` / `strategy_*_final.json`: Code nach §43.11.

**Grenze der Erkennungssicht:** Ein Dokument, das der Restscan blockiert, hat keine Payload. Gemessen wird dann der
Pseudonymizer **allein**, ohne die Rückfrage-Maskierung, also eine konservative Untergrenze. Wechselt ein Dokument durch
eine Änderung von „frei“ zu „blockiert“, können dort Kennungen als „verbleibend“ erscheinen, die vorher die
Rückfrage-Maskierung erfasst hat; gesendet wird in diesem Fall nichts (§43.11, PII2-TA01).
