"""§43.10: Funde aus dem Korpus `benchmarks/pii_full_documents_v1` (vollstaendige synthetische Dokumente) - Regressionen
fuer die minimal ergaenzten Detektor-Regeln. Nur synthetische Daten."""

import hashlib
import json
from pathlib import Path

import pytest

from app.privacy.detectors import (
    _table_column_values,
    detect_aktenzeichen,
    detect_all,
    detect_rechnungsnummer,
    detect_rubrum_party,
)
from app.privacy.gateway import ClaudePrivacyGateway
from app.privacy.gateway_schema import SECOND_OPINION_PURPOSE

CORPUS = Path(__file__).resolve().parents[1] / "benchmarks" / "pii_full_documents_v1" / "cases" / "test.json"
CORPUS_SHA256 = "176d6f2c384d6aafcd70eaaae08b87755ec0b3741d8b0d38e59828cba7594e1a"


@pytest.fixture(scope="module")
def gateway() -> ClaudePrivacyGateway:
    return ClaudePrivacyGateway()


# --- Kurzrubrum und Gerichtspersonal ----------------------------------------------------------------------------

@pytest.mark.parametrize("text, names", [
    ("In dem Rechtsstreit Beispiel ./. Muster", ["Beispiel", "Muster"]),
    ("Kurzrubrum: Beispiel ./. Muster", ["Beispiel", "Muster"]),
    ("In dem Rechtsstreit Vogel ./. Winter wegen Werklohn", ["Vogel", "Winter"]),
    ("Konrad Beispiel ./. Muster", ["Konrad Beispiel", "Muster"]),
    ("Verkündet am 21.08.2026 - Weiß, Justizhauptsekretärin als Urkundsbeamtin", ["Weiß"]),
    ("Engel, Rechtspflegerin", ["Engel"]),
])
def test_rubrum_parties_and_court_staff_are_persons(text: str, names: list[str]) -> None:
    assert [s.value for s in detect_rubrum_party(text)] == names
    assert all(s.category == "person" for s in detect_rubrum_party(text))


@pytest.mark.parametrize("text", [
    "[PERSON_01] ./. [PERSON_02]",          # bereits pseudonymisiert
    "Kläger ./. Beklagte",                  # nur Rollenwoerter
    "Der Kläger ist Rechtspfleger.",        # keine Komma-Apposition
    "In dem Rechtsstreit ./. wegen",        # keine Namen
])
def test_no_rubrum_finding_without_names(text: str) -> None:
    assert detect_rubrum_party(text) == []


def test_known_over_masking_of_capitalized_nouns_around_the_separator() -> None:
    """Bewusst in Kauf genommen (datenschutzfreundlich): ausserhalb eines Rubrums ist "./." selten; grossgeschriebene
    Substantive daneben werden vorsorglich maskiert, Artikel nicht."""
    assert [s.value for s in detect_rubrum_party("Die Anlage ./. Kopie liegt bei.")] == ["Anlage", "Kopie"]


def test_short_rubrum_is_masked_in_the_gateway_payload(gateway) -> None:
    result = gateway.prepare_request(purpose=SECOND_OPINION_PURPOSE,
                                     sachverhalt="In dem Rechtsstreit Beispiel ./. Muster wegen Werklohn",
                                     review_mode="mask")
    sent = result.payload.anonymisierter_sachverhalt
    assert "Beispiel" not in sent and "Muster" not in sent and "./." in sent


# --- Bezeichnetes Zeichen mit Praefix, Rechnungsnummern-Spalte ---------------------------------------------------

@pytest.mark.parametrize("text, value", [
    ("Neuhain, 14.10.2026 · Unser Zeichen: AR 26/0571\nKündigungsschutzklage", "AR 26/0571"),
    ("Ihr Zeichen: KS 12/2026", "KS 12/2026"),
    ("Aktenzeichen: VU 2026/118842", "VU 2026/118842"),
])
def test_labelled_reference_with_letter_prefix(text: str, value: str) -> None:
    assert value in [s.value for s in detect_aktenzeichen(text)]


def test_prefixed_reference_needs_a_label() -> None:
    assert "AR 26/0571" not in [s.value for s in detect_aktenzeichen("Siehe Tabelle AR 26/0571 im Anhang.")]
    # oeffentliche Fundstelle: "AZR" ist kein Kennwort "Az"
    assert "R 123/20" not in [s.value for s in detect_aktenzeichen("BAG, Urt. v. 05.05.2021 - 2 AZR 123/20")]


def test_invoice_numbers_in_a_table_column_also_across_ocr_blank_lines() -> None:
    text = ("Rechnungs-Nr. | Rechnungsdatum | Betrag\nMW-26-08812 | 18.08.2026 | 2.890,00 EUR\n\n"
            "MW-26-08813 | 18.08.2026 | 495,00 EUR\n\nSumme offen\nX-1234 | 9")
    assert [s.value for s in detect_rechnungsnummer(text)] == ["MW-26-08812", "MW-26-08813"]


def test_table_helper_default_still_stops_at_blank_lines() -> None:
    """Unveraendertes Standardverhalten fuer die bisherigen Aufrufer (Geburtsdatum, Grundbuch)."""
    import re

    text = "Name | Wert\nA | 111\n\nB | 222"
    header, value = re.compile(r"\s*Wert\s*"), re.compile(r"\d+")
    assert [s.value for s in _table_column_values(text, header, value, "x")] == ["111"]
    assert [s.value for s in _table_column_values(text, header, value, "x", across_blank_lines=True)] == ["111", "222"]


def test_new_rules_are_part_of_detect_all() -> None:
    values = {s.value for s in detect_all("Kurzrubrum: Beispiel ./. Muster. Unser Zeichen: AR 26/0571")}
    assert {"Beispiel", "Muster", "AR 26/0571"} <= values


# --- Korpus eingefroren -------------------------------------------------------------------------------------------

def test_full_document_corpus_is_frozen_and_valid() -> None:
    assert hashlib.sha256(CORPUS.read_bytes()).hexdigest() == CORPUS_SHA256, "Korpus nur versioniert aendern"
    data = json.loads(CORPUS.read_text(encoding="utf-8"))
    assert len(data["documents"]) == 24
    for document in data["documents"]:
        table = document.get("table") or {}
        text = "\n".join(document["paragraphs"]) + "\n" + "\n".join(
            " | ".join(map(str, row)) for row in [table.get("header", []), *table.get("rows", [])])
        assert all(item["value"] in text for item in document["pii"]), document["id"]
        assert all(keep in text for keep in document.get("keep", [])), document["id"]


# --- §43.11: Korpus v2 (Dev zur Entwicklung, blinder Test nur einmal gemessen) eingefroren ------------------------

V2 = Path(__file__).resolve().parents[1] / "benchmarks" / "pii_full_documents_v2" / "cases"
V2_SHA256 = {"dev.json": "ded5418f34c034b3d753d2ed49b27f35d56306e367ce0fce3e4d73e77fd4866d",
             "test.json": "1498fe064e6a9f9042d70ed4ed12f7f248b21f0858cef4dc965ab861f39d581c"}


def test_v2_splits_are_frozen_disjoint_and_valid() -> None:
    ids: dict[str, set[str]] = {}
    for name, digest in V2_SHA256.items():
        path = V2 / name
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest, f"{name} nur versioniert aendern"
        data = json.loads(path.read_text(encoding="utf-8"))
        ids[name] = {d["id"] for d in data["documents"]}
        for document in data["documents"]:
            table = document.get("table") or {}
            text = "\n".join(document["paragraphs"]) + "\n" + "\n".join(
                " | ".join(map(str, row)) for row in [table.get("header", []), *table.get("rows", [])])
            for key in ("keep", "keep_gewoehnlich"):
                assert all(value in text for value in document.get(key, [])), (document["id"], key)
            assert all(item["value"] in text for item in document["pii"]), document["id"]
    assert (len(ids["dev.json"]), len(ids["test.json"])) == (12, 24)
    assert not ids["dev.json"] & ids["test.json"]


# --- §43.11: Regeln aus dem Dev-Split von pii_full_documents_v2 --------------------------------------------------

@pytest.mark.parametrize("text, expected", [
    ("Am Weiher 11, Wohnung 3. OG\nlinks, 34567 Musterstadt", ["Am Weiher 11", "Wohnung 3. OG\nlinks"]),
    ("Herrn Jonas Schäfer\nHohe Leuchte 7\n34590 Fiktivhausen", ["Hohe Leuchte 7"]),
    ("Auf der Höhe 15, Hinterhaus, 34612 Beispielheim", ["Auf der Höhe 15", "Hinterhaus"]),
    ("Hinter den Gärten 8, 34590\nFiktivhausen", ["Hinter den Gärten 8"]),
    ("Im Winkel 9, 2. OG rechts, 34590 Fiktivhausen", ["Im Winkel 9", "2. OG rechts"]),
])
def test_streets_without_street_word_are_found_before_a_postcode(text: str, expected: list[str]) -> None:
    values = [s.value for s in detect_all(text)]
    assert all(value in values for value in expected), values


@pytest.mark.parametrize("text", [
    "Im Jahr 2025, 34590 Fiktivhausen",            # Jahreszahl, keine Hausnummer
    "Am 12.03.2026 ging die Klage ein.",          # kein PLZ-Anker
    "Mietminderung Im Winkel 9 wegen Schimmel",   # Strasse ohne PLZ: nur ueber Wiederholung erfasst, nicht hier
])
def test_prefixed_street_rule_needs_a_postcode_anchor(text: str) -> None:
    from app.privacy.detectors import detect_address

    assert not [s for s in detect_address(text) if s.value.startswith(("Im ", "Am "))]


def test_birth_names_after_geb_and_geborene() -> None:
    from app.privacy.detectors import detect_birth_name

    text = "Hanna Sommer geb. Winter; Lena Fuchs geborene Weiß; Petra Wolf geb. Klein, geb. 23.09.1979"
    assert [s.value for s in detect_birth_name(text)] == ["Winter", "Weiß", "Klein"]
    assert {"Winter", "Weiß", "Klein"} <= {s.value for s in detect_all(text)}


@pytest.mark.parametrize("text, value", [
    ("Partnerschaftsregister Amtsgericht Musterstadt PR\n2291 · Tel.", "PR\n2291"),
    ("Registergericht Fiktivhausen HRB 4471", "HRB 4471"),
    ("Vereinsregister VR 1234", "VR 1234"),
])
def test_register_numbers(text: str, value: str) -> None:
    from app.privacy.detectors import detect_referenznummer

    assert value in [s.value for s in detect_referenznummer(text)]


def test_register_rule_ignores_words_and_amounts() -> None:
    from app.privacy.detectors import detect_referenznummer

    assert detect_referenznummer("Die PR-Agentur verlangte 2.291,00 EUR.") == []
