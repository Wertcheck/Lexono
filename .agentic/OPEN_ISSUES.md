# OPEN_ISSUES – Technische Schulden & offene Workstreams

Kategorien: CRITICAL / HIGH / MEDIUM / LOW / FUTURE.
Kein Eintrag hier bedeutet automatisch Untätigkeit – Einträge werden aktiv
von den zuständigen Agenten (siehe `agents/`) abgearbeitet oder bewusst
zurückgestellt (mit Begründung).

## CRITICAL

_Keine offenen CRITICAL-Punkte zum Stand 31.08._

## HIGH

- **Dokument-Workspace mit Pseudonymisierungs-Highlighting** (Referenzbild
  2 aus Masterprompt V2) - **01.09. umgesetzt (V, real getestet)**: neue
  Route `GET /dashboard/chat/{conversation_id}/document/{document_id}`
  (`app/web/chat_router.py::chat_document_view`) zeigt den bereits lokal
  extrahierten Dokumenttext (`Document.extracted_text`) mit inline
  hervorgehobenen erkannten Kategorien (`app/chat/document_preview.py`,
  nutzt exakt dieselben Detektoren wie der echte Pseudonymisierungslauf)
  plus rechter Kontextleiste "Erkannte Mandantendaten" plus eingeklappter
  Unterhaltungsliste. Aktenisolation getestet (`ChatService.
  get_attached_document`, Dokument aus fremder Konversation liefert 404-
  Redirect). 5 neue Tests (test_document_preview.py,
  test_chat_service.py, test_web_chat.py), volle Suite grün.
  **Bewusst NICHT umgesetzt**: echtes PDF-Seiten-Rendering mit Zoom/Print/
  Seitennavigation wie im Referenzbild (bräuchte eine PDF.js-artige
  Client-Bibliothek + Koordinaten-Mapping der Presidio-Spans auf
  Pixel-Positionen im PDF - deutlich größerer, eigener Workstream). Die
  aktuelle Lösung zeigt stattdessen den EXTRAHIERTEN TEXT (denselben, den
  auch die KI tatsächlich verarbeitet) mit Text-Span-Highlighting - liefert
  dieselbe Kernfunktion (sehen, was als sensibel erkannt wurde), aber
  nicht die pixelgenaue PDF-Optik. Zuständig für eine spätere PDF-Variante:
  Agent D (Chat/Document Workspace) + Agent B (Frontend).
  Status: V (Textbasierte Variante), OFFEN (echtes PDF-Rendering).

- **Model Evaluation Engine über mehrere Runtimes/Modelle** (Masterprompt
  V2 §15–17): Bisher nur Ollama + 3 Qwen/Llama-Modellvarianten real
  benchmarkt (siehe MODEL_EVALUATION.md). Llama.cpp und weitere
  Modellfamilien (Mistral, Gemma) noch nicht evaluiert; keine
  wiederverwendbare Scoring-Engine mit repräsentativen
  Kanzlei-Testaufgaben vorhanden. Zuständig: Agent F (Local AI/Model
  Engineer).
  **01.09. konkretisiert**: Architektur-Readiness verifiziert (Protocol-
  basiert, `Settings.local_ai_runtime` + `ModelCatalogEntry.runtime`
  bereits als Erweiterungspunkte vorhanden, siehe MODEL_EVALUATION.md) -
  nächster konkreter Schritt ist NICHT mehr Recherche, sondern: (1) eine
  `LlamaCppLocalLLMProvider`-Klasse gegen dasselbe `LocalLLMProvider`-
  Protocol schreiben, (2) `build_local_llm_provider` um Dispatch auf
  `settings.local_ai_runtime` erweitern, (3) echten Benchmark gegen die
  bestehende `qwen2.5:1.5b`-Baseline fahren. Absichtlich nicht in dieser
  Session begonnen (mehrstündiger Download-/Kompilier-/Messaufwand,
  unverhältnismäßig neben den übrigen Punkten dieser Iteration).
  Status: NV (Umsetzung), Architektur-Readiness V (verifiziert).

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

## OFFEN – PRODUKTENTSCHEIDUNG ERFORDERLICH

- **`PROMPT38_ANALYSIS.md` (Repo-Root)**: dokumentiert eine abgeschlossene
  ANALYSE zu "Multi-Kanzlei-Profile + Cross-Tenant-Tests", explizit
  markiert "Implementierung noch NICHT begonnen. Kein Code geändert."
  Gefunden bei der Repository-Hygiene-Durchsicht (01.09., Master
  Workstream V3, §24). Ob Lexono mehrere Kanzleien in einer Instanz
  unterstützen soll, ist eine Produktentscheidung, keine rein technische
  - nicht ungefragt begonnen. Bei Bedarf: `PROMPT38_ANALYSIS.md` zuerst
  lesen, dann mit dem Nutzer klären, ob/wann das noch relevant ist.

## LOW

- **`move_window_by`/`resize_window_by` ohne Bildschirm-Clamp**
  (`run.py::_NativeApi`, gefunden bei unabhängiger Sicherheitsdurchsicht
  01.09.): kein oberes Limit und keine Prüfung, ob das Fenster (bzw.
  zumindest die Titelleiste) noch sichtbar bleibt - ein Fenster könnte
  theoretisch vollständig aus dem sichtbaren Bereich gezogen werden, ohne
  offensichtlichen Weg, es zurückzuholen (kein Alt+Leertaste-Menü
  garantiert). Ausdrücklich KEIN Sicherheitsproblem (Review-Ergebnis:
  "acceptable for a fully trusted first-party desktop shell"), nur
  UX-Robustheit. Nicht behoben, da spekulativ (bräuchte
  Bildschirmgrößen-Erkennung für einen sauberen Clamp) und durch reines
  Draggen der eigenen Titelleiste in der Praxis kaum auslösbar.

- **Silent-Install haengt gelegentlich beim ersten Versuch (wiederholt
  beobachtet, 01.09., drei separate Installer-Rebuilds derselben Nacht)**:
  Bei JEDEM der drei Installer-Rebuilds dieser Nacht haengte sich
  mindestens ein `/VERYSILENT`-Lauf bei konstant ~0% CPU auf (sichtbares,
  aber inaktives Setup-Fenster, keine Kindprozesse, kein Fortschritt bei
  der Datei-Extraktion) - meist nach 5-10 Minuten erkennbar. Beim letzten
  Rebuild sogar ZWEI aufeinanderfolgende haengende Versuche vor einem
  erfolgreichen dritten. **Zuverlaessig behobenes Muster**: betroffenen
  `Lexono_Setup`/`Lexono_Setup.tmp`-Prozess beenden und denselben Befehl
  erneut ausfuehren - hat in JEDEM Fall beim naechsten oder uebernaechsten
  Versuch funktioniert (erkennbar am tatsaechlich steigenden CPU-Verbrauch
  des `.tmp`-Prozesses, z. B. 67s echte CPU-Zeit bei einem erfolgreichen
  Versuch vs. konstant ~0,15s bei einem haengenden). Ursache weiterhin
  NICHT geklaert (Kandidaten unveraendert: Windows-Defender-
  Echtzeitpruefung der frisch entpackten ~1,1GB, ein Inno-Setup-eigener
  Zustand) - Disk-Speicherplatz wurde als Ursache ausgeschlossen (~324GB
  frei). Fuer zukuenftige Installer-Laeufe: IMMER CPU-Verbrauch des
  `.tmp`-Prozesses ueber mehrere Minuten vergleichen statt nur auf den
  Log-Dateipfad zu warten (der wird ohnehin erst beim Prozessende
  geschrieben, in dieser Umgebung nie zuverlaessig beobachtet) - flache
  CPU-Kurve ueber 5+ Minuten ist der zuverlaessigste Hinweis auf einen
  echten Haenger.

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
