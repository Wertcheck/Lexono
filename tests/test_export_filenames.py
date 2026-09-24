"""Tests für app/export/filenames.py::safe_download_filename (19.09.,
echter Fund - siehe dortiger Moduldocstring: ein Halbgeviertstrich im
Aktentitel wurde bisher stillschweigend geloescht statt ersetzt, Ergebnis
ein Dateiname mit doppeltem Leerzeichen)."""

from __future__ import annotations

from app.export.filenames import safe_download_filename


def test_replaces_en_dash_with_hyphen_instead_of_dropping_it() -> None:
    result = safe_download_filename("Einspruch Steuerbescheid 2025 – sabine")
    assert result == "Einspruch Steuerbescheid 2025 - sabine"
    assert "  " not in result


def test_removes_unsafe_characters() -> None:
    result = safe_download_filename('Akte "Test"/<Sonderzeichen>?.txt')
    assert "/" not in result
    assert '"' not in result
    assert "<" not in result and ">" not in result


def test_keeps_umlauts_and_normal_characters() -> None:
    result = safe_download_filename("Müller & Söhne GmbH - Vertragsprüfung")
    assert result == "Müller  Söhne GmbH - Vertragsprüfung"


def test_falls_back_when_everything_gets_stripped() -> None:
    assert safe_download_filename("///???", fallback="Schriftsatz") == "Schriftsatz"


def test_falls_back_on_empty_string() -> None:
    assert safe_download_filename("", fallback="Dokument") == "Dokument"


def test_default_fallback_is_dokument() -> None:
    assert safe_download_filename("///") == "Dokument"
