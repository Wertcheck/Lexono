# Skill: Dokumentenverarbeitung / OCR-Status

## Zweck

Änderungen am Dokument-Upload/-Status-Fluss im Chat oder anderen Modulen.

## Vorgehensweise

1. Bestehende OCR-Status-Zustände verstehen: `done`/`not_needed`
   (verarbeitet), `pending` (wartet auf OCR), `failed`
   (Verarbeitung fehlgeschlagen), `unsupported_format`.
2. Fehlermeldungen IMMER nutzerfreundlich formulieren, NIE Stacktraces,
   volle Dateipfade mit Mandantennamen oder interne Architekturdetails
   nach außen zeigen (Masterprompt §28, Beispieltexte dort).
3. Neue Dateiformate: `allowed_upload_extensions`-Konfiguration prüfen,
   nicht hartkodiert im Template.

## Prüfungen

Keine sensiblen Rohdaten in Fehlermeldungen oder sichtbaren Dateipfaden.

## Relevante Dateien

`app/web/templates/chat.html` (Anhangs-Chips + Statuslogik),
Dokumentverarbeitungs-Service (siehe ARCHITECTURE.md für exakten Pfad)
