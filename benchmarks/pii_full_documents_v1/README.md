# pii_full_documents_v1 – vollständige synthetische Dokumente für die Pseudonymisierung (§43.10)

Nur **synthetische** Dokumente: erfundene Namen, Firmen, Anschriften und Nummern; E-Mail-Domains `.example` oder
`.invalid`. Keine echten Akten, Mandanten- oder Personendaten.

## Korpus

| Datei | Inhalt | SHA-256 |
|---|---|---|
| `cases/test.json` | 24 Dokumente, 572 vorab festgelegte PII-Stellen, 164 Werte, die lesbar bleiben müssen (`keep`) | `176d6f2c384d6aafcd70eaaae08b87755ec0b3741d8b0d38e59828cba7594e1a` |
| `extracted_texts_ocr.json` | Text, den Lexonos echter Pfad (Rendern, Import, Text/OCR mit Tesseract) aus jedem Dokument extrahiert hat | `7a2d4314b956daf134d821d168bbccec1d914d87ab1b52528c36fd0439be9db7` |

**Verfasser:** zwei getrennte, **blinde** Agenten.
- Teil A: Gericht, Rubrum, Schriftsätze, Urteile, Protokolle, Anlagen.
- Teil B: Korrespondenz, Rechnungen, Mahnungen, E-Mail-Ketten, Verträge, Gutachten, Vollmacht, Telefonnotiz.
- Sie hatten keinen Zugriff auf `app/`, `tests/`, `scripts/`, `benchmarks/`, `gateway/` und die Architektur-Dokumente.
- Sie haben die PII-Stellen nach fachlichem Urteil festgelegt, nicht nach Detektorverhalten.
- Die Teile wurden mechanisch zusammengeführt, geprüft (jeder Wert steht wörtlich im Text) und **vor** der ersten Messung
  eingefroren. `tests/test_privacy_full_document_findings.py` prüft den Hash.

**Abdeckung:**
- Formate: 7 PDF, 4 DOCX, 5 TXT, 4 Scan, 4 schlechter Scan.
- 9 Tabellen, 5 Dokumente über 6.000 Zeichen (mehrseitig).
- Kurzrubrum („Beispiel ./. Muster“, „Kranich ./. Fiktiv-Bau GmbH“).
- Briefköpfe, Fußzeilen mit Bankverbindung und Steuernummer, Unterschriftenzeilen.
- Namen ohne Anrede und Namensvarianten („Marie Engel“, „Engel“, „M. Engel“), Firmen mit Nachnamen.

**PII-Kategorien (Anzahl Einträge):** person 197, organisation 67, adresse 64, plz_ort 56, sonstige_kennung 43,
telefon 40, email 28, aktenzeichen 23, steuernummer 13, iban 13, rechnungsnummer 8, kundennummer 8, geburtsdatum 7,
kfz_kennzeichen 5.

**Label-Konventionen:**
- Ein Ort allein ist keine PII, nur mit PLZ (`plz_ort`).
- Gerichte und Behörden sind keine PII.
- Banken und Versicherer zählen als `organisation`.

## Messung

`python -m scripts.legal_bench.pii_measure --out <ergebnis.json> [--texts extracted_texts_ocr.json]`

Echter Pfad bis zum bestehenden `ClaudePrivacyGateway` mit Zweck `second_opinion_document`. Kein Netz.

Je Dokument wird mit und ohne `known_entities` (Mandant aus der Akte) in zwei Sichten gemessen:
- **Versand** (`review_mode="ask"`, wie die Zweitmeinung): Status und Klartext im tatsächlich sendefertigen Text.
- **Erkennung** (`review_mode="mask"`):
  - Klartext im pseudonymisierten Text, je Kategorie.
  - Bei einer Blockade durch den Restscan wird der Pseudonymizer allein gemessen. Das ist eine konservative
    Untergrenze ohne Rückfrage-Maskierung.
  - Von der OCR veränderte Werte werden über die ähnlichste Textstelle gesucht (Ähnlichkeit ≥ 0,8).
  - Teil-Leck: Ein Namensbestandteil einer Person steht weiter als eigenes Wort im Text.
  - `keep`-Werte, die fehlen, zählen als Übermaskierung.

## Ergebnisdateien

- `results_baseline_5608ee6.json`: Stand vor den Detektor-Regeln aus §43.10.
- `results_after_fix.json`: Stand danach. Vergleich und Bewertung siehe `PRIVACY_REVIEW_ARCHITECTURE.md` §43.10.

## Grenzen

- 24 Dokumente, alle von Sprachmodellen verfasst. Echte Kanzleidokumente sind vielfältiger.
- Die Nachmessung lief auf denselben Dokumenten, an denen die Funde entstanden sind. Sie belegt die Behebung dieser
  Funde, nicht deren Verallgemeinerung; dafür braucht es einen frischen blinden Korpus.
- Aus der Messung folgt keine vollständige Leckfreiheit.
