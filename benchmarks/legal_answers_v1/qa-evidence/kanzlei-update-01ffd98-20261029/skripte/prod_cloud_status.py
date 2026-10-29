"""Cloud-Verbindung und Statusanzeige des Produktivsystems getrennt pruefen - OHNE Inhalte zu senden und ohne
Zugangsdaten auszugeben: Konfigurationsart (nur ja/nein), Erreichbarkeit des Ziels (TCP/TLS-Handshake), letzter
Aufrufstatus aus dem Protokoll (lesend) und die daraus berechnete Statusanzeige (Logik der App).
Usage: prod_cloud_status.py <out_json>"""
import json, os, sys
from pathlib import Path

sys.path.insert(0, r"C:\Users\Bonit\Lexono")
os.environ.pop("LEXONO_DATA_DIR", None)
os.environ.pop("DATABASE_URL", None)
OUT = Path(sys.argv[1])
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.ai_providers import cloud_health  # noqa: E402
from app.config.settings import get_settings  # noqa: E402

s = get_settings()
R = {"konfiguration": {"gateway_url_gesetzt": bool(s.lexono_gateway_url), "anthropic_schluessel_gesetzt": bool(getattr(s, "anthropic_api_key", None)),
                       "route": cloud_health.cloud_route(s)}}
target = cloud_health.probe_target(s)
R["ziel_vorhanden"] = target is not None
if target:
    host, port, tls = target
    ok, detail = cloud_health.probe_reachability(host, port, tls)
    R["erreichbarkeit"] = {"host": host, "port": port, "tls": tls, "erreichbar": ok, "detail": detail[:120]}
engine = create_engine(f"sqlite:///file:{Path(r'C:/ProgramData/Lexono/data/kanzlei_ai.db').as_posix()}?mode=ro&uri=true")
with Session(engine) as db:
    R["letzte_anfrage_fehlgeschlagen"] = cloud_health.last_request_failed(db)
health = cloud_health.compute_cloud_health(s, session_factory=lambda: Session(engine))
R["statusanzeige"] = {k: str(v) for k, v in vars(health).items()} if hasattr(health, "__dict__") else {f: str(getattr(health, f)) for f in health._fields}
OUT.write_text(json.dumps(R, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps(R, ensure_ascii=False, indent=1))
