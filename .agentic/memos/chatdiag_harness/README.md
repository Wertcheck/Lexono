# Mess-/Diagnosewerkzeuge zur Chat-Intelligence-Forensik (15.09.)

KEIN Produktcode. Diese Skripte gehoeren zum Befund in
`../CHAT_INTELLIGENCE_MEMO.md` und liegen hier, damit die Messungen
WIEDERHOLBAR sind - der Messplan der Tasks CHAT-01/CHAT-02 verlangt
ausdruecklich ein erneutes Ausfuehren nach der Umsetzung, sonst ist
"besser geworden" wieder nur eine Behauptung.

- `payload_spy.py`   - haengt sich an `ClaudeWritingProvider.write` und
                       schreibt den TATSAECHLICH gesendeten Payload nach
                       `payload_capture.json`. Damit wurde belegt, dass der
                       Gespraechsverlauf das Modell nicht erreicht.
- `perf.py`          - Latenz je Pipeline-Stufe, cold/warm
                       (`perf_results.json`).
- `block_reasons.py`,
  `why_blocked.py`,
  `block2.py`        - isolieren die Antwort-Vollstaendigkeitspruefung
                       (CHAT-01) ohne den Rest der Pipeline.
- `trace_exc.py`     - macht die verschluckte technische Fehlerursache
                       sichtbar; hat CHAT-05 ueberhaupt erst aufgedeckt.
- `chat04_perf.py`   - reale Vorher-/Nachher-Latenzmessung von CHAT-04
                       gegen ECHTES Ollama (kein Fake-Provider): "Hallo"
                       auf einer Akte mit Mandantennamen im Titel
                       (Skip-Pfad) vs. Kontrollgruppe mit neuem,
                       unbekanntem Namen (volle Pipeline muss weiterhin
                       laufen). Ergebnis: 2,60s vs. 24,35s (9,4x). Braucht
                       ein lokal laufendes Ollama mit `qwen2.5:1.5b`.
- `chat02_e2e_proof.py` - CHAT-02-Ende-zu-Ende-Beweis: EXAKT derselbe
                       4-Turn-Dialog wie in `payload_spy.py`
                       ("Hallo"/"Ich habe eine Frage zum
                       Steuerrecht."/"Es geht um einen
                       Steuerbescheid."/"Welche Frist gilt?"), diesmal MIT
                       CHAT-02. Beantwortet die urspruengliche
                       Forensik-Kernfrage jetzt mit "True" fuer alle drei
                       Sonden (vorher: nur zufaellig "Steuerbescheid" ueber
                       den Aktentitel, "Hallo"/"Steuerrecht" fehlten
                       komplett). Zeigt zusaetzlich: Turn 4 dupliziert die
                       aktuelle Nachricht NICHT im Gespraechsverlauf.

Ausfuehrung gegen eine KOPIE der Datenbank, nie gegen die installierte
Instanz. Die Skripte sind Diagnosestand vom 15.09. und werden nicht
mitgetestet.
