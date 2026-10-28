"""Produktplanung fuer den Shop (28.10.2026) - VORLAEUFIG, NICHT LIVE.

Diese Werte sind eine konfigurierbare Planungsgrundlage (Owner-Vorgabe 28.10.2026), keine veroeffentlichten Preise:
Lexono zeigt sie nirgends als Kaufpreis an und schaltet anhand von Preisen nichts frei. Freigeschaltet wird
ausschliesslich ueber die signierte Lizenz (app/modules/licensing.py). Preisaenderungen hier aendern keine bereits
ausgestellte Lizenz.

Modell (bewusst flexibel):
- Ein **Produkt** besteht aus Modulen (app/modules/catalog.py); das Basispaket ist immer enthalten.
- Zusatzmodule sind frei kombinierbar (Finanzen, Steuern oder beide).
- Preise je Modul und Abrechnungszeitraum; weitere Zeitraeume (z. B. jaehrlich mit Rabatt) oder Bundles koennen
  ergaenzt werden, ohne Lizenzformat oder Pruefung zu aendern.
- Keine Einrichtungsgebuehr: Kunden kaufen und installieren selbst.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.modules.catalog import BASE_MODULES, CATALOG, MODULE_FINANZEN, MODULE_KANZLEI, MODULE_STEUERN

PLANNING_STATUS = "vorläufige Produktplanung – nicht live"
CURRENCY = "EUR"
SETUP_FEE = Decimal("0.00")


@dataclass(frozen=True)
class ModulePrice:
    module: str
    shop_name: str
    monthly_net: Decimal
    is_add_on: bool


#: Vorlaeufige Monatspreise (netto, ohne USt-Angabe - steuerliche Ausweisung im Shop ist noch zu klaeren)
MODULE_PRICES: dict[str, ModulePrice] = {
    MODULE_KANZLEI: ModulePrice(MODULE_KANZLEI, "Lexono Kanzlei", Decimal("99.00"), is_add_on=False),
    MODULE_FINANZEN: ModulePrice(MODULE_FINANZEN, "Lexono Finance", Decimal("149.00"), is_add_on=True),
    MODULE_STEUERN: ModulePrice(MODULE_STEUERN, "Lexono Steuern", Decimal("299.00"), is_add_on=True),
}

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


def plan_price(add_ons, *, term: str = "monthly") -> Decimal:
    """Planungspreis eines Pakets je Abrechnungszeitraum (netto). Nur Planung - keine Rechnung, keine Freischaltung."""
    if term not in BILLING_TERMS:
        raise PlanError(f"Unbekannter Abrechnungszeitraum: {term}")
    modules = plan_modules(add_ons)
    return sum((MODULE_PRICES[m].monthly_net for m in modules), Decimal("0")) * BILLING_TERMS[term] + SETUP_FEE
