"""P2.2 (§41): Integritaet des Korpus `benchmarks/classification_deadlines_v1` und der gemeinsamen Bewertung.

Der unabhaengige Testsplit ist eingefroren (SHA-256). Eine Aenderung muss begruendet, versioniert (neue Datei/Version)
und in benchmarks/classification_deadlines_v1/README.md dokumentiert werden - die urspruenglichen Ergebnisse bleiben."""

from scripts.legal_bench.cd_measure import load, score, sha256, validate

TEST_SPLIT_SHA256 = "a2cb80158685d1db22d34a4ed0706e99da4bcd83de1e114dfdcd863ed54fe9f2"


def test_both_splits_are_valid_and_disjoint() -> None:
    dev, test = load("dev"), load("test")
    assert validate(dev) == [] and validate(test) == []
    assert not {d["id"] for d in dev["documents"]} & {d["id"] for d in test["documents"]}
    assert len(dev["documents"]) >= 30 and len(test["documents"]) >= 30


def test_independent_test_split_is_frozen() -> None:
    assert sha256("test") == TEST_SPLIT_SHA256, "Testsplit geaendert - nur versioniert und begruendet zulaessig"


def test_corpus_covers_the_required_case_groups() -> None:
    tags = {tag for split in ("dev", "test") for d in load(split)["documents"] for tag in d["tags"]}
    for required in ("urteil", "anlage", "ocr", "tabelle", "lang", "widerspruch", "kuendigungsandrohung"):
        assert required in tags, required
    dev_tags = {tag for d in load("dev")["documents"] for tag in d["tags"]}
    assert {"eichschein", "erwaehnung", "bekannter_fehler"} <= dev_tags


def test_score_counts_non_deadlines_as_critical_and_respects_unscored_dates() -> None:
    data = {"documents": [{
        "id": "X1", "paragraphs": ["Eichschein gültig bis 31.12.2026. Frist bis zum 15.05.2026."], "tags": [],
        "label": {"typ": "Protokoll", "zulaessig": ["Protokoll"], "art": "kern"},
        "fristen": {"frist": ["15.05.2026"], "termin": [], "fristbeginn": [], "fristdauern": 0,
                    "keine_frist": ["31.12.2026"], "nicht_bewertet": []},
    }]}
    wrong = score(data, {"X1": {"typ": "Rechnung", "frist": ["31.12.2026"], "termin": [], "fristbeginn": [], "fristdauern": 0}})
    assert wrong["klassifikation"]["richtig"] == "0/1"
    assert {e["art"] for e in wrong["kritisch"]} == {"frist_uebersehen", "frist_falsch"}
    right = score(data, {"X1": {"typ": "Protokoll", "frist": ["15.05.2026"], "termin": [], "fristbeginn": [], "fristdauern": 0}})
    assert right["klassifikation"]["richtig"] == "1/1" and right["kritisch"] == []
    # fehlende Vorhersage (z. B. fail-closed nicht gesendet) zaehlt als Unbekannt ohne Daten
    missing = score(data, {})
    assert missing["klassifikation"]["richtig"] == "0/1" and missing["fristen"]["frist"]["uebersehen"] == 1
