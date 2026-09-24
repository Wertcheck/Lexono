"""Hilfsfunktionen für die Suche: Vektor-Ähnlichkeit und Textausschnitte."""

from __future__ import annotations

import math

_SNIPPET_CONTEXT_CHARS = 80
_SNIPPET_FALLBACK_LENGTH = 160


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine Similarity, auf [0, 1] geklemmt (negative Kosinuswerte
    werden als 0 behandelt - fuer unsere Zwecke bedeutet das schlicht
    "keine relevante Ähnlichkeit", ein Score-Feld erlaubt ohnehin nur
    Werte zwischen 0 und 1, siehe app/search/schema.py)."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    similarity = dot / (norm_a * norm_b)
    return max(0.0, min(1.0, similarity))


def build_snippet(text: str, query: str) -> str:
    """Baut einen kurzen Kontextausschnitt um den ersten Treffer von
    `query` in `text`, oder - falls kein Volltext-Treffer vorliegt (z. B.
    reiner semantischer Treffer) - die ersten Zeichen des Texts.

    ECHTER FUND (realer Abnahme-Test, 13.09., siehe dieselbe Korrektur in
    app/deadlines/extractor.py::_context_window): ein einzelnes
    Leerzeichen statt des Zeilenumbruchs verschmilzt eine Ueberschrift mit
    dem direkt folgenden Absatz zu einem einzigen, scheinbar
    zusammenhaengenden Zwei-Wort-Ausdruck - genau das Kriterium, mit dem
    app/privacy/security_check.py::_find_possible_unrecognized_names einen
    Namens-Kandidaten erkennt (nur bei GENAU einem Leerzeichen). Zwei
    Leerzeichen bewahren die Absatzgrenze fuer diese Pruefung, bleiben
    aber weiterhin ein lesbarer einzeiliger Ausschnitt."""
    normalized_text = text.replace("\n", "  ")
    if query.strip():
        index = normalized_text.lower().find(query.lower())
        if index != -1:
            start = max(0, index - _SNIPPET_CONTEXT_CHARS)
            end = min(len(normalized_text), index + len(query) + _SNIPPET_CONTEXT_CHARS)
            prefix = "…" if start > 0 else ""
            suffix = "…" if end < len(normalized_text) else ""
            return f"{prefix}{normalized_text[start:end].strip()}{suffix}"

    truncated = normalized_text.strip()[:_SNIPPET_FALLBACK_LENGTH]
    suffix = "…" if len(normalized_text.strip()) > _SNIPPET_FALLBACK_LENGTH else ""
    return f"{truncated}{suffix}"
