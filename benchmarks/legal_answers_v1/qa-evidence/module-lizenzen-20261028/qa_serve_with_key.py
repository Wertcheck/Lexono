"""NUR QA: startet den Lexono-Server (Quellstand) mit einem zur Laufzeit uebergebenen Wegwerf-Pruefschluessel in
TRUSTED_PUBLIC_KEYS. Die App selbst hat bewusst keinen Mechanismus dafuer (kein Env-Schalter, keine Konfiguration).
Usage: qa_serve_with_key.py <key_id> <public_key_b64> <port>"""
import sys

sys.path.insert(0, r"<REPOSITORY>")
key_id, pub, port = sys.argv[1], sys.argv[2], int(sys.argv[3])
from app.modules import licensing  # noqa: E402

licensing.TRUSTED_PUBLIC_KEYS[key_id] = pub
import uvicorn  # noqa: E402

from app.main import app  # noqa: E402

uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
