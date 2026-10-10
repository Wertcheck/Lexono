"""Diagnose: warum werden Fachbegriffe als Person/Ort/Organisation pseudonymisiert? (Qualitaetslauf 10.10.2026)

Reproduzierbar, ausschliesslich synthetische Saetze. Gibt aus:
1. welche von ~100 Fachbegriffen (je drei Beispielsaetze) der Presidio-/spaCy-Stack als Entitaet erkennt,
2. fuer diese Woerter die spaCy-Rohmerkmale (Entitaetstyp, Wortart, Wortvektor vorhanden?) - zur Einordnung der
   Ursache (NER-Modell, nicht Typzuordnung/Nachverarbeitung),
3. eine Gegenprobe: echte Namen/Orte werden weiterhin erkannt.
Aufruf:  python scripts/diagnose_ner_false_positives.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.privacy import presidio_ner as pn  # noqa: E402

TERMS = """Attika Verblechung Verblechungen Bitumenbahn Lichtkuppel Lichtkuppeln Dämmung Abdichtung Flachdach Dachabdichtung
Gewährleistung Mängel Mangel Minderung Nachbesserung Nacherfüllung Vertragsstrafe Sicherheitseinbehalt Abschlagszahlung Schlusszahlung
Abnahmeprotokoll Werkvertrag Bauleiter Sachverständiger Gutachten Baumangel Feuchtigkeit Schimmelbefall Außenwand Estrich Fassade
Wärmedämmverbundsystem Dampfsperre Entwässerung Dachrinne Fallrohr Brandschutz Rauchmelder Heizkostenabrechnung Betriebskosten
Nebenkostenvorauszahlung Kaution Mietspiegel Kappungsgrenze Eigenbedarf Räumungsfrist Zugluft Mietminderung Schönheitsreparaturen
Kaufpreis Sachmängelhaftung Gewährleistungsausschluss Erstzulassung Laufleistung Gebrauchtwagen Übergabeprotokoll Quittung
Erbschaftsteuer Grundbesitzwert Freibetrag Nachlassverzeichnis Vermächtnis Pflichtteil Testamentsvollstrecker Betriebsprüfung
Umsatzsteuer Vorsteuerabzug Einspruchsfrist Bescheid Steuerbescheid Verspätungszuschlag Säumniszuschlag Zwangsgeld Vollstreckung
Schadensersatz Verzugszinsen Basiszinssatz Fälligkeit Zahlungsverzug Mahnbescheid Widerspruch Rücktritt Anfechtung Verjährung
Kündigungsfrist Abmahnung Betriebsrat Aufhebungsvertrag Zeugnis Überstunden Urlaubsabgeltung Kündigungsschutzklage
Prozessvollmacht Streitwert Gerichtskosten Vergleich Klageabweisung Berufungsbegründung Beweislast Zeugenvernehmung""".split()

TEMPLATES = [
    "Die {X} ist nicht ordnungsgemäß ausgeführt worden.",
    "An der {X} zeigt sich ein erheblicher Schaden an mehreren Stellen.",
    "Wir verlangen die Beseitigung der {X} bis zum 30.11.2026.",
]


def main() -> None:
    nlp = pn._get_analyzer_engine().nlp_engine.nlp["de"]
    raw_flagged: dict[str, list[str]] = {}
    for term in TERMS:
        for tpl in TEMPLATES:
            text = tpl.format(X=term)
            doc = nlp(text)
            for token in doc:
                if token.text == term and token.ent_type_ in ("LOC", "PER", "ORG"):
                    raw_flagged.setdefault(term, []).append(f"{token.ent_type_}/{token.pos_}/{'OOV' if token.is_oov else 'Vektor'}")
    print(f"spaCy-NER (de_core_news_lg) erkennt {len(raw_flagged)} von {len(TERMS)} Fachbegriffen als Entität:")
    for term, info in raw_flagged.items():
        suppressed = term.lower() in pn._NEVER_ENTITY_WORDS
        still = any(
            term.lower() in s.value.lower()
            for tpl in TEMPLATES
            for s in pn.detect_presidio_entities(tpl.format(X=term))
        )
        print(f"  {term:22s} {sorted(set(info))}  -> im Gateway: {'weiterhin erkannt' if still else 'nicht erkannt (Ganzwort-Ausnahme)' if suppressed else 'nicht erkannt'}")

    names = ["Henrike Marquardt", "Olaf Thiessen", "Rainer Kostka", "Erika Mustermann", "Dr. Jonas Wiebe", "Svenja Falk"]
    missed = [
        n for n in names
        if not any(n.split()[-1] in s.value for s in pn.detect_presidio_entities(f"Das Schreiben stammt von {n} und wurde gestern versandt."))
    ]
    print("Gegenprobe - echte Namen nicht erkannt:", missed or "keine")


if __name__ == "__main__":
    main()
