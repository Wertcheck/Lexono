# Erkennungslücke „<Schreiben/Brief/E-Mail …> von König“ – Ursache, Korrektur, Messung (26.10.2026)

Ausschließlich synthetische Sätze, echter Presidio/spaCy-Stack über das echte Gateway (`ClaudePrivacyGateway`).
**Keine Datenschutzfreigabe.**

## Reproduktion (vorher, HEAD `e767be3`)

Beispielsatz: „Wir bestaetigen den Eingang des Schreibens von **König** vom 16.03.2024.“ Er ging ohne Maskierung und
ohne Rückfrage hinaus.

- Betroffen waren König, Kaiser, Graf, Bauer, Sommer, Notar, Kommissar, Pfarrer, Direktor u. a.
- Gleiches Muster nach „Brief von“, „E-Mail von“, „Anruf von“, „Stellungnahme von“ und „Gutachten von“.

## Ursache (Stufe für Stufe ermittelt)

1. **NER:** spaCy erkennt kein Personen-Entity, denn das Modell kennt diese Wörter als Gattungs- bzw. Titelwörter.
2. **Rückfrage-Stufe** (`app/privacy/review.py::find_review_candidates`):
   - Die Namensrolle wird korrekt erkannt (`nach_praeposition` hinter „von“, artikellos, Singular).
   - Der Kandidat scheitert aber an `score < REVIEW_MIN_PROBE_SCORE`: Das Modell-Probing ergab **0 von 5** Rahmen. Damit
     gab es weder eine Rückfrage noch die vorsichtige Maskierung im Modus „mask“.
3. **Zweiter, verwandter Fehler (Fehlmaskierung):** Bei Fuchs, Stein, Berg, Wolf, Koch u. a. etikettierte spaCy
   „**Schreibens von Fuchs**“ als *einen* Ort.
   - Der Platzhalter verschluckte Nomen und Präposition: „Eingang des [ORT_01] vom 16.03.2024.“
   - Bei „Stein“ ging im Gateway sogar das Datum mit hinein: „Eingang des [ORT_01]“.

## Korrektur (ohne pauschale Maskierung allgemeiner Begriffe)

- **`review.py::_sender_after_communication_noun`:** Ein artikelloses Einzelwort direkt nach „<Kommunikationsnomen>
  von“ ist der **Absender**.
  - Dafür gibt es eine Rückfrage (HITL), auch ohne Modell-Probing (Rolle `absender_nach_kommunikationsnomen`).
  - Im Modus „mask“ greift wie bei allen Kandidaten die vorsichtige Pseudonymisierung.
- **Ausnahmen:**
  - Redewendungen („von Amts wegen“, „von Bedeutung“, „von Seiten“, „von Hand“ …);
  - Abstrakta (-ung, -heit, -keit …) sowie Monate und Wochentage;
  - ein Titelwort, dem der eigentliche Name oder ein Platzhalter folgt („von Notar Weber“);
  - Artikel („vom König“ = von dem König) und andere Präpositionen.
- **`presidio_ner.py::trim_sender_phrases`:** Ein Treffer, der mit „<Kommunikationsnomen> von|vom“ beginnt, wird auf
  den Absender gekürzt. Ein mitgefangenes Datum bleibt lesbar. Es wird nur gekürzt, nie entfernt; der Absender bleibt
  maskiert (fail-closed).
- **Unverändert:**
  - Fail-closed-Verhalten (ein Fehler der Kandidatensuche blockiert weiterhin);
  - HITL-Ablauf und Server-Revalidierung;
  - die Entscheidungslogik: „kein personenbezogenes Datum“ hebt nur die Rückfrage auf.

## Messung (gleiche Fälle vorher/nachher, `scripts/bench_sender_after_communication_noun.py`)

| Messgröße | vorher (`e767be3`) | nachher |
|---|---|---|
| Lecks im Modus „mask“ (nicht-interaktive Pfade), 30 Namen × 6 Wendungen | **50 / 180** | **0 / 180** |
| ungefragt im Modus „ask“ (Chat) | 50 / 180 | 0 / 180 |
| Kommunikationsnomen und Datum bleiben lesbar | 165 / 180 | 180 / 180 |
| Fehlalarme in 13 Negativsätzen | 2 („von Hand“ über das bestehende Probing; „Bayern“ als Ort) | 2 – identisch, **keine neuen** |

Regression der bestehenden Benchmarks (vorher/nachher identisch, nur Laufzeit abweichend):

- `bench_name_detection.py --variant baseline`: 1100 Namensfälle, 0,0 % Lecks; Fehlalarme 7/132 unverändert.
- `bench_role_object_negatives.py`: 0 Personen-Platzhalter, 0/12 Verluste.
- `bench_name_followups.py`: A 0/1288; **B 14/138** (siehe Restrisiko); N 4/22 – unverändert.

## Tests

- `tests/test_privacy_sender_after_communication_noun.py` (26 Tests), darunter:
  - 9 Lückenwörter (Rückfrage mit Absenderrolle);
  - Maskierung mit lesbarem Datum;
  - 4 weitere Kommunikationsnomen;
  - die Fehlmaskierung bei Fuchs/Stein/Berg;
  - 6 Negativsätze, Titelwort vor Namen, andere Präpositionen.
- Datenschutz-Regression (108 Testdateien): 2794 bestanden, 1 übersprungen, 8 xfailed, 1 fehlgeschlagen.
  - Der Fehlschlag betrifft `test_s80_benchmark_driver::test_privacy_finding_in_payload_aborts_before_sending`. Er ist
    reihenfolgeabhängig: Einzeln besteht der Test mit und ohne Änderung, und `test_s80` + `test_s81` bestehen zusammen
    (86/86).
  - Er hängt nicht mit dieser Korrektur zusammen.

## Verbleibendes Restrisiko (quantifiziert, nicht behoben)

- **Nachname nach Artikel ohne Einführung:** „Der König hat am 12.03.2026 angerufen“ bleibt in **14 von 138** Fällen
  lesbar (`bench_name_followups.py`, Gruppe B: Schuster, Wagner, Bauer, König, Kaiser, Vogel, Hahn, Winter, Sommer,
  Horn, Graf).
  - Die Artikel-Lesart ist dort grammatisch gleichwertig. Eine Regel würde „Der Bauer hat das Feld bestellt“
    fehlmaskieren.
  - Bleibt eine Owner-Entscheidung, etwa eine Rückfrage bei einem bekannten Akten-Nachnamen.
- Die übrigen Restlecks und Fehlalarme früherer Messungen sind hier nicht neu bewertet.
