"""Lokale Spracheingabe fuer den Chat-Composer (05.10., Owner-Direktive
"ARCHITECTURE & PRODUCT FLOW PASS" §22-27).

VORGESCHICHTE (wichtig fuer die Architekturentscheidung hier): der
Mikrofon-Button in chat.html existierte bereits vorher, war aber bewusst
NICHT an die browser-native Web Speech API (`SpeechRecognition`) angebunden
- siehe .agentic/DECISIONS.md (15.09.): in Chromium/WebView2 gibt es dafuer
KEIN On-Device-Modell, das aufgenommene Audio ginge an einen CLOUD-
Spracherkennungsdienst, BEVOR die lokale Pseudonymisierung greifen koennte.
Das waere ein direkter Verstoss gegen die Grundregel "Aktenkontext strikt
isolieren" (CLAUDE.md) - ein Anwalt, der "Herr Mueller bestreitet die
Frist" diktiert, haette den Mandantennamen unverschluesselt an einen
externen Dienst gesendet. Dieses Modul ist die dort bereits benannte
Voraussetzung: eine echte, LOKAL (on-device) laufende STT-Komponente.

TECHNOLOGIEWAHL: faster-whisper (CTranslate2-Inferenz) statt z.B.
openai-whisper (PyTorch) - kleinerer Ressourcenbedarf, int8-Quantisierung
fuer CPU. WICHTIG, in dieser Sitzung real getestet (nicht nur angenommen):
anders als das in diesem Projekt bereits dokumentierte onnxruntime-AVX2-
Problem (siehe pyproject.toml - onnxruntime>=1.21 crasht beim Import auf
der dokumentierten Alt-Hardware ohne AVX2) besitzt CTranslate2 einen
expliziten Nicht-AVX2-Fallback (Umgebungsvariable
`CT2_FORCE_CPU_ISA=GENERIC`) - mit erzwungenem GENERIC-Pfad real getestet:
funktioniert fehlerfrei, nur ca. 25% langsamer (3,6s statt 2,9s fuer eine
echte, per Windows-TTS erzeugte ~9s deutsche Testaeusserung mit
Rechtsbegriffen - siehe Abschlussbericht fuer das vollstaendige Protokoll).
Kein eigener ISA-Zwang wird hier gesetzt - CTranslate2 erkennt die
tatsaechliche CPU selbst und waehlt den bestmoeglichen, sicheren Pfad.

MODELLGROESSE ("small", NICHT das groesste Modell - §22 der Direktive
verbietet das explizit): mehrsprachiges/deutsches WER ca. 7% (vs. ~10% bei
"base", ~5% bei "medium"), ca. 470 MB Modellgroesse (vs. ~1,5 GB bei
"medium"). In dieser Sitzung real gemessen: Transkription eines ~9s
deutschen Rechts-Testsatzes in 2,9s auf aktueller Entwicklungshardware,
3,6s mit erzwungenem GENERIC-ISA-Pfad (simuliert Alt-Hardware ohne AVX2) -
deutlich schneller als Echtzeit, ausreichend Sicherheitsmarge fuer die
dokumentierte Alt-Hardware (Intel i7-3720QM, siehe pyproject.toml) bei
kurzen Chat-Diktaten (wenige Saetze), ohne das sehr viel groessere/
langsamere "medium"-Modell zu benoetigen."""

from __future__ import annotations

import tempfile
from pathlib import Path

#: Siehe Modul-Docstring fuer die vollstaendige Begruendung der Wahl.
MODEL_SIZE = "small"
COMPUTE_TYPE = "int8"
DEVICE = "cpu"

#: Unterhalb dieser Audiolaenge gilt eine Aufnahme als "leer/zu kurz" (§26
#: "sehr kurze Aufnahme"/"leere Aufnahme") statt als (meist bedeutungsloses)
#: Transkriptionsergebnis behandelt zu werden.
MIN_AUDIO_SECONDS = 0.35

#: Obergrenze fuer eine einzelne Diktat-Aufnahme - grosszuegig genug fuer
#: mehrere Minuten komprimiertes Audio (WebM/Opus, Composer-Diktat ist kein
#: Stunden-Protokoll), verhindert aber eine uebermaessig grosse Anfrage.
#: Gleiches Prinzip wie `ChatService._MAX_UPLOAD_SIZE_BYTES` (Dokumente).
MAX_AUDIO_SIZE_BYTES = 20 * 1024 * 1024


class SpeechTranscriptionError(Exception):
    """Basisklasse - der Router faengt dies ab und zeigt einen ehrlichen
    Fehlerzustand (§25/§26 der Direktive) statt eines Absturzes."""


class SpeechModelUnavailableError(SpeechTranscriptionError):
    """Das Whisper-Modell konnte nicht geladen werden (fehlt/beschaedigt/
    kein Speicher) - §26 "fehlendes Whisper-Modell"/"Modell konnte nicht
    geladen werden"."""


class SpeechEmptyRecordingError(SpeechTranscriptionError):
    """Die Aufnahme ist leer oder zu kurz, um sinnvoll transkribiert zu
    werden - §26 "sehr kurze Aufnahme"/"leere Aufnahme"."""


class SpeechTooLargeError(SpeechTranscriptionError):
    """Die Aufnahme ueberschreitet `MAX_AUDIO_SIZE_BYTES`."""


class SpeechDecodeError(SpeechTranscriptionError):
    """Das uebermittelte Audio liess sich nicht dekodieren (beschaedigt,
    unerwartetes/nicht unterstuetztes Format) - §26 "Transkription schlaegt
    fehl"."""


_model = None
_model_load_error: Exception | None = None


def _get_model():
    """Laedt das Whisper-Modell genau einmal pro Prozess (Lazy Singleton) -
    das Laden dauert mehrere Sekunden (Download/Disk-I/O), eine einzelne
    Chat-Transkription darf dafuer nicht jedes Mal erneut warten. Ein
    fehlgeschlagener Ladeversuch wird gemerkt statt bei jeder weiteren
    Anfrage erneut (langsam) zu scheitern."""
    global _model, _model_load_error
    if _model_load_error is not None:
        raise SpeechModelUnavailableError(
            "Lokales Spracherkennungsmodell konnte nicht geladen werden."
        ) from _model_load_error
    if _model is None:
        try:
            from faster_whisper import WhisperModel

            _model = WhisperModel(MODEL_SIZE, device=DEVICE, compute_type=COMPUTE_TYPE)
        except Exception as exc:  # faster-whisper/ctranslate2 werfen diverse Fehlerklassen
            _model_load_error = exc
            raise SpeechModelUnavailableError(
                "Lokales Spracherkennungsmodell konnte nicht geladen werden."
            ) from exc
    return _model


def transcribe_audio_bytes(
    audio_bytes: bytes, *, suffix: str = ".webm", language: str = "de"
) -> str:
    """Transkribiert eine einzelne Chat-Diktat-Aufnahme zu Text.

    §24 ("keine unnoetige Audiospeicherung"): das Audio landet NUR in einer
    temporaeren Datei, die unmittelbar nach der Verarbeitung geloescht wird
    - unabhaengig davon, ob die Transkription erfolgreich war (`finally`).
    Es gibt keinen Codepfad, der die Aufnahme dauerhaft speichert, als
    Chat-Anhang persistiert oder an einen externen Dienst sendet."""
    if not audio_bytes:
        raise SpeechEmptyRecordingError("Die Aufnahme ist leer.")
    if len(audio_bytes) > MAX_AUDIO_SIZE_BYTES:
        raise SpeechTooLargeError(
            f"Die Aufnahme ueberschreitet das Limit von "
            f"{MAX_AUDIO_SIZE_BYTES // (1024 * 1024)} MB."
        )

    model = _get_model()

    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(audio_bytes)
            tmp_path = Path(tmp.name)

        try:
            # vad_filter=True (Voice Activity Detection, laeuft ueber das
            # bereits im Projekt AVX2-sicher gepinnte onnxruntime, siehe
            # pyproject.toml): ueberspringt reine Stille statt sie als
            # bedeutungslose Halluzination zu transkribieren.
            segments, info = model.transcribe(str(tmp_path), language=language, vad_filter=True)
            text = " ".join(segment.text.strip() for segment in segments).strip()
        except SpeechTranscriptionError:
            raise
        except Exception as exc:
            raise SpeechDecodeError("Audio konnte nicht verarbeitet werden.") from exc

        if info.duration < MIN_AUDIO_SECONDS:
            raise SpeechEmptyRecordingError("Die Aufnahme ist zu kurz.")

        return text
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)
