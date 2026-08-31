# Agent D: Chat / Document Workspace

## Verantwortung

Chat, Unterhaltungsliste, Composer, Datei-Upload, Büroklammer, Drag & Drop,
Spracheingabe, Dokumentstatus, Dokumentansicht, Pseudonymisierungs-
darstellung, Kontextleiste, Mandanten-/Aktenkontext.

## Relevante Architektur

- `app/chat/service.py::ChatService.send_message` → ruft
  `DraftingService.create_draft()` (dieselbe Pipeline wie der
  Schriftsatz-Generator) → lokale KI (optional, Pflicht wenn konfiguriert)
  → Pseudonymisierung/Final Payload Gate → Cloud-KI.
- `app/web/chat_router.py` – Rendering-Kontext für `chat.html`, inkl.
  `local_ai_status`, `provider_configured`.
- `app/web/templates/chat.html` – zentrale Chat-Seite (Landingpage nach
  Login).

## Bestehende Funktionen (nicht neu bauen)

- Vier Quick-Actions im leeren Chat-Zustand (Dokument analysieren,
  Schriftsatz erstellen, Dokument zusammenfassen, Akte öffnen).
- Dokumentstatus-Badges pro Anhang: Verarbeitet / Wartet auf OCR /
  Verarbeitung fehlgeschlagen / Format nicht unterstützt.
- Zwei Statusindikatoren im Chat-Header: Lokale KI (5 Zustände) und
  Cloud-KI (Gateway- oder Dev-Key-bewusst).

## Dokument-Workspace (umgesetzt 01.09., Task #62)

Neue Route `GET /dashboard/chat/{conversation_id}/document/{document_id}`
(`chat_document_view`) + `app/chat/document_preview.py::build_document_preview`
(nutzt `app/privacy/detectors.py::detect_all` + `presidio_ner.py` - exakt
dieselben Detektoren wie der echte Pseudonymisierungslauf, KEINE zweite
Erkennungslogik). Zeigt den extrahierten Dokumenttext mit inline
`<mark class="pii-highlight pii-highlight--{kategorie}">`-Hervorhebungen
plus rechter Kontextleiste ("Erkannte Mandantendaten", gruppiert nach
Kategorie/Wert mit Vorkommen-Zaehler). Unterhaltungsliste wird dabei per
CSS ausgeblendet (`chat-shell--document-view`), ueber einen Zurueck-Pfeil
im Header wieder erreichbar. `ChatService.get_attached_document` erzwingt
Aktenisolation (Dokument muss an eine Nachricht DIESER Konversation
angehaengt sein, sonst 404-Redirect) - 3 Tests in test_chat_service.py, 2
Integrationstests in test_web_chat.py, 6 Tests in test_document_preview.py.

**Bewusst NICHT umgesetzt**: echtes PDF-Seiten-Rendering (Zoom/Print/
Seitennavigation wie im Referenzbild) - bräuchte eine PDF.js-artige
Bibliothek + Pixel-Koordinaten-Mapping der Presidio-Spans. Die aktuelle
Lösung zeigt den EXTRAHIERTEN TEXT (denselben, den die KI tatsächlich
verarbeitet), nicht die pixelgenaue PDF-Optik - liefert dieselbe
Kernfunktion (sehen was als sensibel erkannt wurde), aber nicht die
visuelle PDF-Wiedergabe. Siehe OPEN_ISSUES.md für den Status.

## Sicherheitsregeln

- Keine unpseudonymisierten Mandantendaten in Fehlermeldungen oder
  Dateipfaden in der UI.
- Nur reale erkannte PII-Kategorien anzeigen, niemals synthetisch erfundene.
