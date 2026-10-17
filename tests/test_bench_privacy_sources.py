"""§65: Datenschutz-Regressionstests mit Quellenbezug (benchmarks/privacy_sources_v1, scripts/bench_privacy_sources.py).

Juristische Problemstellungen aus amtlich veroeffentlichten Entscheidungen und amtliche Normtexte in vollstaendig
synthetischen Sachverhalten. Festgeschrieben ist der am 17.10.2026 gemessene Stand (Sperrklinke): 0 Lecks/Teil-Lecks,
Chronologie vollstaendig, Fail-Closed bei NER-Ausfall, keine Blockade. Bekannte Fehlmaskierungen (v. a. Aktenzeichen
realer BGH-/BAG-Entscheidungen in Zitaten und Gerichtsnamen) bleiben im Regressionsschutz: jede NEUE schlaegt fehl,
eine behobene darf hier gestrichen werden. Keine Netz-/Cloud-Aufrufe ausser dem lokalen Aufzeichnungsserver."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import bench_privacy_sources as bench  # noqa: E402

from tests.test_privacy_wire_payload import FAKE_KEY, _provider, recorder  # noqa: E402,F401  (Fixture)

KNOWN_KEEP_MISSING = {
    "Q02:BGH, Beschluss vom 2. Dezember 2025 – VIII ZR 274/23", "Q02:Räumungsklage",
    "Q03:2019", "Q04:BGH, Urteil vom 8. Oktober 2025 – VIII ZR 18/24", "Q04:personenidentisch",
    "Q07:Arbeitsgericht Frankfurt am Main", "Q08:2 AZR 72/24",
}
# §66 behoben (ausgeschriebener Monat in der kanonischen Bundesgericht-Zitierform; "Unterabs."/"Buchst." wie "Abs."):
# Q05/Q07/Q10 Entscheidungszitate, Q09/Q10 "Art. 6 Abs. 1 Unterabs. 1 Buchst. f DSGVO" - jetzt erhalten und
# festgeschrieben (test_fixed_values_stay_preserved). Q02/Q04 bleiben bewusst maskiert: Hinweis auf eigene Mandanten
# im Umfeld (Schutz eigener Verfahren, §33); Q08 ist die arabische Kurzform ohne Datum (bleibt maskiert, §33).
FIXED_IN_S66 = {
    "Q05:BAG, Urteil vom 20. Februar 2025 – 6 AZR 32/24", "Q07:BAG, Urteil vom 3. April 2025 – 2 AZR 72/24",
    "Q09:Art. 6 Abs. 1 Unterabs. 1 Buchst. f DSGVO", "Q10:Art. 6 Abs. 1 Unterabs. 1 Buchst. f DSGVO",
    "Q10:BGH, Urteil vom 14. Oktober 2025 – VI ZR 431/24",
    # §67: oeffentliche Gerichtsbezeichnungen (Q07 bleibt maskiert: "Frankfurt am Main" ist dort auch Wohnanschrift)
    "Q02:Amtsgericht Köpenick", "Q05:Amtsgericht Kiel", "Q10:Landgericht Osnabrück",
}
KNOWN_UNREFERENCED_MASKS = {
    "Q01:ADRESSE:10435 Berlin", "Q01:ADRESSE:12557 Berlin", "Q01:ADRESSE:Kastanienallee 41", "Q01:PERSON:Berlin",
    "Q01:PERSON:Bestandswand", "Q01:PERSON:Wohnbau Lindenhof GmbH", "Q02:KENNUNG:VIII ZR 274/23",
    "Q02:ORGANISATION:Räumungsklage Wohnbau Lindenhof GmbH ./.", "Q02:PERSON:Sohn",
    "Q02:PERSON:Wissen", "Q03:ADRESSE:14476 Potsdam", "Q03:ORT:Haus im Jahr 2019", "Q04:KENNUNG:VIII ZR 18/24",
    "Q04:ORGANISATION:veräußernden Gesellschaft personenidentisch", "Q04:ORT:Anderkonto", "Q04:ORT:Potsdam",
    "Q07:ORT:Frankfurt am Main",
    "Q07:ORT:am Main", "Q07:PERSON:Gesprächsnotiz", "Q07:PERSON:Integrationsamts", "Q08:KENNUNG:2 AZR 72/24",
    "Q08:PERSON:Tätigkeit", "Q09:PERSON:Identitätsabgleich",
    "Q10:ORT:Klageentwurf", "Q10:PERSON:Erfolgsaussicht",
    "Q11:PERSON:Sohn",
}


@pytest.fixture(scope="module")
def outcome() -> dict:
    return bench.run()


def test_corpus_is_valid_and_uses_only_released_sources() -> None:
    corpus = bench.load_corpus()
    assert bench.validate(corpus) == []
    assert len(corpus["cases"]) == 11 and len(corpus["problems"]) == 5
    register = {s["id"]: s for s in json.loads(bench.REGISTER.read_text(encoding="utf-8"))["sources"]}
    used = {ref for p in corpus["problems"] for ref in p["source_refs"]}
    assert used == {"Q-GESETZE-IM-INTERNET", "Q-RECHTSPRECHUNG-IM-INTERNET"}
    for sid, entry in register.items():
        if sid in used:
            assert entry["nutzungsrechte"]["status"] == "nutzbar" and entry["rechtliche_grundlage"].startswith("§ 5")
            assert entry["abruf"]["datum"] and entry["verwendungszweck"] and entry["redaktionelle_bestandteile"]
        else:
            assert entry["nutzungsrechte"]["status"] != "nutzbar" and entry["verwendet_in"] == []  # weiter gesperrt


def test_the_validator_rejects_invented_values_and_unreleased_sources() -> None:
    corpus = bench.load_corpus()
    corpus["cases"][0]["keep"].append("99.999,99 EUR")  # steht nicht im Text
    corpus["problems"][0]["source_refs"].append("Q-OPENJUR")  # gesperrte Quelle
    errors = bench.validate(corpus)
    assert any("99.999,99" in e for e in errors) and any("Q-OPENJUR" in e for e in errors)


def test_no_leak_no_partial_leak_no_block(outcome: dict) -> None:
    summary = outcome["summary"]
    assert summary["leaks"] == [] and summary["partial_leaks"] == []
    assert summary["protected"] == summary["gold"] == 52
    assert summary["blocked"] == [] and summary["errors"] == []


def test_chronology_is_preserved(outcome: dict) -> None:
    assert outcome["summary"]["chronology_preserved"] == "11/11"


def test_ner_failure_never_creates_a_payload(outcome: dict) -> None:
    assert outcome["fail_closed"] and all(not r["payload_created"] for r in outcome["fail_closed"])
    assert outcome["summary"]["fail_closed"] == "22/22"


def test_lost_legal_values_and_unreferenced_masks_do_not_get_worse(outcome: dict) -> None:
    summary = outcome["summary"]
    new_missing = set(summary["keep_missing"]) - KNOWN_KEEP_MISSING
    assert not new_missing, f"neu verlorene juristische Werte: {sorted(new_missing)}"
    new_masks = set(summary["false_positives"]) - KNOWN_UNREFERENCED_MASKS
    assert not new_masks, f"neue Maskierungen ohne Referenz: {sorted(new_masks)}"


def test_fixed_values_stay_preserved(outcome: dict) -> None:
    assert not FIXED_IN_S66 & set(outcome["summary"]["keep_missing"])
    assert outcome["summary"]["keep_preserved"] == "109/116"


def test_statute_texts_survive_in_the_payload(outcome: dict) -> None:
    """Woertliche Normtexte (Gesetze im Internet) bleiben lesbar - Fristenregeln duerfen nicht verloren gehen."""
    rows = {row["id"]: row for row in outcome["rows"]}
    for cid, phrase in (("Q11", "spätestens am dritten Werktag eines Kalendermonats zum Ablauf des übernächsten Monats"),
                        ("Q04", "an einen Dritten verkauft, so ist der Mieter zum Vorkauf berechtigt"),
                        ("Q04", "Ablauf von zwei Monaten"), ("Q05", "Grund und der Betrag der Forderung"),
                        ("Q01", "schuldhaft nicht unerheblich verletzt")):
        assert phrase not in rows[cid]["keep_missing"], (cid, phrase)


def test_wire_payload_of_a_source_case_contains_no_protected_value(recorder) -> None:
    """Tatsaechlich ausgehende HTTP-Anfrage (lokaler Aufzeichnungsserver, gefaelschter Schluessel)."""
    from app.privacy.gateway import ClaudePrivacyGateway

    case = next(c for c in bench.load_corpus()["cases"] if c["id"] == "Q09")
    result = bench.base._call(ClaudePrivacyGateway(), case)
    assert result.allowed
    _provider().write(result.payload)
    assert len(recorder.requests) == 1
    wire = recorder.requests[0]["raw"].decode("utf-8")
    decoded = json.dumps(json.loads(wire), ensure_ascii=False)
    for entry in case["protect"]:
        assert entry["value"] not in wire and entry["value"] not in decoded, entry["type"]
    for value in ("09.03.2026", "01.06.2023", "31.05.2025", "Positivdaten", "Betrugsprävention", "36 Monaten"):
        assert value in decoded, value
    assert recorder.requests[0]["headers"].get("x-api-key") == FAKE_KEY
