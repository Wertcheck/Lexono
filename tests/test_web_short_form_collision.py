"""Option C im Schriftsatz-Generator (24.10.2026): Kurzform-Kollision -> keine Payload an den Schreib-Provider, feste
Meldung ohne Originalwerte; nach der Umformulierung laeuft der Auftrag. Synthetische Daten, Test-Double statt Cloud."""

from __future__ import annotations

from urllib.parse import unquote_plus

from tests.test_web_form_name_protection import _generate, _matter, _sent, client, db_session, writer  # noqa: F401

COLLISION = "Die Bremer Grundbesitz GmbH (BGB) zahlt nicht. Die BGB verweigert die Zahlung. Nach § 823 BGB haftet sie."
REWORDED = "Die Bremer Grundbesitz GmbH zahlt nicht. Die Gesellschaft verweigert die Zahlung. Nach § 823 BGB haftet sie."


def test_schriftsatz_with_a_collision_sends_nothing_and_asks_for_rewording(client, db_session, writer) -> None:
    matter = _matter(db_session)
    response = _generate(client, matter.id, attorney_anmerkungen=COLLISION)
    assert writer.received_payloads == []
    shown = unquote_plus(response.headers.get("location", "")) + client.get(response.headers.get("location", "/")).text
    assert "die Gesellschaft" in shown
    for value in ("Bremer", "Grundbesitz", "(BGB)"):
        assert value not in unquote_plus(response.headers.get("location", ""))
    _generate(client, matter.id, attorney_anmerkungen=COLLISION)  # erneute Uebermittlung: wieder nichts gesendet
    assert writer.received_payloads == []
    _generate(client, matter.id, attorney_anmerkungen=REWORDED)
    assert len(writer.received_payloads) == 1
    sent = _sent(writer)
    assert "§ 823 BGB" in sent and "Bremer" not in sent
