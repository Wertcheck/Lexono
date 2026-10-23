"""Synthetische Regressionsfaelle aus bestaetigten Ursachen (ARCHITECTURE.md §75, FUTURE_ROADMAP.md P4).

Ablauf: Meldung -> Diagnose -> bestaetigte Ursache -> von Hand geschriebener SYNTHETISCHER Testfall -> Freigabe ->
Export -> Uebernahme durch Entwickler nach tests/regression_cases/ -> laeuft dort gegen die echte Pipeline
(tests/test_quality_regression_cases.py). Keine Stufe wirkt automatisch auf produktive Regeln.

Leck-Pruefung (fail-closed, vor dem Speichern UND beim Export): Der synthetische Text darf weder die Fundstelle der
Meldung noch einen Namen enthalten, der in IRGENDEINER Akte als Mandant oder Beteiligter gespeichert ist (auch Nachname
allein, ab 3 Zeichen), noch E-Mail-, Telefon-, IBAN- oder Aktenzeichen-artige Werte, die die Regex-Erkennung findet.
Echte Mandantendaten werden damit nie ungeprueft in einen Testfall uebernommen. Die Pruefung ersetzt keine menschliche
Durchsicht - sie verhindert die offensichtlichen Fehler.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models import Client, Party
from app.models.quality_report import (
    CATEGORY_ALTERED_LEGAL_CONTENT,
    CATEGORY_MISSED_DEADLINE,
    CATEGORY_MISSED_PERSONAL_DATA,
    CATEGORY_OVER_PSEUDONYMIZATION,
)

MAX_SYNTHETIC_TEXT = 4000
#: Kategorien mit maschinell pruefbarer Erwartung; "privacy_concern" braucht eine konkretere Einordnung.
CASE_CATEGORIES = (
    CATEGORY_MISSED_PERSONAL_DATA,
    CATEGORY_OVER_PSEUDONYMIZATION,
    CATEGORY_ALTERED_LEGAL_CONTENT,
    CATEGORY_MISSED_DEADLINE,
)
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
#: Regex-Kategorien, die in einem synthetischen Text nichts zu suchen haben (koennten echte Kontaktdaten sein).
_FORBIDDEN_REGEX_CATEGORIES = {"email", "phone", "iban", "telefon", "e-mail"}


class RegressionCaseError(ValueError):
    pass


@dataclass(frozen=True)
class Expectation:
    must_mask: tuple[str, ...] = ()
    must_keep: tuple[str, ...] = ()
    expected_due_dates: tuple[str, ...] = ()

    def to_json(self) -> str:
        return json.dumps({"must_mask": list(self.must_mask), "must_keep": list(self.must_keep),
                           "expected_due_dates": list(self.expected_due_dates)}, ensure_ascii=False)


def _terms(raw: str) -> tuple[str, ...]:
    return tuple(t.strip() for t in re.split(r"[\n;]", raw or "") if t.strip())


def build_expectation(category: str, *, must_mask: str = "", must_keep: str = "", due_dates: str = "",
                      synthetic_text: str) -> Expectation:
    """Prueft, dass die Erwartung zur Kategorie passt und sich auf den synthetischen Text bezieht."""
    if category not in CASE_CATEGORIES:
        raise RegressionCaseError("Für diese Kategorie gibt es keine maschinell prüfbare Erwartung.")
    exp = Expectation(_terms(must_mask), _terms(must_keep), _terms(due_dates))
    if category == CATEGORY_MISSED_PERSONAL_DATA and not exp.must_mask:
        raise RegressionCaseError("Bitte angeben, welcher (erfundene) Wert pseudonymisiert werden muss.")
    if category in (CATEGORY_OVER_PSEUDONYMIZATION, CATEGORY_ALTERED_LEGAL_CONTENT) and not exp.must_keep:
        raise RegressionCaseError("Bitte angeben, welcher Begriff unverändert bleiben muss.")
    if category == CATEGORY_MISSED_DEADLINE:
        if not exp.expected_due_dates or not all(_DATE.match(d) for d in exp.expected_due_dates):
            raise RegressionCaseError("Bitte das erwartete Fristdatum im Format JJJJ-MM-TT angeben.")
    for term in exp.must_mask + exp.must_keep:
        if term not in synthetic_text:
            raise RegressionCaseError("Jeder erwartete Begriff muss wörtlich im synthetischen Text vorkommen.")
    return exp


def _real_names(db: Session) -> set[str]:
    names: set[str] = set()
    for (name,) in db.query(Client.name).all() + db.query(Party.name).all():
        if not name:
            continue
        name = name.strip()
        names.add(name.lower())
        parts = [p for p in re.split(r"[\s,]+", name) if len(p) >= 3]
        if len(parts) > 1:
            names.add(parts[-1].lower())  # Nachname allein
    return {n for n in names if len(n) >= 3}


def leak_findings(db: Session, synthetic_text: str, *, report_excerpt: str | None) -> list[str]:
    """Gruende, warum der Text nicht synthetisch genug ist - als feste Bezeichner, ohne die gefundenen Werte."""
    problems: list[str] = []
    lowered = synthetic_text.lower()
    if report_excerpt and report_excerpt.strip() and report_excerpt.strip().lower() in lowered:
        problems.append("enthaelt_fundstelle_der_meldung")
    words = set(re.findall(r"[\wÄÖÜäöüß-]{3,}", lowered))
    for name in _real_names(db):
        if (" " in name and name in lowered) or name in words:
            problems.append("enthaelt_gespeicherten_mandanten_oder_beteiligtennamen")
            break
    from app.privacy.detectors import detect_all

    categories = {span.category.lower() for span in detect_all(synthetic_text, None)}
    if categories & _FORBIDDEN_REGEX_CATEGORIES:
        problems.append("enthaelt_kontaktdaten_artige_werte")
    return problems


def validate_synthetic_text(db: Session, synthetic_text: str, *, report_excerpt: str | None) -> str:
    text = (synthetic_text or "").strip()
    if not text:
        raise RegressionCaseError("Der synthetische Text darf nicht leer sein.")
    if len(text) > MAX_SYNTHETIC_TEXT:
        raise RegressionCaseError(f"Der synthetische Text ist länger als {MAX_SYNTHETIC_TEXT} Zeichen.")
    problems = leak_findings(db, text, report_excerpt=report_excerpt)
    if problems:
        raise RegressionCaseError(
            "Nicht übernommen – der Text wirkt nicht synthetisch: " + ", ".join(problems).replace("_", " ")
            + ". Bitte nur erfundene Namen und Werte verwenden."
        )
    return text
