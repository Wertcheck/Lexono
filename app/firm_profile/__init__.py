"""Kanzlei-Profil (Name/Anschrift/Kontakt) - siehe app/firm_profile/service.py.

Kanzleifachprofil (fachliche Schwerpunkte) - siehe
app/firm_profile/practice_areas.py."""

from app.firm_profile.practice_areas import (
    InvalidPracticeAreaError,
    PracticeAreaOption,
    get_display_options,
    get_selected_practice_areas,
    remove_practice_area,
    set_practice_areas,
)
from app.firm_profile.letterheads import FIRM_LETTERHEAD_REF, resolve_letterhead
from app.firm_profile.service import get_firm_profile

__all__ = [
    "get_firm_profile",
    "FIRM_LETTERHEAD_REF",
    "resolve_letterhead",
    "InvalidPracticeAreaError",
    "PracticeAreaOption",
    "get_display_options",
    "get_selected_practice_areas",
    "remove_practice_area",
    "set_practice_areas",
]
