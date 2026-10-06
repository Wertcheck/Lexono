"""Tests für app/chat/speech.py - lokale Spracheingabe im Chat-Composer
(05.10., Owner-Direktive "ARCHITECTURE & PRODUCT FLOW PASS" §22-27).

Bewusst mit einem FAKE-Modell statt des echten (heruntergeladenen)
Whisper-Modells - ein echter Modell-Download/eine echte Transkription
wurde in dieser Sitzung manuell verifiziert (siehe Abschlussbericht fuer
das vollstaendige Protokoll mit realem, per Windows-TTS erzeugtem
deutschen Audio), gehoert aber nicht in die automatische Testsuite: ein
frischer `pytest`-Lauf darf nicht bei jedem Durchlauf ein ~470-MB-Modell
herunterladen (gleiches Prinzip wie `FakeEmbeddingProvider` fuer
fastembed/onnxruntime an anderer Stelle im Projekt)."""

from __future__ import annotations

import faster_whisper
import pytest

import app.chat.speech as speech_module
from app.chat.speech import (
    SpeechDecodeError,
    SpeechEmptyRecordingError,
    SpeechModelUnavailableError,
    SpeechTooLargeError,
    transcribe_audio_bytes,
)


@pytest.fixture(autouse=True)
def _reset_model_singleton() -> None:
    """Das Modul haelt das geladene Modell als Prozess-weiten Singleton
    (siehe `_get_model`-Docstring) - zwischen Tests zuruecksetzen, sonst
    wuerde ein Fake aus einem Test in den naechsten durchsickern."""
    speech_module._model = None
    speech_module._model_load_error = None
    yield
    speech_module._model = None
    speech_module._model_load_error = None


class _FakeSegment:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeInfo:
    def __init__(self, duration: float) -> None:
        self.duration = duration


class _FakeModel:
    def __init__(self, *, segments: list[str], duration: float, raises: Exception | None = None) -> None:
        self._segments = segments
        self._duration = duration
        self._raises = raises

    def transcribe(self, path: str, *, language: str, vad_filter: bool):
        if self._raises is not None:
            raise self._raises
        return (iter(_FakeSegment(text) for text in self._segments), _FakeInfo(self._duration))


def test_empty_bytes_raise_empty_recording_error() -> None:
    with pytest.raises(SpeechEmptyRecordingError):
        transcribe_audio_bytes(b"")


def test_oversized_audio_raises_too_large_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(speech_module, "MAX_AUDIO_SIZE_BYTES", 10)
    with pytest.raises(SpeechTooLargeError):
        transcribe_audio_bytes(b"x" * 20)


def test_successful_transcription_joins_segment_text() -> None:
    speech_module._model = _FakeModel(segments=["Bitte prüfen Sie", "die Kündigung."], duration=4.0)
    text = transcribe_audio_bytes(b"\x00" * 2000)
    assert text == "Bitte prüfen Sie die Kündigung."


def test_short_duration_raises_empty_recording_error() -> None:
    speech_module._model = _FakeModel(segments=["Hm."], duration=0.1)
    with pytest.raises(SpeechEmptyRecordingError):
        transcribe_audio_bytes(b"\x00" * 2000)


def test_decode_failure_is_wrapped_as_speech_decode_error() -> None:
    speech_module._model = _FakeModel(segments=[], duration=0.0, raises=RuntimeError("kaputte Datei"))
    with pytest.raises(SpeechDecodeError):
        transcribe_audio_bytes(b"\x00" * 2000)


def test_model_load_failure_raises_speech_model_unavailable_and_is_cached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """§26 "fehlendes Whisper-Modell"/"Modell konnte nicht geladen werden" -
    UND: ein einmal fehlgeschlagener Ladeversuch wird gemerkt (kein
    wiederholter, langsamer Download-/Ladeversuch pro Anfrage)."""
    call_count = 0

    def _raise(*args: object, **kwargs: object) -> None:
        nonlocal call_count
        call_count += 1
        raise RuntimeError("Modell nicht gefunden")

    monkeypatch.setattr(faster_whisper, "WhisperModel", _raise)

    with pytest.raises(SpeechModelUnavailableError):
        transcribe_audio_bytes(b"\x00" * 2000)
    with pytest.raises(SpeechModelUnavailableError):
        transcribe_audio_bytes(b"\x00" * 2000)

    assert call_count == 1


def test_transcription_never_persists_a_file_after_success(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """§24 "keine unnoetige Audiospeicherung" - die temporaere Datei muss
    nach der Verarbeitung tatsaechlich wieder verschwunden sein."""
    import tempfile

    created_paths: list[str] = []
    original_named_temp_file = tempfile.NamedTemporaryFile

    def _tracking_named_temp_file(*args: object, **kwargs: object):
        handle = original_named_temp_file(*args, **kwargs)
        created_paths.append(handle.name)
        return handle

    monkeypatch.setattr(tempfile, "NamedTemporaryFile", _tracking_named_temp_file)
    speech_module._model = _FakeModel(segments=["Text."], duration=2.0)

    transcribe_audio_bytes(b"\x00" * 2000)

    assert len(created_paths) == 1
    from pathlib import Path

    assert not Path(created_paths[0]).exists()


def test_transcription_removes_temp_file_even_on_decode_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    import tempfile
    from pathlib import Path

    created_paths: list[str] = []
    original_named_temp_file = tempfile.NamedTemporaryFile

    def _tracking_named_temp_file(*args: object, **kwargs: object):
        handle = original_named_temp_file(*args, **kwargs)
        created_paths.append(handle.name)
        return handle

    monkeypatch.setattr(tempfile, "NamedTemporaryFile", _tracking_named_temp_file)
    speech_module._model = _FakeModel(segments=[], duration=0.0, raises=RuntimeError("kaputt"))

    with pytest.raises(SpeechDecodeError):
        transcribe_audio_bytes(b"\x00" * 2000)

    assert len(created_paths) == 1
    assert not Path(created_paths[0]).exists()
