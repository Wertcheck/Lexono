# OPEN_ISSUES – Technische Schulden & offene Workstreams

Kategorien: CRITICAL / HIGH / MEDIUM / LOW / FUTURE.
Kein Eintrag hier bedeutet automatisch Untätigkeit – Einträge werden aktiv
von den zuständigen Agenten (siehe `agents/`) abgearbeitet oder bewusst
zurückgestellt (mit Begründung).

## CRITICAL

_Keine offenen CRITICAL-Punkte (zuletzt geprüft 01.09.)._

## OFFEN – PRODUKTENTSCHEIDUNG ERFORDERLICH

- **Logo-/Akzentfarbe: grün (Nutzerauftrag) vs. verifiziertes Navy
  `#101828` (frühere Sitzung)** – **NEU, 01.09., höchste Priorität
  dieser Kategorie.** Der Nutzer verlangt explizit ein "neues grünes
  Logo" sowie weitere, konsequent umgesetzte Akzentfarben. Der aktuelle
  Farbcode `#101828` für Logo (`app/web/static/img/logo.svg`) UND die
  primäre UI-Akzentfarbe (`--seal-green`/`-dark`/`-tint` in
  `app/web/static/css/app.css`, ~72 Verwendungsstellen) wurde jedoch in
  einer früheren Sitzung NICHT willkürlich gewählt, sondern per
  Pixelfarbmessung aus dem tatsächlichen, vom Anwalt bereitgestellten
  offiziellen Logo-Bild (`Desktop\Lexono Logo.png`) verifiziert
  (Kernfarbwert ~`#0d1526`, dunkles Navy) und danach bewusst auf
  primäre UI-Elemente ausgeweitet - dokumentiert in `ARCHITECTURE.md`
  §61/§62 mit vollständiger Herleitung.
  **Konkrete Frage an den Nutzer**: Gibt es eine NEUERE/andere offizielle
  Logo-Datei (grün), die die Grundlage für diese Anweisung ist? Falls ja,
  bitte bereitstellen (Datei oder exakter Hex-Farbwert) - dann wird die
  Änderung sauber und pixelgenau wie beim vorherigen Mal durchgeführt.
  Falls die Erwartung auf den textuellen Referenzbeschreibungen aus
  früheren Prompts beruht (die von einem "grünen Icon" sprachen, ohne
  dass dafür je eine reale Datei vorlag) und das verifizierte Navy
  tatsächlich das korrekte, aktuelle offizielle Logo ist, wäre stattdessen
  zu klären, ob die Erwartungshaltung (grün) angepasst werden soll.
  Nicht eigenmächtig entschieden - beide Interpretationen sind mit den
  vorliegenden Informationen plausibel, eine Markenfarbentscheidung
  gehört nicht zu den "normalen Implementierungsentscheidungen", die
  autonom getroffen werden dürfen.
  **Update 01.09., später**: der unabhängige Teil ("weitere Akzentfarben")
  wurde umgesetzt - die vier Chat-Schnellaktionen zeigen jetzt farblich
  unterschiedliche Icon-Badges (`--accent-blue`/`-purple`/`-orange`, neu
  in `app.css`, unabhängig von der Primärfarbe). NUR die "grüne" Badge
  bindet weiterhin bewusst an `--seal-green` (die umstrittene
  Primärfarbe) - sobald die Logo-Frage geklärt ist, übernimmt diese Badge
  automatisch den finalen Wert, ohne weitere Änderung nötig.
  Status: OE nur noch für die Logo-/Primärfarbe selbst (siehe
  `PROJECT_STATE.md`); "weitere Akzentfarben" V (umgesetzt, getestet).

- **`PROMPT38_ANALYSIS.md` (Repo-Root)**: dokumentiert eine abgeschlossene
  ANALYSE zu "Multi-Kanzlei-Profile + Cross-Tenant-Tests", explizit
  markiert "Implementierung noch NICHT begonnen. Kein Code geändert."
  Ob Lexono mehrere Kanzleien in einer Instanz unterstützen soll, ist
  eine Produktentscheidung, keine rein technische - nicht ungefragt
  begonnen. Bei Bedarf: `PROMPT38_ANALYSIS.md` zuerst lesen, dann mit dem
  Nutzer klären, ob/wann das noch relevant ist.

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
  eine echte llama.cpp-Runtime-Integration bleibt ein eigener,
  mehrstündiger Download-/Kompilier-/Messaufwand und wurde bewusst NICHT
  begonnen (unverhältnismäßig).
  **01.09., später: viertes Modell real benchmarkt** - `gemma2:2b`
  (bounded, nur ein weiteres Ollama-Modell, kein neuer Runtime-Aufwand)
  real getestet: langsamer (18,2s warm vs. 10-11s bei qwen2.5:1.5b) UND
  bei einem Pflichtkriterium (Platzhaltererhaltung) unzuverlässig - zwei
  reale Fälle von veränderten/verlorenen Platzhaltern in nur zwei
  Testläufen, siehe MODEL_EVALUATION.md für Details. `qwen2.5:1.5b`
  bleibt datenbasiert bestätigt die beste Wahl - kein Konfigurationswechsel.
  Status: NV (llama.cpp-Runtime), V (Mistral/Gemma-Modellfamilie
  stichprobenhaft evaluiert - gemma2:2b negativ beschieden), Architektur-
  Readiness V (verifiziert).

## MEDIUM

- **Visual QA Loop (§23)**: Noch kein echter Screenshot-Vergleichslauf
  gegen die bereitgestellten Referenzbilder in dieser Session durchgeführt
  (kein Browser-Tool in dieser Umgebung aktiv verfügbar). Zuständig: Agent
  J (Visual QA).
  Status: NV, siehe VISUAL_QA.md.

- **ERLEDIGT, hier nur zur Nachvollziehbarkeit erwähnt**: "Fenster-Chrome"
  (frameless statt nativ) und "Statusindikatoren global in der Sidebar"
  standen hier vorher als offen - beides ist seit 01.09. umgesetzt und
  getestet (Fenster-Chrome zusätzlich vom Nutzer real am Schließen-Button
  bestätigt). Siehe `PROJECT_STATE.md` für den aktuellen Stand,
  `SESSION_LOG.md` für den Verlauf.

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

## FUTURE (erwogen, bewusst nicht umgesetzt)

- **Maximieren per Doppelklick auf die Titelleiste**: erwogen als
  Ergänzung zum Eck-Resize-Griff (der einzige aktuelle Weg, die
  Fenstergröße zu ändern - Griff ist klein, für Nutzer mit
  motorischen Einschränkungen ggf. schwer zu treffen). `webview.Window.
  maximize()`/`.restore()` existieren bereits und sind ein echtes
  Toggle-Paar (`WindowState = Maximized`/`Normal`, in `winforms.py`
  verifiziert) - das Problem ist NICHT die Umsetzung des Togglens
  selbst, sondern die zuverlässige Erkennung des AKTUELLEN Zustands
  (maximiert oder nicht) vor jedem Doppelklick, ohne die native
  Fenster-Objektebene direkt anzufassen. `_NativeApi`s eigener
  Docstring (`run.py`) warnt ausdrücklich vor genau diesem Bereich -
  ein früherer, real aufgetretener Bug (`get_functions`-Rekursion durch
  ein öffentliches `window`-Attribut) führte zu einem kompletten
  Programmhänger beim Fensteraufbau. Nicht umgesetzt, um dieses Risiko
  nicht für ein unaufgefordertes Komfort-Feature einzugehen.

## FUTURE (explizit nicht jetzt zu bauen, Masterprompt §30–32)

- Mehrstufige Multi-Agenten-Unternehmensorganisation (CEO/Product/SWE/QA/
  Security/... als eigenständige dauerhaft laufende Agenten) – architektonisch
  mitdenken, nicht jetzt bauen.
- Öffentliche Landingpage – Repo-Struktur so halten, dass spätere Integration
  sauber möglich ist, aber nicht Teil dieses Auftrags.
- Reale Gateway-Produktivbereitstellung (Hosting/Domain/Zertifikat) –
  separate, explizit zu genehmigende Infrastrukturentscheidung.
