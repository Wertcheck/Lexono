# DECISIONS – Architektur- und Produktentscheidungen

Format: DECISION / REASON / DATE. Neue Einträge unten anfügen, bestehende
nicht rückwirkend umschreiben (Historie bleibt nachvollziehbar).

---

DECISION: Gateway-Relay-Architektur (Lexono Gateway hält den echten
Anthropic-Key, Kanzlei-PC kennt nur Gateway-Adresse + Tenant-Credential)
bleibt Baseline und wird nicht zurückgebaut.
REASON: Bewusste Umkehrung einer früheren „kein zentraler Proxy“-Entscheidung
aus einer vorherigen Session; siehe ARCHITECTURE.md Gateway-Kapitel.
DATE: vor 31.08. (Vorsession)

---

DECISION: Lokales Standardmodell ist `qwen2.5:1.5b` statt `qwen3:4b`.
REASON: Echter Benchmark auf realer CPU-only-Entwicklungshardware zeigte
qwen3:4b >20 Min. Latenz durch „Thinking“-Modus; qwen2.5:1.5b liefert in
10–11s (warm) korrekte, nicht-halluzinierte Ergebnisse. Datenbasierte
Entscheidung gemäß Masterprompt-Anforderung „nicht einfach abschalten,
sondern testen“. Siehe ARCHITECTURE.md §71, `.agentic/MODEL_EVALUATION.md`.
DATE: 31.08.

---

DECISION: Local-AI-Setup wird über den interaktiven Setup-Wizard verdrahtet
(`local-ai-setup` CLI-Subcommand, Opt-out, NICHT load-bearing für die
Kernanwendung).
REASON: Hardware-Detection/Model-Recommendation/Ollama-Installer existierten
bereits vollständig, wurden aber nie aufgerufen – größte reine
Verdrahtungslücke der Local-AI-Architektur. Bewusst nicht load-bearing, da
ein fehlgeschlagenes Local-AI-Setup die App-Installation nicht blockieren
darf (Kernanwendung muss auch ohne lokale KI startbar bleiben).
DATE: 31.08.

---

DECISION: „Presidio“ wird aus der normalen Produkt-UI entfernt (nur noch
„Pseudonymisierung“); auf technischen/administrativen Seiten
(`account_privacy.html`, `settings.html`), die sich explizit als
„Technische Transparenz“ ausweisen, bleibt die Nennung bestehen.
REASON: Masterprompt V2 §13/§21 verlangt Entfernung des Bibliotheksnamens
aus der normalen Oberfläche, erlaubt ihn aber ausdrücklich in technischer
Dokumentation. Die beiden genannten Seiten sind bewusst als technische
Transparenzseiten gerahmt, nicht Teil des Kern-Workflows – dort bleibt der
Begriff bewusst stehen statt eine riskante flächendeckende Änderung
vorzunehmen.
DATE: 31.08.

---

DECISION: „Strg+K“-Tastenkürzel-Badge wird aus der sichtbaren UI entfernt,
die Tastenkombination (Strg+K/⌘K) selbst bleibt funktional erhalten.
REASON: Masterprompt V2 §21 explizit: „Suchfeld ohne 'Strg+K'“ (visuell).
DATE: 31.08.

---

DECISION: Kein Wechsel auf ein frameless pywebview-Fenster in dieser
Iteration (siehe OPEN_ISSUES.md, Kategorie MEDIUM).
REASON: Das native WinForms-Fenster (`run.py::webview.create_window`) ist
Teil einer in Vorsessions mühsam stabilisierten Desktop-Shell (u. a.
WebView2-Bootstrapper-Fix). Ein Wechsel auf `frameless=True` würde native
Resize-Fähigkeit riskieren (pywebview bietet für frameless Fenster kein
eingebautes Rand-Resize) und erfordert eigene Minimieren/Schließen-Buttons
über die JS-Bridge. Substanzielle, aber risikoreiche Änderung – bewusst als
eigener, separat zu entscheidender Workstream zurückgestellt statt
nebenbei im selben Durchgang wie Textänderungen umgesetzt.
DATE: 31.08.
