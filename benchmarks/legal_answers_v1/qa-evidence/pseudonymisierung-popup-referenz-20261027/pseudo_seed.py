"""Isolierte QA-Instanz fuer das Pseudonymisierungs-Pop-up: eigene Ablage im Job-Ordner, toter Cloud-Endpunkt,
nur ein Wegwerf-Admin. Dokumente legt pseudo_shots.py (synthetisch) ueber die laufende App an.
Usage: pseudo_seed.py <dir> <port>"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

D, PORT = Path(sys.argv[1]), int(sys.argv[2])
assert "jobs" in str(D)
REPO = Path(__file__).resolve().parents[4]  # Repository-Wurzel
if D.exists():
    shutil.rmtree(D)
(D / "data").mkdir(parents=True)
db_path = (D / "data" / "kanzlei_ai.db").as_posix()
(D / ".env").write_text("\n".join([
    f"DATABASE_URL=sqlite:///{db_path}", "APP_ENV=development", "LOCAL_AI_ENABLED=false", "HOST=127.0.0.1",
    f"PORT={PORT}", "LEXONO_GATEWAY_URL=http://127.0.0.1:9", "LEXONO_GATEWAY_CLIENT_ID=qa-toter-endpunkt",
    "LEXONO_GATEWAY_TIMEOUT_SECONDS=5", "MAIL_AUTO_SYNC_ENABLED=false", "WEB_SEARCH_ENABLED=false",
    "LAW_UPDATE_CHECK_ENABLED=false",
    "SESSION_SECRET_KEY=qa-pseudo-wegwerf-" + os.urandom(16).hex(), ""]), encoding="utf-8")
(D / ".setup_complete").write_text("1", encoding="utf-8")
env = dict(os.environ, LEXONO_DATA_DIR=str(D), DATABASE_URL=f"sqlite:///{db_path}", PYTHONDONTWRITEBYTECODE="1")
for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL"):
    env.pop(k, None)
py = str(REPO / ".venv" / "Scripts" / "python.exe")
subprocess.run([py, str(REPO / "run.py"), "migrate"], cwd=str(D), env=env, check=True,
               stdout=open(D / "migrate.out", "w"), stderr=subprocess.STDOUT)

os.environ.update({k: v for k, v in env.items() if k in ("LEXONO_DATA_DIR", "DATABASE_URL")})
sys.path.insert(0, str(REPO))
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.auth.security import hash_password  # noqa: E402
from app.models import Role, User  # noqa: E402

engine = create_engine(f"sqlite:///{db_path}")
with Session(engine) as db:
    admin = db.query(Role).filter(Role.name.ilike("admin")).one()
    db.add(User(email="qa-pseudo@lexono.test", display_name="QA Pseudonymisierung", role_id=admin.id,
                password_hash=hash_password("QA-pseudo-0000"), must_change_password=False))
    db.commit()
print("ok", D)
