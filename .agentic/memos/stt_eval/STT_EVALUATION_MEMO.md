# LEXONO LOKALE SPRACHERKENNUNG — EVALUATIONSMEMO (15.09.)

Reiner Evaluationsauftrag (Nutzerdirektive, Feld B). **Keine Implementierung,
keine Anbindung an das Produkt.** Ergänzt den bestehenden Befund vom
selben Tag, dass der Mikrofon-Button der Entwurfs-Anweisungsleiste bis
eben die native (cloud-basierte) Browser-Spracherkennung nutzte und jetzt
konsistent mit dem Chat-Composer deaktiviert ist (siehe DECISIONS.md,
15.09.) — DAS war ein realer Fund/Fix, DIES hier ist die vorbereitende
Evaluation für eine spätere echte lokale Anbindung.

## Referenzhardware (Vorgabe)

Lenovo ThinkPad T14 Gen 2, Intel i5, 16 GB RAM, Windows 11 Pro.

**Ehrliche Einschränkung:** die folgenden Messungen liefen NICHT auf
dieser Referenzmaschine, sondern auf der aktuellen Entwicklungsmaschine.
Sie sind als **relativer Vergleich zwischen den Kandidaten** belastbar
(identisches Audiomaterial, identische Maschine, direkt vergleichbar),
aber die ABSOLUTEN Latenzwerte müssen vor einer Entscheidung noch einmal
auf der echten Referenzhardware nachgemessen werden.

**Zweite ehrliche Einschränkung:** das Testaudio ist NICHT von einem
Menschen gesprochen, sondern per Windows-SAPI (`Microsoft Hedda Desktop`,
de-DE) synthetisiert (kein Mikrofon in dieser Umgebung verfügbar). Das
ist eine echte, faire Vergleichsgrundlage zwischen den Engines (dieselbe
Audiodatei für beide), aber KEIN Ersatz für einen Test mit echter,
menschlicher Anwaltsstimme (Dialekt, Sprechtempo, Pausen, Hintergrundgeräusch
fehlen komplett).

## Methode

5 realistische Testdiktate erzeugt (kanzlei-/steuertypisch: Begrüßung,
Mandantenname+Aktenzeichen, Paragraph+Frist, Betrag+Datum, langer
Schriftsatz-Entwurf) — siehe `audio/reference_texts.json`. Beide Engines
in einer isolierten venv installiert (NICHT im Projekt-`.venv`), reale
Transkription, reale Zeitmessung (cold = erster Modell-Load im Prozess,
warm = zweiter Load im selben Prozess).

## GEMESSEN: Vosk (`vosk-model-small-de-0.15`)

| | |
|---|---|
| Paket-Download | 51,6 MB (2 native DLLs: `libvosk.dll` 25,2 MB, `libstdc++-6.dll` 25,4 MB) |
| Modell (klein, de) | 44,3 MB Download, 91,2 MB entpackt |
| Lizenz | Apache 2.0 |
| Modell-Ladezeit | 0,45 s (cold), 0,44 s (warm) |
| Transkription je Satz | 0,40 s – 1,32 s (durchweg unter 1,5 s, auch für den langen Diktat-Satz) |
| PyInstaller-Hook mitgeliefert | Nein — braucht explizite `--collect-all vosk` bzw. Spec-Eintrag (derselbe bereits etablierte Umgang wie bei spaCy im Projekt, siehe `pyproject.toml`) |

**Reale Transkriptionsfehler (Auszug, vollständig in `vosk_results.json`):**
- "gemäß" → "gamer es" (falsches Wort, Fachbegriff)
- "dreihundertfünfundfünfzig" → "dreihundert fuhr in fund fünfzig" (Zahlwort zerlegt/verfälscht — **§ 355 wird unlesbar**)
- "zwölftausend" → "zwölf punkte" (Betrag falsch)
- "fällig" → "feierlich" (falsches Wort)
- Keine Groß-/Kleinschreibung, keine Satzzeichen (Modell liefert nur Rohtext)

## GEMESSEN: faster-whisper (`Systran/faster-whisper-base`, CTranslate2, INT8, CPU)

| | |
|---|---|
| Paket-Download | faster-whisper (klein) + ctranslate2 59,8 MB (`ctranslate2.dll` 56,6 MB) |
| Modell (base, int8) | 141,0 MB im HuggingFace-Cache |
| Lizenz | MIT (faster-whisper UND ctranslate2) |
| Modell-Ladezeit | 50,2 s (cold, **inkl. Erstdownload/-konvertierung des Modells** — reiner Ladevorgang bei bereits vorhandenem Modell ist das nicht) |
| Modell-Ladezeit warm | 0,74 s |
| Transkription je Satz | 0,71 s – 1,26 s (vergleichbar mit Vosk) |
| PyInstaller-Hook mitgeliefert | Nein — dieselbe Situation wie Vosk |

**Reale Transkriptionsfehler (Auszug, vollständig in `faster_whisper_results.json`):**
- "Hallo, wie kann ich Ihnen helfen?" → **exakt korrekt**, inkl. Groß-/Kleinschreibung und Satzzeichen
- "gemäß Paragraph" → "Gehmer-Sparagraf" (beide Wörter falsch)
- Paragraph 355 → **"350"** (falsche Zahl — Whisper wandelt gesprochene Zahlen automatisch in Ziffern um, hier aber FALSCH)
- Betrag 12.350 € → **"12.310"** (falsch, Abweichung 40 €)
- Jahr 2027 → **"2007"** (falsch, Abweichung 20 Jahre)
- "fällig" → "fahrerlich" (falsches Wort, derselbe Fehlertyp wie bei Vosk)

**Wichtige Einordnung, nicht nur Zahlenvergleich:** faster-whisper liefert
DEUTLICH natürlicheren, besser lesbaren Text (Satzzeichen, Großschreibung,
automatische Ziffern-Umwandlung) — aber genau diese automatische
Ziffern-Umwandlung erzeugt bei falscher Erkennung einen TEXT, der
KORREKT AUSSIEHT, obwohl er es nicht ist ("12.310" wirkt wie ein
plausibler Eurobetrag). Ein Anwalt, der das Transkript nur überfliegt,
merkt einen falsch formatierten Vosk-Fehler ("zwölf punkte
dreihundertfünfzig") eher als Fehler als einen sauber formatierten, aber
falschen Whisper-Fehler ("12.310"). Für sicherheitskritische Zahlen
(Fristen, Beträge, Aktenzeichen) ist das ein ernstzunehmender
Qualitätsaspekt, kein reiner Geschmacksfall.

## DOKUMENTIERT, NICHT SELBST GEMESSEN

### OpenAI Whisper (`openai-whisper`-Paket, reines PyTorch)
Bewusst NICHT installiert/getestet heute Nacht — Begründung, keine
Verweigerung: das Paket zieht `torch` nach (auf Windows CPU-Wheel ca.
1,5–2 GB), und CPU-only-PyTorch-Inferenz ist für Whisper-Modelle
gut dokumentiert 3–5× langsamer als dieselbe Modellgröße über das
CTranslate2-Backend (genau der Umbau, den `faster-whisper` selbst als
Daseinsberechtigung angibt, dort mit eigenen Benchmarks belegt). Sowohl
Paketgröße als auch CPU-Laufzeit widersprechen direkt der Vorgabe "nicht
für High-End-Hardware optimieren, sondern für die 16-GB-Referenzklasse".
**Empfehlung: aus der engeren Auswahl ausschließen**, es sei denn, ein
späterer echter Test widerlegt das.

### whisper.cpp
Nicht installiert (bräuchte entweder einen vorkompilierten Windows-Build
oder einen lokalen C++-Kompilierschritt — beides über den heutigen
Evaluationsrahmen hinaus). Dokumentierte Eckdaten (Projekt-README/Releases):
reine C/C++-Implementierung ohne Python-ML-Abhängigkeiten zur Laufzeit,
quantisierte GGML-Modelle (`ggml-base.bin` ≈ 148 MB, `ggml-small.bin` ≈
488 MB), MIT-Lizenz, aktiv für AVX2/ARM-NEON optimiert. Von den vier
Kandidaten vermutlich der PyInstaller-freundlichste (ein einzelnes
kompiliertes Binary/eine DLL, kein Python-Paket mit nativen
Abhängigkeiten wie bei Vosk/ctranslate2) — das ist aber eine Einschätzung
aus der Dokumentation, kein eigener Messwert, und muss vor einer
Entscheidung real nachgeprüft werden.

## Zwischenfazit (Evaluation, KEINE Implementierungsempfehlung)

1. **Beide real getesteten Kandidaten sind auf dieser Hardware-Klasse
   grundsätzlich schnell genug** (Transkription < 1,5 s je Satz, warmer
   Modell-Load < 1 s) — Latenz ist NICHT der limitierende Faktor.
2. **Beide haben ein reales, für eine Kanzlei relevantes Genauigkeitsproblem
   bei gesprochenen Zahlen** (Paragraphen, Beträge, Jahreszahlen). Das
   bestätigt unabhängig, warum die Nutzervorgabe "Der Benutzer soll das
   Transkript vor dem Absenden prüfen und korrigieren können. Keine
   automatische Übermittlung ohne Benutzeraktion" nicht nur eine
   UX-Vorsichtsmaßnahme, sondern für DIESE Fehlerklasse (falsch aber
   plausibel aussehende Zahlen) eine echte Notwendigkeit ist.
3. **Vosk ist kleiner** (Modell 91 MB vs. 141 MB, kein Download-Overhead
   beim ersten Start), **faster-whisper liefert besser lesbaren Text**
   (Interpunktion/Großschreibung), macht dafür bei falschen Zahlen einen
   TÄUSCHENDEREN Fehler.
4. Vor einer echten Anbindung fehlen noch: (a) Messung auf der echten
   Referenzhardware, (b) Test mit echter menschlicher Stimme statt
   TTS-Audio, (c) ein realer whisper.cpp-Vergleich, (d) ein größeres
   Whisper-Modell (`small` statt `base`) zur Prüfung, ob die
   Zahlenfehler mit mehr Modellkapazität abnehmen, (e) eine
   PyInstaller-Bündelungsprobe (beide Pakete brauchen explizite
   `--collect-all`-Einträge, analog zum bestehenden spaCy-Umgang im
   Projekt — technisch machbar, aber nicht heute Nacht verifiziert).

**Kein Implementierungsvorschlag** — Nutzervorgabe §12 ("Nicht sofort
implementieren") bewusst eingehalten.
