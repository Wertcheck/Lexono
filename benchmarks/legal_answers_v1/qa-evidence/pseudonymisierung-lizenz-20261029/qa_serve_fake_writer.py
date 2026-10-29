"""NUR QA: startet den Lexono-Server (Quellstand) mit einem lokalen, verzoegerten Fake-Schreibmodell statt des
Cloud-Providers (synthetisch, kein Netz). Verzoegerung und Modus kommen aus der Umgebung:
QA_FAKE_DELAY (Sekunden, Standard 20), QA_FAKE_MODE ("ok" | "error"). Jeder Aufruf wird in QA_FAKE_LOG protokolliert
(nur Zeitstempel und Laenge des pseudonymisierten Prompts, kein Inhalt), damit Doppelaufrufe zaehlbar sind.
Usage: qa_serve_fake_writer.py <port>"""
import json
import os
import sys
import time

sys.path.insert(0, r"C:\Users\Bonit\Lexono")
port = int(sys.argv[1])
DELAY = float(os.environ.get("QA_FAKE_DELAY", "20"))
MODE = os.environ.get("QA_FAKE_MODE", "ok")
LOG = os.environ.get("QA_FAKE_LOG", "")

from app.ai_providers.claude_writing_provider import ClaudeWritingResult  # noqa: E402

TEXT = ("An das Finanzamt Musterstadt\n\nEinspruch gegen den Bescheid vom 02.02.2024\n\n"
        "Sehr geehrte Damen und Herren,\n\nnamens und in Vollmacht unserer Mandantschaft legen wir Einspruch ein "
        "(synthetischer QA-Entwurf).\n\nMit freundlichen Gruessen\n\n"
        "Offene Fragen:\n1. Liegt der Bescheid im Original vor?\n2. Wann wurde der Bescheid zugestellt?")


class SlowFakeWriter:
    def _log(self, payload):
        if LOG:
            with open(LOG, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"t": time.time(), "len": len(str(payload))}) + "\n")

    def write(self, payload):
        self._log(payload)
        time.sleep(DELAY)
        if MODE == "error":
            raise RuntimeError("QA: simulierter Providerfehler")
        return ClaudeWritingResult(text=TEXT, token_count=120, input_tokens=80, output_tokens=40)


from app.web import service_factory  # noqa: E402

service_factory.build_writing_provider = lambda settings: SlowFakeWriter()
import uvicorn  # noqa: E402

from app.main import app  # noqa: E402

uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
