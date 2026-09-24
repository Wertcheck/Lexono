"""Tests fuer app/search/utils.py (Prompt 11)."""

from app.search.utils import build_snippet, cosine_similarity


def test_identical_vectors_have_similarity_one() -> None:
    vector = [1.0, 2.0, 3.0]
    assert cosine_similarity(vector, vector) == 1.0


def test_orthogonal_vectors_have_similarity_zero() -> None:
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == 0.0


def test_opposite_vectors_are_clamped_to_zero() -> None:
    """Negative Kosinuswerte werden auf 0 geklemmt (Score-Feld erlaubt nur
    0..1, siehe app/search/schema.py)."""
    assert cosine_similarity([1.0, 0.0], [-1.0, 0.0]) == 0.0


def test_empty_vectors_return_zero() -> None:
    assert cosine_similarity([], []) == 0.0
    assert cosine_similarity([1.0], []) == 0.0


def test_mismatched_dimensions_return_zero() -> None:
    assert cosine_similarity([1.0, 2.0], [1.0, 2.0, 3.0]) == 0.0


def test_zero_vector_returns_zero() -> None:
    assert cosine_similarity([0.0, 0.0], [1.0, 1.0]) == 0.0


def test_build_snippet_finds_query_context() -> None:
    text = "Dies ist ein langer Testtext mit der Frist 15.03.2027 mittendrin platziert."
    snippet = build_snippet(text, "15.03.2027")
    assert "15.03.2027" in snippet


def test_build_snippet_falls_back_to_truncation_without_match() -> None:
    text = "Ein Text ohne den gesuchten Begriff." * 10
    snippet = build_snippet(text, "nicht vorhanden")
    assert snippet.startswith("Ein Text ohne")
    assert len(snippet) <= 165  # Fallback-Laenge + Ellipse


def test_build_snippet_handles_empty_query() -> None:
    text = "Ein normaler Text."
    snippet = build_snippet(text, "")
    assert snippet.startswith("Ein normaler Text")


def test_build_snippet_preserves_paragraph_boundary_around_heading() -> None:
    """ECHTER FUND (realer Abnahme-Test, 13.09.): eine Ueberschrift direkt
    gefolgt vom naechsten Absatz durfte nicht zu "Schriftverkehr Mit" mit
    nur einem Leerzeichen verschmelzen - siehe dieselbe Korrektur in
    app/deadlines/extractor.py::_context_window fuer die volle Begruendung
    (falscher Namens-Kandidat in security_check.py, Punkt 6)."""
    text = "2. Schriftverkehr\nMit Schreiben vom 12.08.2026 forderte die Mieterin den Vermieter auf."
    snippet = build_snippet(text, "")
    assert "Schriftverkehr Mit" not in snippet
