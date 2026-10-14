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

- Nur synthetische Fälle (`synthetic: true`, keine `source_refs`). Ein Fall aus einer öffentlichen Quelle braucht einen
  Registereintrag mit Status `nutzbar`, Prüfdatum und Begründung – die Einstufung trifft eine verantwortliche Person,
  nicht der Agent. Derzeit sind alle Registereinträge `nicht_geprueft` und unbenutzt (BLOCKIERT).
- Jeder Referenzwert steht wörtlich im angegebenen Dokument (keine erfundenen Werte, Fristen oder Fundstellen).
- Zitate außer Normen brauchen eine belegte Quelle; der Korpus enthält derzeit nur reale Normen.
- Jede geschützte Partei steht in `privacy.protect` und in mindestens einem Dokument.
- Widersprüche: mindestens zwei Dokumente mit unterschiedlichen Werten.
- Split = Ordner, ID-Präfix `LE-D`/`LE-T`; Personennamen sind fallübergreifend eindeutig (Mandantentrennung messbar).

## Aufruf

```
python -m scripts.legal_bench.corpus                  # Korpus validieren
python -m scripts.legal_bench.e2e [--split dev|test] [--out PFAD]
```

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
