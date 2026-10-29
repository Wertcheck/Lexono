"""Produktplanung fuer den Shop - VORLAEUFIG, NICHT LIVE.

Diese Werte sind eine konfigurierbare Planungsgrundlage, keine Rechnung und keine Freischaltung: Lexono zeigt sie im
Lizenz-Tab der Einstellungen nur als Produktuebersicht an und schaltet anhand von Preisen oder Produktstufen NICHTS
frei. Freigeschaltet wird ausschliesslich ueber die signierte Lizenz (app/modules/licensing.py), begrenzt durch das
Build-Profil (app/modules/profile.py). Preisaenderungen hier aendern keine bereits ausgestellte Lizenz.

Modell (Owner-Vorgabe 29.10.2026, ersetzt die kombinierbaren Zusatzmodul-Preise vom 28.10.2026):
- Drei **Produktstufen**, jede schliesst die vorherige vollstaendig ein:
  Lexono (Kanzlei) -> Lexono Finance (+ Finanzbuchhaltung, Belegverwaltung) -> Lexono Finance & Tax (+ Steuern).
- Preise je Benutzer und Monat, **inklusive MwSt.**
- Status je Stufe: "verfuegbar" oder "in_entwicklung" - unabhaengig davon, ob Code im Repository existiert. Im
  Kanzlei-Pilot sind Finance und Finance & Tax in Entwicklung.
- Keine Einrichtungsgebuehr: Kunden kaufen und installieren selbst.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.modules.catalog import BASE_MODULES, CATALOG, MODULE_FINANZEN, MODULE_KANZLEI, MODULE_STEUERN

PLANNING_STATUS = "vorläufige Produktplanung – nicht live"
CURRENCY = "EUR"
SETUP_FEE = Decimal("0.00")
PRICE_BASIS = "pro Benutzer / Monat, inkl. MwSt."

STATUS_AVAILABLE = "verfuegbar"
STATUS_IN_DEVELOPMENT = "in_entwicklung"
STATUS_LABELS = {STATUS_AVAILABLE: "Verfügbar", STATUS_IN_DEVELOPMENT: "In Entwicklung"}


@dataclass(frozen=True)
class Product:
    key: str
    name: str            # vollstaendiger Name, z. B. "Lexono Finance & Tax"
    edition: str         # Zusatz unter "Lexono" ("" fuer das Basisprodukt)
    modules: frozenset[str]
    monthly_gross_per_user: Decimal
    status: str
    summary: str

    @property
    def status_label(self) -> str:
        return STATUS_LABELS[self.status]


PRODUCTS: tuple[Product, ...] = (
    Product("lexono", "Lexono", "", frozenset({MODULE_KANZLEI}), Decimal("99.00"), STATUS_AVAILABLE,
            "Kanzlei-Workflows, KI-Chat, Pseudonymisierung, Schriftsätze, Mandanten- und Aktenverwaltung"),
    Product("finance", "Lexono Finance", "Finance", frozenset({MODULE_KANZLEI, MODULE_FINANZEN}), Decimal("149.00"),
            STATUS_IN_DEVELOPMENT, "Alles aus Lexono, dazu Finanzbuchhaltung und digitale Belegverwaltung"),
    Product("finance_tax", "Lexono Finance & Tax", "Finance & Tax",
            frozenset({MODULE_KANZLEI, MODULE_FINANZEN, MODULE_STEUERN}), Decimal("249.00"), STATUS_IN_DEVELOPMENT,
            "Alles aus Lexono Finance, dazu Steuererklärungen und Steuer-Workflows"),
)
PRODUCTS_BY_KEY = {p.key: p for p in PRODUCTS}


@dataclass(frozen=True)
class Feature:
    label: str
    module: str  # das Modul, das die Funktion traegt


#: Funktionsvergleich "Ihre Vorteile im Überblick" - enthalten, wenn das Modul zur Produktstufe gehoert
FEATURES: tuple[Feature, ...] = (
    Feature("Kanzlei-Workflows", MODULE_KANZLEI),
    Feature("Mandanten- und Aktenverwaltung", MODULE_KANZLEI),
    Feature("KI-Chat und Dokumentenanalyse", MODULE_KANZLEI),
    Feature("Lokale Pseudonymisierung", MODULE_KANZLEI),
    Feature("Schriftsätze", MODULE_KANZLEI),
    Feature("Finanzbuchhaltung", MODULE_FINANZEN),
    Feature("Digitale Belegverwaltung", MODULE_FINANZEN),
    Feature("Steuererklärungen und Steuer-Workflows", MODULE_STEUERN),
)

BILLING_TERMS = {"monthly": 1}  # Abrechnungszeitraum -> Monate; weitere (z. B. "yearly": 12) spaeter


class PlanError(ValueError):
    pass


def plan_modules(add_ons: set[str] | frozenset[str] | list[str]) -> frozenset[str]:
    """Basispaket + gewaehlte Zusatzmodule; unbekannte oder Basismodule als Zusatz werden abgelehnt."""
    chosen = frozenset(add_ons)
    unknown = [m for m in chosen if m not in CATALOG or not CATALOG[m].licensed]
    if unknown:
        raise PlanError(f"Kein buchbares Zusatzmodul: {sorted(unknown)}")
    return BASE_MODULES | chosen


def product_for(add_ons) -> Product:
    """Produktstufe zu einer Modulkombination - nur die drei Stufen sind buchbar (z. B. Steuern ohne Finance nicht)."""
    modules = plan_modules(add_ons)
    for product in PRODUCTS:
        if product.modules == modules:
            return product
    raise PlanError(f"Keine Produktstufe für diese Modulkombination: {sorted(modules)}")


def plan_price(add_ons, *, term: str = "monthly", users: int = 1) -> Decimal:
    """Planungspreis (brutto, inkl. MwSt.) einer Produktstufe je Abrechnungszeitraum und Benutzerzahl.
    Nur Planung - keine Rechnung, keine Freischaltung."""
    if term not in BILLING_TERMS:
        raise PlanError(f"Unbekannter Abrechnungszeitraum: {term}")
    if users < 1:
        raise PlanError("Mindestens ein Benutzer.")
    return product_for(add_ons).monthly_gross_per_user * users * BILLING_TERMS[term] + SETUP_FEE


def current_product(enabled_modules) -> Product:
    """Die hoechste Produktstufe, deren Module ALLE tatsaechlich freigeschaltet sind (Lizenz UND Build-Profil, siehe
    licensing.enabled_modules) - reine Anzeige, ohne jede Wirkung auf die Freischaltung."""
    enabled = frozenset(enabled_modules)
    best = PRODUCTS[0]
    for product in PRODUCTS:
        if product.modules <= enabled:
            best = product
    return best
