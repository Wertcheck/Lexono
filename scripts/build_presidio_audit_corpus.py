"""Erzeugt die EINGEFRORENE Testbasis fuer den Presidio-Audit (scripts/presidio_audit_corpus.json).

Nur einmal ausfuehren; danach bleibt die JSON-Datei unveraendert (Baseline fuer A/B/C-Vergleiche, siehe
scripts/bench_presidio_audit.py). Ausschliesslich synthetische Daten: erfundene Namen/Anschriften, Nummern mit gueltiger
Pruefziffer werden aus einem festen Zufallsstartwert erzeugt (keine realen Personen/Dokumente).

Je Fall: `pii` = Werte, die NIE im Klartext in der ausgehenden Payload stehen duerfen (False Negative, wenn doch);
`keep` = Inhalte, die lesbar bleiben muessen (False Positive, wenn maskiert/blockiert).
"""

from __future__ import annotations

import json
import random
from pathlib import Path

from presidio_analyzer.predefined_recognizers import (
    DeHealthInsuranceRecognizer,
    DeIdCardRecognizer,
    DeSocialSecurityRecognizer,
    DeTaxIdRecognizer,
    DeVatIdRecognizer,
)

RNG = random.Random(20261013)


def _valid(recognizer, make):
    for _ in range(200000):
        value = make()
        if recognizer.validate_result(value):
            return value
    raise RuntimeError(type(recognizer).__name__)


STEUER_ID = _valid(DeTaxIdRecognizer(), lambda: str(RNG.randint(1, 9)) + "".join(str(RNG.randint(0, 9)) for _ in range(10)))
RVNR = _valid(
    DeSocialSecurityRecognizer(),
    lambda: f"{RNG.randint(10, 89)}{RNG.randint(1, 28):02d}{RNG.randint(1, 12):02d}{RNG.randint(40, 99)}"
    f"{RNG.choice('ABCDEFGHKLMNPRSTW')}{RNG.randint(0, 99):02d}{RNG.randint(0, 9)}",
)
KVNR = _valid(DeHealthInsuranceRecognizer(), lambda: RNG.choice("ABCDEFGHKLMNPRSTW") + "".join(str(RNG.randint(0, 9)) for _ in range(9)))
NPA = _valid(
    DeIdCardRecognizer(),
    lambda: RNG.choice("CFGHJKLMNPRTVWXYZ") + "".join(RNG.choice("CFGHJKLMNPRTVWXYZ0123456789") for _ in range(7)) + str(RNG.randint(0, 9)),
)
VAT = _valid(DeVatIdRecognizer(), lambda: "DE" + "".join(str(RNG.randint(0, 9)) for _ in range(9)))


def case(case_id, group, text, pii=(), keep=()):
    return {"id": case_id, "group": group, "text": text, "pii": list(pii), "keep": list(keep)}


CASES = [
    # --- Namen: normale Schreibung -------------------------------------------------------------------------------------
    case("N01", "name_normal", "Herr Gernot Hahn hat am 12.03.2026 angerufen und um Rückruf gebeten.", ["Gernot", "Hahn"], ["Rückruf"]),
    case("N02", "name_normal", "Die Mandantin Odette Paschke wünscht eine Einschätzung zur Kündigung.", ["Odette", "Paschke"], ["Kündigung", "Mandantin"]),
    case("N03", "name_normal", "Zeuge Tjark Westphal bestätigte den Wasserschaden in der Küche.", ["Tjark", "Westphal"], ["Wasserschaden", "Zeuge"]),
    case("N04", "name_normal", "Rechtsanwältin Dr. Mareike Thormählen vertritt die Gegenseite.", ["Mareike", "Thormählen"], ["Gegenseite"]),
    case("N05", "name_normal", "Brockhagen hat die Rechnung nicht bezahlt, obwohl die Frist abgelaufen ist.", ["Brockhagen"], ["Rechnung", "Frist"]),
    case("N06", "name_normal", "Der Vermieter Kowalczyk verweigert die Rückzahlung der Kaution.", ["Kowalczyk"], ["Vermieter", "Kaution"]),
    case("N07", "name_normal", "Laut Gutachten von Sachverständigem Holger Behrends liegt ein Mangel vor.", ["Holger", "Behrends"], ["Gutachten", "Mangel"]),
    case("N08", "name_normal", "Frau Sommer und Herr Winter haben den Vertrag gemeinsam unterzeichnet.", ["Sommer", "Winter"], ["Vertrag"]),
    # --- Namen: Versalien / Kleinschreibung --------------------------------------------------------------------------------
    case("U01", "name_caps", "HERRN\nGERNOT HAHN\nBIRKENALLEE 17\n12345 MUSTERSTADT", ["GERNOT", "HAHN", "BIRKENALLEE", "MUSTERSTADT"], ["HERRN"]),
    case("U02", "name_caps", "Der Beklagte WINKELMANN hat die Forderung bestritten.", ["WINKELMANN"], ["Beklagte", "Forderung"]),
    case("U03", "name_caps", "In Sachen HABERLAND ./. WINKELMANN wegen Werklohn", ["HABERLAND", "WINKELMANN"], ["Werklohn", "./."]),
    case("U04", "name_caps", "Mit freundlichen Grüßen\nGEZ. FENJA RÜBSAM\nRechtsanwältin", ["FENJA", "RÜBSAM"], ["Rechtsanwältin"]),
    case("L01", "name_lower", "mein vermieter herr sauer hat die heizung nicht repariert.", ["sauer"], ["vermieter", "heizung"]),
    case("L02", "name_lower", "bitte rufen sie frau elfriede kaminski zurück, es geht um die kündigung.", ["elfriede", "kaminski"], ["kündigung"]),
    # --- Sonderzeichen, griechisch, kyrillisch ----------------------------------------------------------------------------
    case("S01", "name_special", "Herr Jürgen Schößler und Frau Ayşe Yıldırım sind Zeugen.", ["Schößler", "Ayşe", "Yıldırım"], ["Zeugen"]),
    case("S02", "name_special", "Die Klägerin Agnieszka Łukasiewicz verlangt 3.912,40 EUR.", ["Agnieszka", "Łukasiewicz"], ["Klägerin", "3.912,40"]),
    case("S03", "name_special", "Herr René Lefèvre und Frau Søren Kjærgaard haben unterschrieben.", ["René", "Lefèvre", "Kjærgaard"], []),
    case("S04", "name_special", "Der Zeuge Jiří Dvořák wohnt in Brünn.", ["Jiří", "Dvořák"], ["Zeuge"]),
    case("G01", "name_greek_cyrillic", "Der Mandant Γιάννης Παπαδόπουλος hat die Vollmacht unterschrieben.", ["Γιάννης", "Παπαδόπουλος"], ["Vollmacht"]),
    case("G02", "name_greek_cyrillic", "Die Zeugin Анна Смирнова hat ausgesagt.", ["Анна", "Смирнова"], ["Zeugin"]),
    case("G03", "name_greek_cyrillic", "ΓΙΑΝΝΗΣ ΠΑΠΑΔΟΠΟΥΛΟΣ ist Geschäftsführer der Firma.", ["ΓΙΑΝΝΗΣ", "ΠΑΠΑΔΟΠΟΥΛΟΣ"], ["Geschäftsführer"]),
    # --- Anschriften ------------------------------------------------------------------------------------------------------
    case("A01", "address", "Die Wohnung liegt in der Birkenallee 17, 12345 Musterstadt.", ["Birkenallee 17", "Musterstadt"], ["Wohnung"]),
    case("A02", "address", "Herrn\nKlaus Brockhagen\nRosenweg 2a\n24103 Kiel\n\nBetreff: Mahnung", ["Brockhagen", "Rosenweg", "Kiel"], ["Betreff", "Mahnung"]),
    case("A03", "address", "Lieferung an: Hafenstraße 9, 20457 Hamburg, 3. OG links", ["Hafenstraße 9", "20457"], ["Lieferung"]),
    case("A04", "address", "Die Mieterin wohnt Am Mühlbach 3 in 79100 Freiburg.", ["Mühlbach 3", "Freiburg"], ["Mieterin"]),
    case("A05", "address", "Postfach 10 20 30, 50667 Köln", ["10 20 30", "Köln"], []),
    # --- Rollen, Anreden, Folgeerwaehnungen --------------------------------------------------------------------------------
    case("R01", "role_followup", "Sehr geehrter Herr Thielker,\nwir bitten um Zahlung. Herr Thielker hat bislang nicht reagiert.", ["Thielker"], ["Zahlung"]),
    case("R02", "role_followup", "Die Zeugin Eiche hat ausgesagt. Eiche bestätigte den Unfallhergang.", ["Eiche"], ["Unfallhergang"]),
    case("R03", "role_followup", "Am 12.03.2026 erschien Frau Marlene Osthues in der Kanzlei. Osthues legte die Unterlagen vor.", ["Marlene", "Osthues"], ["Kanzlei", "Unterlagen"]),
    case("R04", "role_followup", "Die Beklagte, vertreten durch Herrn Wessolowski, beantragt Klageabweisung.", ["Wessolowski"], ["Beklagte", "Klageabweisung"]),
    case("R05", "role_followup", "Hallo Frau Krummbiegel, anbei die Unterlagen. Viele Grüße", ["Krummbiegel"], ["Unterlagen"]),
    # --- IBAN, Kontakt, weitere PII ---------------------------------------------------------------------------------------
    case("P01", "pii_structured", "Bitte überweisen Sie auf DE89 3704 0044 0532 0130 00 (Sparkasse).", ["DE89 3704 0044 0532 0130 00"], ["Sparkasse"]),
    case("P02", "pii_structured", "Erreichbar unter 0151 23456789 oder g.hahn@beispiel-mail.de.", ["0151 23456789", "g.hahn@beispiel-mail.de"], []),
    case("P03", "pii_structured", "Tel.: +49 40 123456-0, Fax: 040/123456-99", ["+49 40 123456-0", "040/123456-99"], []),
    case("P04", "pii_structured", f"Steuer-ID: {STEUER_ID}", [STEUER_ID], []),
    case("P05", "pii_structured", "Steuernummer 143/815/08154 beim Finanzamt Musterstadt", ["143/815/08154"], ["Finanzamt"]),
    case("P06", "pii_structured", f"USt-IdNr.: {VAT}", [VAT], []),
    case("P07", "pii_structured", f"Rentenversicherungsnummer: {RVNR}", [RVNR], []),
    case("P08", "pii_structured", f"Krankenversichertennummer {KVNR}, Krankenkasse AOK", [KVNR], ["Krankenkasse"]),
    case("P09", "pii_structured", f"Personalausweisnummer {NPA} wurde vorgelegt.", [NPA], []),
    case("P10", "pii_structured", "Das Fahrzeug mit dem Kennzeichen HH-AB 1234 parkte vor der Einfahrt.", ["HH-AB 1234"], ["Fahrzeug", "Einfahrt"]),
    case("P11", "pii_structured", "Eingetragen im Handelsregister des Amtsgerichts Hamburg unter HRB 123456.", ["HRB 123456"], ["Handelsregister"]),
    case("P12", "pii_structured", "Profil: https://www.linkedin.com/in/gernot-hahn-1980 und www.hahn-bau.de", ["gernot-hahn", "hahn-bau"], ["Profil"]),
    case("P13", "pii_structured", "Der Zugriff erfolgte von der IP-Adresse 192.168.178.23.", ["192.168.178.23"], ["Zugriff"]),
    case("P14", "pii_structured", "Geburtsdatum: 14.07.1983, geboren in Bielefeld", ["14.07.1983", "Bielefeld"], ["Geburtsdatum"]),
    case("P15", "pii_structured", "BIC: COBADEFFXXX, Kontoinhaber Gernot Hahn", ["COBADEFFXXX", "Gernot", "Hahn"], ["Kontoinhaber"]),
    # --- Fachsprache, Normzitate, Abkuerzungen, Betraege (nur keep) ---------------------------------------------------------
    case("K01", "legal_keep", "Der Anspruch folgt aus § 535 Abs. 1 BGB i. V. m. § 280 Abs. 1 BGB.", [], ["§ 535 Abs. 1 BGB", "§ 280 Abs. 1 BGB"]),
    case("K02", "legal_keep", "Nach Art. 6 Abs. 1 lit. b DSGVO ist die Verarbeitung rechtmäßig.", [], ["Art. 6 Abs. 1 lit. b DSGVO", "Verarbeitung"]),
    case("K03", "legal_keep", "Der Halter haftet nach § 7 StVG; die StVO wurde verletzt.", [], ["§ 7 StVG", "StVO", "Halter"]),
    case("K04", "legal_keep", "Die Klage ist zulässig und begründet. Die Kosten trägt die Beklagte (§ 91 ZPO).", [], ["Klage", "Kosten", "§ 91 ZPO"]),
    case("K05", "legal_keep", "Streitwert: 12.400,00 EUR; Gebühr 1,3 nach Nr. 3100 VV RVG.", [], ["12.400,00", "3100 VV RVG", "Streitwert"]),
    case("K06", "legal_keep", "Mängelanzeige: Feuchtigkeit im Keller, Risse im Estrich, Nachbesserungsfrist bis 30.11.2026.", [], ["Mängelanzeige", "Feuchtigkeit", "Estrich", "Nachbesserungsfrist"]),
    case("K07", "legal_keep", "Die Berufung gegen das Urteil des Landgerichts wird zurückgewiesen.", [], ["Berufung", "Urteil", "Landgerichts"]),
    case("K08", "legal_keep", "Der Erblasser hinterließ ein Testament; der Pflichtteil beträgt 1/4.", [], ["Erblasser", "Testament", "Pflichtteil"]),
    case("K09", "legal_keep", "Gemäß §§ 433 ff. BGB schuldet der Verkäufer die mangelfreie Sache.", [], ["§§ 433 ff. BGB", "Verkäufer"]),
    case("K10", "legal_keep", "Die GmbH & Co. KG haftet nach § 128 HGB; das AG Hamburg ist zuständig.", [], ["§ 128 HGB"]),
    # --- Ueberschriften, gewoehnliche Woerter, fremdsprachige Texte ------------------------------------------------------
    case("H01", "heading_keep", "BETREFF: MAHNUNG ODER KLAGE\nIM NAMEN DES VOLKES\nURTEIL", [], ["BETREFF", "MAHNUNG", "KLAGE", "VOLKES", "URTEIL"]),
    case("H02", "heading_keep", "TATBESTAND\nDie Parteien streiten über Mängel.\nENTSCHEIDUNGSGRÜNDE", [], ["TATBESTAND", "Parteien", "ENTSCHEIDUNGSGRÜNDE"]),
    case("H03", "heading_keep", "WICHTIG: BITTE FRISTEN BEACHTEN UND ZAHLUNG LEISTEN", [], ["WICHTIG", "FRISTEN", "ZAHLUNG"]),
    case("H04", "heading_keep", "Offener Prüfpunkt: Hochachtungsvoll, mit freundlichen Grüßen", [], ["Prüfpunkt", "Hochachtungsvoll"]),
    case("H05", "heading_keep", "Sommer, Sonne und Winterdienst: die Hausordnung regelt die Schneeräumung.", [], ["Sommer", "Sonne", "Winterdienst", "Hausordnung"]),
    case("F01", "foreign_keep", "The tenant shall pay the rent in advance on the first day of each month.", [], ["tenant", "rent", "month"]),
    case("F02", "foreign_keep", "Le locataire doit payer le loyer avant le premier jour du mois.", [], ["locataire", "loyer", "mois"]),
    case("F03", "foreign_keep", "CONDITIONS GÉNÉRALES DE VENTE – FORCE MAJEURE", [], ["CONDITIONS", "VENTE"]),
    case("F04", "foreign_keep", "Il conduttore deve pagare il canone entro il primo giorno del mese.", [], ["conduttore", "canone"]),
]


def main() -> None:
    out = Path(__file__).with_name("presidio_audit_corpus.json")
    out.write_text(json.dumps({"version": 1, "cases": CASES}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(CASES)} Faelle, {sum(len(c['pii']) for c in CASES)} PII-Werte, {sum(len(c['keep']) for c in CASES)} keep-Werte -> {out}")


if __name__ == "__main__":
    main()
