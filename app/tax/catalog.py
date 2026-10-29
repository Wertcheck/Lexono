"""Steuerarten und Vorgangsarten fuer "Lexono Steuern" (28.10.2026).

Grundlage: die im ERiC-Entwicklerhandbuch 43.3 als im Echtbetrieb unterstuetzt genannten Verfahren (Recherche
28.10.2026, siehe MODULARCHITEKTUR.md). Die Schluessel hier sind Lexono-interne Kennungen, KEINE ERiC-Datenart-
namen; die Zuordnung zu Datenarten/Versionen erfolgt erst mit der ERiC-Integration und muss dann gegen die jeweils
gueltige Dokumentation geprueft werden.

``pilot_priority``: aus den Angaben zur Pilotkanzlei (Steuerfachanwalt; Einkommensteuer, Unternehmenssteuern,
Umsatzsteuer sowie Einspruch/Steuerstreit) abgeleitet - steuert nur die Reihenfolge der Umsetzung, nicht den Umfang
des Datenmodells.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TaxType:
    key: str
    label: str
    filing_kind: str          # erklaerung / anmeldung / voranmeldung / nachricht / bilanz
    period_kinds: tuple[str, ...]
    pilot_priority: int       # 1 = zuerst, 3 = spaeter


TAX_TYPES: dict[str, TaxType] = {t.key: t for t in (
    TaxType("est", "Einkommensteuererklärung", "erklaerung", ("year",), 1),
    TaxType("ust", "Umsatzsteuererklärung", "erklaerung", ("year",), 1),
    TaxType("ust_va", "Umsatzsteuer-Voranmeldung", "voranmeldung", ("month", "quarter"), 1),
    TaxType("einspruch", "Einspruch", "nachricht", ("none",), 1),
    TaxType("fristverlaengerung", "Antrag auf Fristverlängerung", "nachricht", ("none",), 1),
    TaxType("gewst", "Gewerbesteuererklärung", "erklaerung", ("year",), 2),
    TaxType("kst", "Körperschaftsteuererklärung", "erklaerung", ("year",), 2),
    TaxType("feststellung", "Feststellungserklärung (Einkünfte)", "erklaerung", ("year",), 2),
    TaxType("euer", "Einnahmenüberschussrechnung", "erklaerung", ("year",), 2),
    TaxType("ebilanz", "E-Bilanz", "bilanz", ("year",), 3),
    TaxType("lsta", "Lohnsteuer-Anmeldung", "anmeldung", ("month", "quarter", "year"), 3),
    TaxType("erbst", "Erbschaftsteuererklärung", "erklaerung", ("none",), 3),
    TaxType("schenkst", "Schenkungsteuererklärung", "erklaerung", ("none",), 3),
)}

PERIOD_LIMITS = {"year": None, "none": None, "quarter": 4, "month": 12}
