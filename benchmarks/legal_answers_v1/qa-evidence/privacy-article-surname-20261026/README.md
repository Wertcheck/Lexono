# Restlecks „Der König hat angerufen“ – Untersuchung, enge Korrektur, Neuquantifizierung (26.10.2026)

Ausschließlich synthetische Sätze, echtes Gateway (Presidio/spaCy). Vergleich immer mit identischen Fällen: vorher =
sauberer Worktree `f42e716`, nachher = Arbeitsstand dieses Commits. **Keine Datenschutzfreigabe.**

## Ursache

Für „Der <Wort> hat … angerufen/unterschrieben“ verlangt die Artikel-Lesart (`review._is_definite_article_name_reading`)
ein Personennomen und den stärksten Namensrahmen des Modells:

- Winter, Sommer, Hahn und Vogel gelten nicht als Personennomen und wurden schon vorher verworfen.
- König, Kaiser und Graf sind Personennomen, das Modell liest sie aber in keinem Rahmen als Namen.

## Prüfung der fachlichen Grenze

Die Tests enthalten eine **dokumentierte Entscheidung**: „Der Bauer hat die Pacht gezahlt“ ist ein Negativfall und muss
lesbar bleiben (`tests/test_privacy_feminine_article_names.py`, als bewusst offene Grenze markiert).

- Eine erste Fassung der Regel maskierte auch im Modus „mask“. Sie hätte diesen Negativfall überstimmt: im
  Folge-Benchmark N 4 → 5 verloren, „Der Richter Zweigelt“ fehlmaskiert, Testfehler.
- Eine zweite Fassung (nur Rückfrage) verdrängte bestehende Maskierungen: P-Lecks im Modus „mask“ 38 → 50, Folge-Benchmark
  B 14 → 19.
- Beide Fassungen wurden **verworfen**.

## Korrektur (dritte Fassung, eng)

`review._surname_noun_as_party` gilt nur, wenn alle drei Bedingungen erfüllt sind:

- „der“ steht direkt davor;
- das Wort ist ein häufiger deutscher Nachname, der zugleich Gattungswort ist (feste Liste);
- es ist Subjekt einer typischen Parteihandlung (unterschreiben, anrufen, widersprechen, zahlen, kündigen …).

Kein Titel vor einem Namen („Der Richter Zweigelt“).

**Wirkung:**

- **Nur als Rückfall:** Die Regel greift nur dort, wo die bestehende Logik das Wort verwerfen würde. Auf dem bestehenden
  Weg gefundene Begriffe behalten ihr Verhalten, einschließlich der Maskierung im Modus „mask“.
- **Wo der Durchlass zur bestehenden Prüfung reicht**, maskiert diese wie bisher (Winter, Vogel, Hahn, Wagner).
- **Sonst nur Rückfrage im Chat** (`ReviewCandidate.ask_only`), keine stille Maskierung. Im Modus „mask“ entfernt der
  Gateway diese Kandidaten, das Verhalten dort bleibt wie vorher. Der dokumentierte Negativfall bleibt lesbar (neuer Test).
- Fail-closed, Server-Revalidierung und Entscheidungslogik sind unverändert. „Kein personenbezogenes Datum“ hebt nur die
  Rückfrage auf (Test).

## Messung vorher / nachher

**`scripts/bench_article_surname_nouns.py`** (neu; 20 Wörter, 4 Partei-Rahmen, Gegengruppen):

| Gruppe | vorher | nachher |
|---|---|---|
| P: Name als handelnde Partei, ungefragt im Chat (Modus „ask“) | 38 / 80 | **0 / 80** |
| P: Lecks im Modus „mask“ (nicht-interaktive Pfade) | 38 / 80 | **26 / 80** (offen: König, Kaiser, Graf, Bauer, Sommer) |
| G: gleiche Wörter als Gattungswort, still maskiert | 10 / 20 | 10 / 20 (unverändert, vorbestehend durch die Namenserkennung) |
| G: gefragt | 2 / 20 | 2 / 20 |
| G: Übermaskierung anderer Wörter („Brandenburg“ als Ort) | 1 | 1 |
| M: mehrdeutig („übergeben“), gefragt | 3 / 20 | 3 / 20 |
| R: Rollenwort + Parteihandlung, still maskiert (mask) | 4 / 8 | **4 / 8 – keine neue Fehlmaskierung** |
| R: Rollenwort + Parteihandlung, gefragt (ask) | 2 / 8 | **6 / 8 – Kosten: +4 Rückfragen** (König, Kaiser, Bauer, Schuster) |

**Bestehende Benchmarks:**

- `bench_name_followups.py`: Gruppe B 14 → **10 / 138**; A 0/1288 und N 4/22 unverändert. Der Negativfall „Der Bauer hat
  die Pacht …“ bleibt lesbar.
- Identisch mit vorher (Laufzeitspalten ausgenommen):
  - `bench_name_detection.py` (mask und ask), `bench_role_object_negatives.py`, `bench_article_noun_persons.py`;
  - `bench_sender_after_communication_noun.py`;
  - `bench_realistic_documents.py` (DOCX/PDF/Scan aus `make_realistic_privacy_docs.py`: 0 Lecks, 0 verlorene Fachwerte,
    gleiche Rückfragen).

## Erneute Quantifizierung der bekannten Restleck-Klassen

`bench_name_detection.py --variant baseline`, 1100 synthetische Namensfälle in 10 Kontextgruppen:

| Modus | Lecks gesamt | nach Klasse | Rückfragen / Blocks | Fehlalarme (132 gewöhnliche Wörter) |
|---|---|---|---|---|
| `off` (nur automatische Erkennung, ohne Unsicherheitsstufe) | **3,3 %** | Tabelle 6,4 %; zwei Namen 6,4 %; Überschrift 5,5 %; Feldanfang, Zeilenumbruch, Satzanfang je 3,6 %; Satzmitte 2,7 %; Anrede 0,9 %; Titel und Vollname 0 % | – | 1 (Verwalter) |
| `mask` (Standard nicht-interaktiv) | **0,0 %** | alle Gruppen 0 % | 0 | 7 (Gericht, Protokoll, Rücktritt, Schadensersatz, Urteil, Verwalter, Verzug) |
| `ask` (Chat) | **0,0 %** | alle Gruppen 0 % | 37 Rückfragen, 3,4 % Blocks bis zur Entscheidung | 7 (davon 6 als Block) |

**Adversarialer Benchmark** `bench_pseudonymization.py`:

- E2E 7/7 wie erwartet, 0 Lecks, Fachwerte 4/4 erhalten (`bench_pseudonymization_v1_results_nachher.json`).
- Die eingecheckte `benchmarks/pseudonymization_v1/results.json` wurde nicht verändert.

**Einordnung:**

- Die Klassen aus PRIVACY_REVIEW_ARCHITECTURE.md §1 bestehen in der reinen Erkennung weiter, mit 3,3 % statt früher 3,8 %
  im Kontextsatz: (a) Gattungswort als Nachname, (b) seltene Namen in Tabellen, Überschriften und nach Umbrüchen.
- Sie werden erst durch die Unsicherheitsstufe abgefangen, und zwar auf Kosten von Fehlalarmen bei gewöhnlichen
  Rechtswörtern (7/132).
- Die Zahlen gelten nur für diesen synthetischen Korpus. Ein Nachweis für echte Akten fehlt.

## Verbleibend offen

- Modus „mask“: König, Kaiser, Graf, Bauer und Sommer als handelnde Partei mit Artikel bleiben lesbar (26/80). Dahinter
  steht die bewusste Entscheidung für den Negativfall „Der Bauer hat die Pacht gezahlt“.
- Eine Maskierung auch dort ist eine **Owner-Entscheidung**:
  - Option A: so lassen, nur Rückfrage im Chat.
  - Option B: im Modus „mask“ vorsichtig maskieren. Kosten: R 4 → 8 von 8 still maskiert; Folge-Benchmark N 4 → 5.
- Tests: `tests/test_privacy_article_surname_party.py`; angepasst `tests/test_privacy_feminine_article_names.py` (Winter
  und Vogel jetzt geschützt; Bauer bleibt die dokumentierte Grenze; neuer Test für den lesbaren Negativfall).
