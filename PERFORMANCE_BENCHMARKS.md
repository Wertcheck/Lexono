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

## Lokale Zusammenfassung: A/B-Vergleich (Messung 10.10.2026, zweiter Lauf)

**Aufbau.** Gleiche Aufträge, drei Varianten über die neue Einstellung `LOCAL_SUMMARY_MODE`: **A** `always`
(immer zusammenfassen), **B** `never` (nie), **auto** (Standard, Regeln unten). Echte Claude-Anfragen,
synthetische Dokumente, installierter Build. Fälle: K (kurz, ohne Dokument), M (1 Dokument), C (2 Dokumente,
Kaufvertrag/Quittung mit Preisabweichung), L (4 Dokumente, ~6.200 Zeichen, Werkvertrag mit Einwänden, Beträgen,
Fristen). 3–6 Läufe je Fall und Variante.

**Laufzeit (Sekunden bis zum fertigen Schriftsatz, Median [Spanne]):**

| Fall | A: immer | B: nie | auto (Endstand, n = 3) |
|---|---|---|---|
| K | 32,5 [31,5–37,4] | 19,1 [16,6–19,2] | 15,3 [15,0–17,9] |
| M | 102 [96–115] (n = 6) | 43 [39–46] (n = 6) | 96 [89–97] |
| C | 107 [91–122] (n = 6) | 51 [42–68] (n = 6) | 94 [92–94] |
| L | **0 von 3 Ergebnissen** (294 s: Zeitüberschreitung der Zusammenfassung; 2 × 146 s: Block) | 1 von 3 (133 s); 2 × Block nach 42 s | **3 von 3** (127 [126–129]) |

Schritte (Median): Zusammenfassung 38–50 s bei M/C, **143 s bei L (einmal 240 s = Zeitüberschreitung)**;
semantische Prüfung 24–35 s bei M/C, 85–90 s bei L, 8–9 s bei K; Claude ≈ 15 s; Rest < 3 s.

**Qualität (blindes Paarurteil durch Claude, zufällige Reihenfolge, Quelldokumente und Aufgabe als Maßstab;
Punkte 1–5, Mittel; „A besser“ = Entwurf mit Zusammenfassung):**

| Fall | Paare | A besser / B besser | Vollständigkeit A / B | Chronologie A / B | Beträge A / B | Einwände A / B | jur. Qualität A / B |
|---|---|---|---|---|---|---|---|
| M | 6 | 5 / 1 | 4,00 / 3,17 | 4,33 / 3,83 | 4,83 / 4,33 | 3,67 / 3,17 | 3,83 / 3,17 |
| C | 6 | 5 / 1 | 4,00 / 3,50 | 4,17 / 3,33 | 4,83 / 3,50 | 2,67 / 2,00 | 2,83 / 2,33 |
| K | 3 | Wertungen identisch in allen Kategorien | 4,33 / 4,33 | 4,33 / 4,33 | 3,67 / 3,67 | 3,67 / 3,67 | 4,00 / 4,00 |

Deterministische Faktenabdeckung (Beträge/Daten/Sachverhaltsbegriffe, % der erwarteten Fakten im Entwurf) – kein
einheitliches Bild: M A 67/61/54 vs. B 67/56/79; C A 100/100/100 vs. B 100/100/92. Beträge wurden in allen Entwürfen
im Klartext und richtig übernommen.

**Einordnung (keine stärkere Aussage als die Daten tragen):** Das Urteil stammt von einem Sprachmodell
(n = 6 Paare je Fall, ein Urteil je Paar, mögliche Längen-Präferenz). 10 von 12 Paaren (M, C) fielen zugunsten der
Zusammenfassung aus – ein **Hinweis auf einen Nutzen bei Dokumentfällen**, kein Beweis. Bei K (kein Dokument) gab es
keinen Unterschied. Bei L ist die Zusammenfassung ein **nachgewiesener Nachteil** (Zeit, Zeitüberschreitung, eigene
Blockaden), ein Qualitätsvergleich war dort wegen zu weniger gültiger Entwürfe nicht möglich.

**Daraus abgeleitete Regeln (Modus `auto`, Standard):**
1. Ohne Dokument und unter 800 Zeichen: keine Zusammenfassung (K: −17 s, gleiche Wertung). Bestehende Regel bleibt;
   sie gilt nur noch **ohne** Dokument, weil C (kurz, aber mit Dokumenten) von der Zusammenfassung profitierte.
2. Über 3000 Zeichen Sachverhalt: keine Zusammenfassung. Begründung aus den Messwerten: Kosten wachsen mit der
   Eingabe (Prompt-Lesen ≈ 50–60 Token/s, Erzeugung bei langem Kontext ≈ 2 Token/s, ≈ 1,35 Zeichen je Token); bei
   3000 Zeichen ≈ 110 s (Schätzung aus den Messwerten), mehr als doppelte Sicherheit zur 240-s-Grenze, bei ~6000
   Zeichen gemessen 143–240 s.
3. Sonst läuft sie. `LOCAL_SUMMARY_MODE=never` gibt M/C auf ≈ 43–51 s, auf Kosten des beobachteten Qualitätsvorteils;
   `always` erzwingt sie (nicht empfohlen bei langen Sachverhalten).
In **allen** Modi: Erreichbarkeitsprüfung der lokalen KI vor dem Claude-Aufruf (sonst Fail-Closed), semantische
Antwortprüfung, Presidio, Leak-Check unverändert. Claude erhält den **vollen** Sachverhalt: Die 4000-Zeichen-Grenze
gilt nur im lokalen Prüfprompt und die 6000-Zeichen-Grenze nur im lokalen Zusammenfassungsprompt (Regressionstest
mit 8 Dokumenten, > 12.000 Zeichen, alle Schlusswörter im Cloud-Payload).

**Privacy (installierter Build, abgefangene Cloud-Anfragen K/M/C/L):** 0 Originalwerte in allen vier Payloads
(L: 11.787 Zeichen, Namen/Adressen/Daten/Rechnungsnummer geprüft), Beträge im Klartext, Chronologie enthalten.
**Export (installierter Build, Entwurf aus L):** DOCX (40 KB) und PDF (3 Seiten) werden erzeugt; Briefkopf im
DOCX-Header bzw. PDF-Kopf, Unterzeichner am Ende, „Entwurf Version 1“, Beträge vorhanden, keine Platzhalter im Export.

## Neue Befunde (nicht Teil dieser Änderung, nicht behoben)

1. **Leak-Check-Fehlalarme durch NER:** In Fall L wurden normale Wörter als Personen/Orte pseudonymisiert („Mängel“ als
   `PERSON`, „Attika“, „Verblechungen“, „Bitumenbahn“ als `ORT`). Verwendet Claude das Wort im Entwurf, blockiert der
   Leak-Check („nicht ausreichend anonymisierter Wert“) – in 2 von 3 Läufen ohne Zusammenfassung und in 2 von 3
   mit Zusammenfassung (dort schon an der lokalen Zusammenfassung). Das Fail-Closed-Verhalten ist korrekt, die
   Trefferqualität der Erkennung bei Bau-/Fachvokabular ist es nicht. Eine Änderung an Detektoren ist eine
   Privacy-Entscheidung und wurde nicht vorgenommen.
2. **Auszugsgrenze:** Jedes Dokument geht höchstens mit den ersten 5000 Zeichen in den Sachverhalt
   (`local_ai_provider._MAX_DOCUMENT_EXCERPT_CHARS`, damals gemessen: größtes Dokument 4594 Zeichen). Längere
   Dokumente erreichen Claude nur gekürzt – bei echten, längeren Verträgen/Schriftsätzen prüfen.

## Qualitätslauf (10.10.2026, dritter Lauf): Dokumentvollständigkeit, Pseudonymisierung, Zusammenfassung

### 1. Dokumentvollständigkeit

**Befund (gemessen, `scripts/bench_document_completeness.py`, synthetische Verträge mit Fakten am Anfang, in der
Mitte und am Ende).** Jede Anfrage (Schriftsatz, Chat, Review) baut den Sachverhalt in
`RuleBasedLocalAIProvider.prepare_draft_context`: alle Dokumente der Akte (neueste zuerst, höchstens 30), jedes
auf die **ersten 5000 Zeichen** gekürzt, nur mit „…“ markiert. Betroffen sind alle Dokumenttypen und alle
Workflows; die Grenze war an einem Bestand gemessen worden, dessen größtes Dokument 4594 Zeichen hatte.

| Dokumentlänge | Fakten Anfang | Mitte (Beträge/Fristen) | Ende (inkl. Schlussanweisung) |
|---|---|---|---|
| ≤ 5000 Zeichen | 100 % | 100 % | 100 % |
| 10.000–95.000 Zeichen (vorher) | 100 % | **0 %** | **0 %** |

**Änderung.** Je Dokument höchstens 30.000 Zeichen, über alle Dokumente höchstens 90.000 (jedes Dokument
mindestens 3000). Längere Dokumente werden nicht mehr nur vorne abgeschnitten, sondern als **Anfang (55 %) +
ausgewählte Schlüsselstellen aus dem Mittelteil (Sätze mit Beträgen, Daten, Fristen, Rechtsfolgen,
Gliederungszeilen) + Ende (25 %)** übergeben, an Wortgrenzen geschnitten, mit Marker im Text für Claude
(„Auszug: N von M Zeichen ausgelassen …“) und einem Hinweis in den Prüfpunkten für die Anwaltschaft.

| Dokumentlänge | Sachverhalt danach (Zeichen ≈ Token/3) | Anfang | Mitte (Beträge/Fristen) | Mitte (beschreibend) | Ende |
|---|---|---|---|---|---|
| 10.000 | 10.214 (≈ 3,4 Tsd.) | 100 % | 100 % | 100 % | 100 % |
| 20.000 | 20.111 (≈ 6,7 Tsd.) | 100 % | 100 % | 100 % | 100 % |
| 47.500 | 25.178 (≈ 8,4 Tsd.) | 100 % | 100 % | **0 %** | 100 % |
| 94.900 | 27.185 (≈ 9,1 Tsd.) | 100 % | 100 % | **0 %** | 100 % |

**Kosten.** Datenschutz-Gateway (Presidio) ≈ 0,1 s je 1000 Zeichen (gemessen: 40.000 Zeichen 3,8 s, 80.000
Zeichen 7,7 s); im installierten Build 2,3–3,4 s für 12–27 Tsd. Zeichen. Claude-Eingabe bis ≈ 9 Tsd. Token je
Dokument. Der Prompt bleibt begrenzt (90.000 Zeichen insgesamt).

**Grenze der Methode (NICHT behebbar ohne Eingabe-Vergrößerung):** Beschreibende Fakten ohne Betrag/Datum/Frist
im ausgelassenen Mittelteil von Dokumenten **über 30.000 Zeichen** erreichen Claude nicht (0 % im Test). Die
Anwaltschaft wird darauf per Prüfpunkt hingewiesen. Echte Dokumentlängen in Kanzleien wurden nicht gemessen
(NICHT VERIFIZIERT).

**Installierter Build (abgefangene Cloud-Anfragen, synthetische Verträge):** 14.000-Zeichen-Vertrag: Payload
13.553 Zeichen, vollständig (Datumsangaben als Platzhalter); 60.000-Zeichen-Vertrag: 26.795 Zeichen mit
Auszug-Marker, Schlussanweisung und Schlussfakten enthalten.

### 2. Falsche Pseudonymisierungstreffer

**Ursache (reproduzierbar, `scripts/diagnose_ner_false_positives.py`).** Quelle ist das **spaCy-NER-Modell
`de_core_news_lg`**; Presidio gibt den Treffer mit konstantem Score 0,85 weiter (keine Konfidenz). Nicht die
Typzuordnung, nicht die Nachverarbeitung. Zwei Mechanismen:
1. **Seltene Fachsubstantive** werden auch in korrekt geschriebenem Text als `LOCATION` erkannt: 5 von 100
   Fachbegriffen (je 3 Sätze): Attika, Verblechung(en), Bitumenbahn, Sicherheitseinbehalt, Prozessvollmacht
   (Wortart NOUN, ohne Wortvektor; „Attika“ PROPN).
2. **Transliterierter Text (ae/oe/ue statt ä/ö/ü)** macht gewöhnliche Wörter für das Modell unbekannt:
   „Maengel“ wird `PERSON`/`MISC`, „Geschaeftsfuehrerin“ `PERSON`, „Flachdachflaeche“ `LOC`; mit Umlauten
   („Mängel“) wird nichts erkannt. Die bisherigen Testdokumente des Falls L waren transliteriert.

**Auswirkung.** (a) Erstdurchlauf: Over-Pseudonymisierung (Claude sieht „[ORT_04]“ statt „Attika“). (b) Der
Restrisiko-Scan des Gateways läuft auf dem pseudonymisierten Text mit anderem Kontext; dasselbe Wort kann dort
anders getaggt werden und löst einen Fail-Closed-Block aus („weiterhin erkennbare Muster: ort“). Gemessen im
transliterierten Fall L: **4 von 4** Läufen blockiert („Lichtkuppel“ wird erst im Zweitdurchlauf als Ort erkannt);
**mit Umlauten 3 von 3 Läufen erfolgreich** (echtes Claude). Der Leak-Check der Antwort ist wortgrenzenbasiert und
schlägt nur an, wenn Claude ein Originalwort selbst schreibt.

**Änderung (kleinste sichere Korrektur).** Ganzwort-Ausnahme für genau die 6 belegten Wörter
(`_NEVER_ENTITY_WORDS`, Gleichheit des gesamten Treffers, nie Teilstring: „Bitumenbahn GmbH“ bleibt geschützt,
Verhalten vorher/nachher identisch). Eine strukturelle Regel („NOUN und ohne Wortvektor“) wurde **bewusst nicht
eingeführt**: sie hätte an 50 seltenen echten Namen/Orten 0 Treffer gehabt, schwächt aber die Erkennung
grundsätzlich ab. **Nicht behoben:** transliterierte Dokumente können weiterhin zu Fehlalarmen/Blocks führen
(Empfehlung: Pilot-Dokumente auf Umlaute prüfen; Entscheidung über eine NER-Vorverarbeitung steht aus).
Regressionstests: positiv (6 Wörter × 3 Sätze nicht mehr pseudonymisiert), negativ (echte Namen/Adressen im
selben Satz, Firmennamen mit dem Wort, Größe der Liste begrenzt).

**NEUER, schwerwiegenderer Befund: Nachname nach „Dr.“/„Prof.“ im Klartext im Cloud-Payload (behoben).**
Beim Prüfen der Schlussanweisung eines langen Vertrags stand „Schiedsgutachter Dr. Wiebe“ als
„[PERSON_02]  Wiebe.“ im Payload. Reproduziert (`scripts/diagnose_titled_names.py`, 96 Sätze über das echte
Gateway): **13 von 96 Sätzen (alle nach „Dr.“/„Prof. Dr.“) – Nachname im Klartext, Gateway erlaubt die
Anfrage**; das NER ist dort kontextabhängig blind, und der Restrisiko-Scan fand ihn ebenfalls nicht. Zwei
Ursachen: (1) die NER übersieht den Namen oder liefert nur „Schiedsgutachter Dr.“; (2) die
Überlappungsauflösung verwarf einen später beginnenden, über den vorherigen hinausragenden Treffer vollständig,
dessen Rest blieb unersetzt. Bisher blieb das oft unentdeckt, weil lange Dokumente schon nach 5000 Zeichen
endeten. **Änderungen:** deterministischer Detektor „Titel + Name“ (`detect_titled_person`, Titel gehört in den
Treffer, höchstens zwei Namenswörter, auch bei Zeilenumbruch nach dem Titel) und Vereinigung teilüberlappender
Treffer **gleicher Kategorie** in der Überlappungsauflösung. **Ergebnis: 0 von 96 Sätzen, 0 Blocks; beide langen
Testverträge ohne Klartextname im Payload.** Tests: positive/negative Detektortests (Titel ohne Namen, kleine
Folgewörter, Zeilengrenzen), Überlappungstests (enthalten, disjunkt, andere Kategorie unverändert), Gateway-Tests
mit Betrag-Erhalt.

### 3. Lokale Zusammenfassung effizienter

Gemessen (in-process, echtes Ollama, kompakter Prompt „höchstens 4 Stichpunkte à 12 Wörter“, Grenze 500 Zeichen):
Zusammenfassung **16–20 s statt 28–46 s** (aktuell; 11 bzw. 12 Läufe, erster Lauf 31–32 s kalt), Ergebnis 210–266
statt 430–510 Zeichen. Deterministische Faktenabdeckung der Entwürfe vergleichbar (Beträge gleich, Daten
M 56 % vs. 39 % ohne Zusammenfassung). **Ob der im A/B beobachtete Qualitätsvorteil (Paarurteil 10 von 12)
erhalten bleibt, ist NICHT VERIFIZIERT:** das Anthropic-Guthaben war während der Messung erschöpft (HTTP 400
„credit balance too low“), das blinde Paarurteil und die Claude-Läufe der aktuellen Variante konnten nicht
ausgeführt werden. Daher **keine Änderung** am Standard; `LOCAL_SUMMARY_MODE` und der Auto-Modus bleiben
unverändert. Kandidat zur späteren Prüfung: der oben genannte Kompaktprompt.
