"""Diagnose: welche GEWOEHNLICHEN Rechts-/Rollenwoerter pseudonymisiert die Erkennung als Person/Ort/Organisation?
(Qualitaetslauf 11.10.2026)

Hintergrund: Wird ein gewoehnliches Wort ("Partei") faelschlich zu einem Platzhalter, blockiert der Leak-Check jede
Claude-Antwort, die dasselbe Wort selbst verwendet ("nicht pseudonymisierter Wert ... im Text gefunden"). Im
Ende-zu-Ende-Lauf mit synthetischen Vertraegen waren 4 von 10 Schriftsaetzen allein dadurch blockiert.

Prueft ~120 Rechts-/Vertragsbegriffe in je drei Beispielsaetzen mit dem echten Presidio-/spaCy-Stack (so, wie das Gateway
sie sieht, inkl. Satzfilter) und listet die als Entitaet erkannten. Aufruf:  python scripts/diagnose_common_noun_entities.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.privacy import presidio_ner as pn  # noqa: E402

TERMS = """Partei Parteien Vertragspartei Vertragsparteien Käufer Verkäufer Auftraggeber Auftragnehmer Mieter Vermieter
Pächter Verpächter Darlehensnehmer Darlehensgeber Gläubiger Schuldner Kläger Beklagter Beklagte Antragsteller Antragsgegner
Gericht Amtsgericht Landgericht Behörde Amt Finanzamt Gesellschaft Gesellschafter Geschäftsführer Vorstand Aufsichtsrat
Bürge Erbe Erblasser Testamentsvollstrecker Betreuer Bevollmächtigter Vollmachtgeber Zeuge Sachverständiger Gutachter Schiedsrichter
Mandant Mandantin Rechtsanwalt Notar Gerichtsvollzieher Insolvenzverwalter Verwalter Eigentümer Besitzer Nachbar Untermieter
Arbeitgeber Arbeitnehmer Betriebsrat Versicherer Versicherungsnehmer Versicherung Bank Sparkasse Kunde Lieferant Hersteller
Händler Verbraucher Unternehmer Verbraucherzentrale Staatsanwaltschaft Polizei Kommune Gemeinde Stadt Land Bund Kreis
Vertrag Kaufvertrag Mietvertrag Werkvertrag Dienstvertrag Auftrag Bestellung Rechnung Mahnung Kündigung Widerspruch Einspruch
Klage Berufung Revision Beschwerde Antrag Beschluss Urteil Bescheid Verfügung Anordnung Satzung Vollmacht Bürgschaft Garantie
Sicherheit Kaution Anzahlung Abschlagszahlung Schlusszahlung Vertragsstrafe Verzug Schadensersatz Gewährleistung Haftung Mangel
Lieferung Leistung Abnahme Übergabe Nachfrist Frist Termin Verhandlung Anlage Anhang Protokoll Gutachten Schreiben""".split()

TEMPLATES = [
    "Die {X} ist verpflichtet, die vereinbarte Leistung zu erbringen.",
    "Der {X} haftet für alle Schäden, die aus der Verletzung dieser Pflicht entstehen.",
    "Die andere {X} ist unverzüglich schriftlich zu informieren.",
]


def main() -> None:
    flagged: dict[str, set[str]] = {}
    for term in TERMS:
        for template in TEMPLATES:
            for span in pn.detect_presidio_entities(template.format(X=term)):
                if span.value.strip().lower() == term.lower():
                    flagged.setdefault(term, set()).add(span.category)
    print(f"{len(flagged)} von {len(TERMS)} gewöhnlichen Rechts-/Rollenbegriffen werden als Entität pseudonymisiert:")
    for term, categories in sorted(flagged.items()):
        suppressed = term.lower() in pn._NEVER_ENTITY_WORDS
        print(f"  {term:22s} {sorted(categories)}{'  (Ganzwort-Ausnahme aktiv)' if suppressed else ''}")


if __name__ == "__main__":
    main()
