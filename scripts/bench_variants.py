"""Experimentvarianten fuer scripts/bench_name_detection.py (Monkeypatches, NICHT produktiv).

baseline    unveraenderter Stand
neutral_*   andere Neutralisierung der internen Gateway-Marker (nur Erkennungsfassung)
"""

from __future__ import annotations

import re

from app.privacy import presidio_ner as pn

_MARK = pn._INTERNAL_MARKER_PATTERN
_PH = pn._INTERNAL_PLACEHOLDER_PATTERN


def _dot_nl(text: str) -> str:
    text = re.sub(r"(@@[A-Z_]+@@)\n", lambda m: "." + " " * (len(m.group()) - 1), text)
    text = _MARK.sub(lambda m: "." + " " * (len(m.group()) - 1), text)
    return _PH.sub(lambda m: " " * len(m.group()), text)


def _dot_nl_reflow(text: str) -> str:
    text = re.sub(r"(?<=[a-zäöüß,])\n(?=[A-ZÄÖÜ])", " ", text)  # weicher Umbruch mitten im Satz (laengengleich)
    return _dot_nl(text)


def _spaces_nl(text: str) -> str:
    text = re.sub(r"(@@[A-Z_]+@@)\n", lambda m: " " * len(m.group()), text)
    text = _MARK.sub(lambda m: " " * len(m.group()), text)
    return _PH.sub(lambda m: " " * len(m.group()), text)


def apply(name: str) -> None:
    if name == "baseline":
        return
    if name == "neutral_dot_nl":
        pn._neutralize_internal_tokens = _dot_nl
        return
    if name == "neutral_spaces_nl":
        pn._neutralize_internal_tokens = _spaces_nl
        return
    if name == "propn":
        apply_propn()
        return
    if name == "reflow_propn":
        pn._neutralize_internal_tokens = _dot_nl_reflow
        apply_propn()
        return
    if name == "dot_propn":
        pn._neutralize_internal_tokens = _dot_nl
        apply_propn()
        return
    raise SystemExit(f"unbekannte Variante {name}")
    raise SystemExit(f"unbekannte Variante {name}")


# --- Zweitsignal: PROPN ohne Entitaetsetikett (Prototyp, spaeter ggf. produktiv in presidio_ner) -----------------------
import os

ROLE = os.environ.get("BENCH_ROLE", "strict")
OOV = os.environ.get("BENCH_OOV", "0") == "1"


def propn_spans(text: str):
    from app.privacy.detectors import DetectedSpan  # noqa: PLC0415

    analyzer = pn._get_analyzer_engine()
    neutral = pn._neutralize_internal_tokens(text)
    doc = analyzer.nlp_engine.nlp["de"](neutral)
    out = []
    for tok in doc:
        t = tok.text
        oov_noun = OOV and tok.pos_ == "NOUN" and tok.is_oov and (tok.i == 0 or doc[tok.i - 1].pos_ not in ("DET", "ADJ", "NUM", "PRON"))
        if (tok.pos_ != "PROPN" and not oov_noun) or len(t) < 3 or not t[0].isupper() or not t.isalpha():
            continue
        if t.isupper():
            continue  # Abkuerzungen (BGB, VOB)
        if t.lower() in pn._NEVER_ENTITY_WORDS:
            continue
        role = ROLE == "any" or tok.dep_ == "sb" or (tok.dep_ == "nk" and tok.head.dep_ == "sbp") or (ROLE == "broad" and tok.dep_ in ("cj", "cd", "app", "oa", "da", "pd", "ROOT", "par", "ep", "nk") and tok.head.pos_ not in ("ADP",) )
        if not role:
            continue
        out.append(DetectedSpan(category="person", start=tok.idx, end=tok.idx + len(t), value=text[tok.idx:tok.idx + len(t)]))
    return out


def apply_propn() -> None:
    orig = pn.detect_presidio_entities

    def wrapped(text):
        base = orig(text)
        extra = [s for s in propn_spans(text) if not any(b.start < s.end and s.start < b.end for b in base)]
        return base + extra

    pn.detect_presidio_entities = wrapped
    import app.privacy.gateway as gw  # noqa: PLC0415
    gw.detect_presidio_entities = wrapped
