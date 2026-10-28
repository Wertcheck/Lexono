"""Hersteller-Werkzeug fuer Modul-Lizenzen (28.10.2026) - NICHT Teil der ausgelieferten App-Logik.

    python scripts/lexono_license_tool.py keygen --out-dir <SICHERER ORDNER AUSSERHALB DES REPOSITORYS>
    python scripts/lexono_license_tool.py sign --key <ordner>/lexono_license_private.pem --licensee "Kanzlei X" \
        --modules finanzen,steuern --valid-from 2026-11-01 --valid-until 2027-10-31 --out kanzlei-x.lexono-license
    python scripts/lexono_license_tool.py verify kanzlei-x.lexono-license

keygen schreibt den privaten Schluessel (PEM, optional mit Passwort ueber LEXONO_LICENSE_KEY_PASSWORD) und gibt
key_id + oeffentlichen Schluessel aus; NUR diese zwei Werte gehoeren in app/modules/licensing.TRUSTED_PUBLIC_KEYS.
Der private Schluessel darf niemals ins Repository, in Logs oder auf Kanzleirechner.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.modules.catalog import LICENSED_MODULES  # noqa: E402
from app.modules.licensing import key_id_for, verify_license_text  # noqa: E402


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _raw_public(key: Ed25519PrivateKey) -> bytes:
    return key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def build_license(private_key: Ed25519PrivateKey, *, licensee: str, modules: list[str], valid_from: date,
                  valid_until: date | None, license_id: str | None = None) -> str:
    unknown = [m for m in modules if m not in LICENSED_MODULES]
    if unknown or not modules:
        raise SystemExit(f"Unbekannte/fehlende Module: {unknown or modules}; erlaubt: {sorted(LICENSED_MODULES)}")
    payload = {"license_id": license_id or str(uuid.uuid4()), "licensee": licensee, "modules": sorted(set(modules)),
               "valid_from": valid_from.isoformat(), "valid_until": valid_until.isoformat() if valid_until else None,
               "issued_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    payload_bytes = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return json.dumps({"format": 1, "key_id": key_id_for(_raw_public(private_key)), "payload": _b64url(payload_bytes),
                       "signature": _b64url(private_key.sign(payload_bytes))}, indent=1)


def _password() -> bytes | None:
    value = os.environ.get("LEXONO_LICENSE_KEY_PASSWORD")
    return value.encode("utf-8") if value else None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    k = sub.add_parser("keygen"); k.add_argument("--out-dir", required=True)
    s = sub.add_parser("sign")
    s.add_argument("--key", required=True); s.add_argument("--licensee", required=True)
    s.add_argument("--modules", required=True); s.add_argument("--valid-from", default=date.today().isoformat())
    s.add_argument("--valid-until"); s.add_argument("--out", required=True)
    v = sub.add_parser("verify"); v.add_argument("file")
    args = parser.parse_args()

    if args.cmd == "keygen":
        out = Path(args.out_dir).resolve()
        if (Path(__file__).resolve().parents[1]) in out.parents or out == Path(__file__).resolve().parents[1]:
            raise SystemExit("Der private Schluessel darf nicht im Repository liegen.")
        out.mkdir(parents=True, exist_ok=True)
        target = out / "lexono_license_private.pem"
        if target.exists():
            raise SystemExit(f"{target} existiert bereits - nicht ueberschrieben.")
        key = Ed25519PrivateKey.generate()
        enc = serialization.BestAvailableEncryption(_password()) if _password() else serialization.NoEncryption()
        target.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, enc))
        raw = _raw_public(key)
        print(f"Privater Schluessel: {target}  (sicher verwahren, nie weitergeben)")
        print(f'TRUSTED_PUBLIC_KEYS-Eintrag: "{key_id_for(raw)}": "{base64.b64encode(raw).decode()}"')
    elif args.cmd == "sign":
        key = serialization.load_pem_private_key(Path(args.key).read_bytes(), password=_password())
        text = build_license(key, licensee=args.licensee, modules=[m.strip() for m in args.modules.split(",") if m.strip()],
                             valid_from=date.fromisoformat(args.valid_from),
                             valid_until=date.fromisoformat(args.valid_until) if args.valid_until else None)
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"Lizenz geschrieben: {args.out}")
    else:
        status = verify_license_text(Path(args.file).read_text(encoding="utf-8"))
        print(("GUELTIG: " if status.valid else "UNGUELTIG: ") + status.reason, sorted(status.modules), status.licensee)


if __name__ == "__main__":
    main()
