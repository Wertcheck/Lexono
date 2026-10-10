# PERFORMANCE_BENCHMARKS.md – Schriftsatz-Geschwindigkeit (Messung 10.10.2026)

Alle Zahlen sind **gemessen** (Ausnahmen sind ausdrücklich als *Schätzung* markiert). Es wurden
ausschließlich synthetische Daten verwendet; Performance-Logs enthalten nur Schrittnamen und Zeiten.

## Testgerät und Methode

- Lenovo ThinkPad T14 Gen 2: Intel Core i5-1145G7 (4 Kerne/8 Threads), 15,7 GB RAM, Windows 11 Pro, Iris Xe
  (iGPU), reine CPU-Inferenz. Ollama 0.34.0, Modell `qwen3:4b` (Q4_K_M), Cloud: Claude über die Anthropic-API.
- **Ende-zu-Ende** im installierten Build (`Lexono.exe`, QA-Datenbank, echte Claude-Anfragen, synthetische PDFs),
  Oberfläche per Fernsteuerung bedient: Zeit von „Senden“ bis zum fertigen Schriftsatz im Chat.
  Fälle: *kurz* (kein Dokument, kurzer Auftrag), *mittel* (1 Dokument), *komplex* (3 Dokumente).
- Schrittzeiten aus dem bestehenden `PERF`-Log (`app/observability/perf_trace.py`), neu: `PERF_OLLAMA`
  (Modell laden / Prompt lesen / Text erzeugen laut Ollama, ohne Inhalte).
- Reproduzierbar: `scripts/bench_local_ai.py` (lokale Schritte einzeln), `scripts/bench_sequence.py`
  (Ablauf Vorabanalyse → Claude-Wartezeit → Prüfung; `--prefill`, `--cold`).
- Stichproben sind klein (n = 2–3 je Fall): **Median und Spannweite**, p95 wird nicht ausgewiesen, weil es bei
  n ≤ 3 nicht belastbar ist.

## Ergebnis Ende-zu-Ende (Sekunden bis zum fertigen Schriftsatz)

| Fall | Ausgangsstand (a1773a3) | Endstand | Median alt → neu |
|---|---|---|---|
| kurz | 79,0 / 53,0 | kalt (Modell entladen) 24,2; warm 15,1 / 11,7 / 12,0 | 66 → 12 |
| mittel (1 Dokument) | 257,9 / 247,9 | 126,8 / 86,8 / 86,7 | 253 → 87 |
| komplex (3 Dokumente) | 206,5 / 203,8 | 128,7 / 90,9 / 95,9 | 205 → 96 |

Der jeweils **erste** Lauf eines neuen Dokuments ist der realistische Wert; Wiederholungen mit identischem
Dokument profitieren vom Prompt-Cache von Ollama (daher 87/96 s in Lauf 2–3).

Schritte (Median der Läufe, Sekunden):

| Schritt | mittel alt | mittel neu | komplex alt | komplex neu |
|---|---|---|---|---|
| lokale Vorabanalyse (Zusammenfassung) | 48–64 | 45–79 | 61–78 | 39–77 |
| Claude (Cloud) | ca. 15 | ca. 15 | ca. 15 | ca. 15 |
| lokale semantische Prüfung | 174–183 | 25–29 | 111–125 | 32–35 |
| Presidio/Pseudonymisierung, Retrieval, Rekonstruktion, Speichern | < 3 | < 3 | < 3 | < 3 |

Synthetischer Ablauf-Benchmark (`bench_sequence.py`, Claude simuliert mit 15 s, n = 2, nur lokale Zeit):
kurz 67,7 → 58,0 s, mittel 133,9 → 95,9 s, komplex **Abbruch nach 240 s (Zeitüberschreitung, im Betrieb ein
Fail-Closed-Block) → 150,8 s**.

## Engpässe (gemessen)

1. **Lokale Modellerzeugung**: Erzeugen kostet ~0,15–0,19 s je Token (5–8 Token/s, speicherbandbreiten-
   begrenzt; Threadzahl 4/6/8 ändert nichts). Die semantische Prüfung erzeugte zu „passed: true“ im Mittel
   431 Token (68 s), obwohl nur `passed` entscheidet.
2. **Prompt-Verarbeitung** auf der CPU: 46–60 Token/s (ein Entwurf von ~1000 Token ≈ 20–30 s).
3. **Endlos-Erzeugung** der Zusammenfassung bei langen Sachverhalten (2377 Token, > 600 s) – im Betrieb ein
   Timeout nach 240 s.
4. **Kontextfenster 4096** (Ollama-Standard): lange Prompts wurden von Ollama still in der Mitte gekürzt
   (`truncated = 1` im Ollama-Log).
5. **`localhost` → IPv6-Verzögerung**: unter Windows kostete **jede** lokale Anfrage ~2 s (2,25 s statt 0,19 s).
6. Modellladen: 3–5 s (kalt), danach 30 min im Speicher. RAM: Modell ≈ 3,6 GB inkl. Kontext; 16 GB reichen.

## Umgesetzte Verbesserungen

- Ausgabe der semantischen Prüfung per Schema begrenzt (`maxItems`/`maxLength`) und Prompt-Hinweis „issues leer
  bei passed“: 431 → 14 Token, gleiche Entscheidung (Test: fehlerfreier und fehlerhafter Text).
- Zusammenfassung: Längengrenze 1200 Zeichen (Grammatik) → keine Endlos-Erzeugung; Eingabe auf 6000 Zeichen
  begrenzt und **sichtbar** gekennzeichnet gekürzt; Claude erhält weiterhin den vollständigen Sachverhalt.
- Kontextfenster fest 6144 (+≈ 0,3 GB RAM) für alle lokalen Aufrufe; Sicherheitsnetz `num_predict` 1024.
- Prüfprompt-Anfang (Anweisung + Sachverhalt) wird **während des Claude-Aufrufs** vorgewärmt (Prompt-Cache);
  Sachverhalt in der Prüfung auf 4000 Zeichen begrenzt (kenntlich gekürzt; der Entwurf wird nie gekürzt).
- Sehr kurze Sachverhalte (< 800 Zeichen): Zusammenfassung entfällt (sie wäre nicht kürzer als der Text);
  **die Erreichbarkeit der lokalen KI wird weiterhin vor dem Claude-Aufruf geprüft** (sonst Fail-Closed wie bisher).
- `localhost` → `127.0.0.1` für Ollama (−2 s je lokaler Anfrage).
- Modell wird beim Start geladen (Warm-up).

## Qualität und Datenschutz

Nachprüfung im installierten Build mit abgefangenen Cloud-Anfragen (kurz/mittel/komplex, synthetische Dokumente): **0 Originalwerte** in allen drei Payloads, Beträge im Klartext vorhanden, lokale Chronologie enthalten. Unverändert: Presidio, Pseudonymisierung, Platzhalter-Integritätsprüfung (Stufe 1), Leak-Check,
Fail-Closed, Beträge im Klartext, Briefkopf/Unterzeichner/Versionen/Export. Es werden keine Originalwerte an
die Cloud gesendet. Verändert und bewusst benannt: die **lokale** Qualitätsprüfung (Stufe 2) sieht bei sehr
langen Sachverhalten nur die ersten 4000 Zeichen; die lokale Zusammenfassung liest höchstens 6000 Zeichen.

## Hardware-Bewertung (16 / 32 / 64 GB)

- **Gemessen:** Die Zeit wird von Speicherbandbreite (Erzeugung) und CPU-Rechenleistung (Prompt-Lesen) bestimmt,
  **nicht von der RAM-Menge**: Das 4B-Modell belegt ≈ 3,6 GB, der Rechner hatte während der Läufe ≈ 8 GB frei.
  Mehr RAM bei gleicher CPU/Bandbreite beschleunigt `qwen3:4b` daher **nicht**.
- **Gemessen (Experiment, nicht aktiviert):** Ollama mit Vulkan auf der Iris Xe (`OLLAMA_VULKAN=1`,
  `OLLAMA_IGPU_ENABLE=1`): Prompt-Lesen 117–159 statt 46–60 Token/s (≈ 2,7×), Prüfung 42 → 19 s;
  Erzeugung unverändert (5,3 Token/s) und die Zusammenfassung **langsamer** (58–66 → 71–79 s). Netto kein
  Vorteil, daher nicht empfohlen.
- **Schätzung (nicht gemessen):** 32/64 GB lohnen nur, wenn ein größeres Modell (8B–14B) gewünscht ist – das
  wäre auf diesem Gerät langsamer. Eine schnellere Erzeugung bräuchte höhere Speicherbandbreite (aktuelle
  Plattformen mit LPDDR5x oder eine dedizierte GPU); ein Vergleichsgerät wurde **nicht** getestet.

## Offene Entscheidung (fachlich, Eigentümer)

Die lokale **Zusammenfassung** ist mit 40–80 s der größte verbleibende Posten (≈ die Hälfte der Laufzeit bei
Dokumenten). Claude erhält den vollständigen Sachverhalt ohnehin; die Zusammenfassung ist ein zusätzlicher
Hinweis (§65-Pflichtschritt). Optionen: (a) beibehalten, (b) nur ab einer Mindestlänge/auf Wunsch,
(c) kleineres Modell für diesen Schritt (nur mit Qualitäts- und Datenschutztest). Ohne Entscheidung unverändert.
Hochrechnung (Schätzung aus den Schrittzeiten): ohne Zusammenfassung ≈ 45 s (mittel) bzw. ≈ 55 s (komplex).
