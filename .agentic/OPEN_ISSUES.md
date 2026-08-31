# OPEN_ISSUES – Technische Schulden & offene Workstreams

Kategorien: CRITICAL / HIGH / MEDIUM / LOW / FUTURE.
Kein Eintrag hier bedeutet automatisch Untätigkeit – Einträge werden aktiv
von den zuständigen Agenten (siehe `agents/`) abgearbeitet oder bewusst
zurückgestellt (mit Begründung).

## CRITICAL

_Keine offenen CRITICAL-Punkte zum Stand 31.08._

## HIGH

- **Vollständiger Dokument-Workspace mit Pseudonymisierungs-Highlighting**
  (Referenzbild 2 aus Masterprompt V2): Chat zeigt bei Dokumenten aktuell
  nur Anhangs-Chips mit OCR-Status (`chat.html`), keine inline
  Dokumentansicht mit farblich hervorgehobenen erkannten PII-Kategorien und
  keine rechte Kontextleiste mit „Erkannte Mandantendaten“. Erfordert:
  PDF-Rendering im Frontend, Koordinaten-/Span-Mapping der
  Presidio-Erkennungen auf die Dokumentdarstellung, neue
  Router-/Template-Struktur für die Dokumentansicht. Größtes noch fehlendes
  UX-Feature aus dem Masterprompt. Zuständig: Agent D (Chat/Document
  Workspace) + Agent B (Frontend).
  Status: NV (nicht begonnen).

- **Model Evaluation Engine über mehrere Runtimes/Modelle** (Masterprompt
  V2 §15–17): Bisher nur Ollama + 3 Qwen/Llama-Modellvarianten real
  benchmarkt (siehe MODEL_EVALUATION.md). Llama.cpp und weitere
  Modellfamilien (Mistral, Gemma) noch nicht evaluiert; keine
  wiederverwendbare Scoring-Engine mit repräsentativen
  Kanzlei-Testaufgaben vorhanden. Zuständig: Agent F (Local AI/Model
  Engineer).
  Status: NV.

## MEDIUM

- **Fenster-Chrome (natives WinForms-Fenster vs. frameless)**: siehe
  DECISIONS.md. Reference-Screenshots zeigen ein Fenster ohne native
  Titelleiste (nur minimalistische −/✕-Icons oben rechts). Aktuell nutzt
  `run.py` ein natives `webview.create_window(...)`-Fenster mit
  OS-Titelleiste. Änderung ist möglich, aber risikobehaftet (Resize-
  Verhalten, Custom-Drag-Region über die JS-Bridge). Erfordert explizite
  Priorisierungsentscheidung, da sie die stabilisierte Desktop-Shell
  anfasst. Zuständig: Agent B (Frontend) + Agent K (Build/Release).
  Status: OE (Entscheidung aussteht).

- **Statusindikatoren (Lokale KI/Cloud-KI) global statt nur im Chat-Header**:
  Referenzbild 1 zeigt die Statusanzeigen in der linken App-Sidebar
  (`base.html`), nicht im Chat-Panel-Header. Aktuell wird `local_ai_status`
  nur in `chat_router.py` berechnet; eine Verlagerung in die globale
  Sidebar würde einen gemeinsamen Kontext-Provider für alle ~20 Router
  erfordern (aktuell rendert jeder Router seinen `TemplateResponse`-Kontext
  einzeln, kein zentraler Context-Injector). Bewusst nicht im selben
  Durchgang wie die Textänderungen umgesetzt, da es ein breiter,
  cross-cutting Eingriff wäre. Zuständig: Agent E (Backend) + Agent B.
  Status: NV.

- **Visual QA Loop (§23)**: Noch kein echter Screenshot-Vergleichslauf
  gegen die bereitgestellten Referenzbilder in dieser Session durchgeführt
  (kein Browser-Tool in dieser Umgebung aktiv verfügbar). Zuständig: Agent
  J (Visual QA).
  Status: NV, siehe VISUAL_QA.md.

## LOW

- App.css enthält einen Kommentar mit „KanzleiAI“ (Zeile ~2695,
  `.chat-panel__header` Kommentarblock) – rein interner Kommentar, keine
  sichtbare UI, niedrige Priorität für Bereinigung.

## FUTURE (explizit nicht jetzt zu bauen, Masterprompt §30–32)

- Mehrstufige Multi-Agenten-Unternehmensorganisation (CEO/Product/SWE/QA/
  Security/... als eigenständige dauerhaft laufende Agenten) – architektonisch
  mitdenken, nicht jetzt bauen.
- Öffentliche Landingpage – Repo-Struktur so halten, dass spätere Integration
  sauber möglich ist, aber nicht Teil dieses Auftrags.
- Reale Gateway-Produktivbereitstellung (Hosting/Domain/Zertifikat) –
  separate, explizit zu genehmigende Infrastrukturentscheidung.
