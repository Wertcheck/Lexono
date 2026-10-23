"""§90 (I12 v2): Schluesselmodell des Owners - Wurzelschluessel, signierte Schluesselliste, Widerruf, Rotation.

Konzept: AUDIT_S89.md §2.3. Zwei Stufen:
- WURZELSCHLUESSEL (offline, verschluesselte Datei auf einem USB-Datentraeger): signiert NUR die Schluesselliste. Sein
  Fingerabdruck (SHA-256 des oeffentlichen Ed25519-Schluessels) steht im geprueften Code (`PINNED_ROOT_FINGERPRINTS`).
  Heute ist KEIN Wurzelschluessel festgelegt -> das Produktiv-Gate bleibt geschlossen.
- FREIGABESCHLUESSEL (Owner, verschluesselte Datei auf dem Host, NIE in der VM): signiert Bestaetigungen. Er gilt nur,
  solange er in einer gueltigen, vom Wurzelschluessel signierten Schluesselliste aktiv und nicht widerrufen ist.

Rotation = neue Schluesselliste mit hoeherer Version (mehrere aktive Schluessel zur Ueberlappung erlaubt). Widerruf =
Eintrag unter `widerrufen`. Rueckspielen einer alten Liste: begrenzt durch die Laufzeit der Liste (max. 90 Tage) und
hart durch `MIN_KEYLIST_VERSION` im geprueften Code (nach einem Widerruf anzuheben). Jede Bestaetigung ist zusaetzlich an
Version und SHA-256 der Liste gebunden.

Private Schluessel: nur verschluesselt gespeichert (scrypt RFC 7914, N=2^17, r=8, p=1 -> AES-256-GCM; Zweck und
Schluessel-ID als zusaetzliche authentifizierte Daten). Passphrasen werden nie gespeichert, protokolliert oder als
Argument/Umgebungsvariable angenommen (siehe owner_sign.py)."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta

KEYLIST_SCHEMA = "lexono-owner-keylist/1"
KEYFILE_SCHEMA = "lexono-owner-private-key/1"
OWNER_ROLE = "Owner"
#: Fingerabdruecke (SHA-256 des rohen oeffentlichen Schluessels) zugelassener Wurzelschluessel. Leer = kein Wurzelschluessel
#: festgelegt -> Gate geschlossen. Eintrag nur per reviewter Code-Aenderung nach Erzeugung durch den Owner (AUDIT_S90.md).
PINNED_ROOT_FINGERPRINTS: tuple[str, ...] = ()
#: Mindestversion der Schluesselliste (Schutz gegen Rueckspielen einer Liste vor einem Widerruf; per Review anzuheben).
MIN_KEYLIST_VERSION = 1
ROOT_MISSING = "kein Wurzelschluessel des Owners festgelegt (PINNED_ROOT_FINGERPRINTS leer) - Gate geschlossen"
MAX_KEYLIST_VALIDITY = timedelta(days=90)
MAX_KEY_VALIDITY = timedelta(days=90)
PURPOSES = ("wurzel", "freigabe")
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2 ** 17, 8, 1
MIN_PASSPHRASE_LENGTH = 15


class KeyFileError(ValueError):
    """Schluesseldatei unlesbar, zu schwach geschuetzt oder Passphrase falsch - nichts wird signiert."""


def canonical(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def fingerprint(public_key) -> str:  # noqa: ANN001
    return hashlib.sha256(public_key.public_bytes_raw()).hexdigest()


def public_key_from_hex(value: str):  # noqa: ANN201
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    return Ed25519PublicKey.from_public_bytes(bytes.fromhex(value))


# --- Schluesselliste ------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class KeyList:
    version: int
    sha256: str
    anchors: dict  # key_id -> {"key", "inhaber", "rolle", "gueltig_von", "gueltig_bis"}
    revoked: dict  # key_id -> {"seit", "grund"}
    root_fingerprint: str


def _time(value) -> datetime:  # noqa: ANN001
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("Zeitangabe ohne Zeitzone")
    return parsed


def verify_keylist(raw: bytes, *, pinned_roots: tuple[str, ...], min_version: int, now: datetime,
                   test_only: bool) -> tuple[KeyList | None, list[str]]:
    """Prueft die signierte Schluesselliste. Jede Unklarheit oder jeder Widerspruch verwirft die GANZE Liste."""
    if not pinned_roots:
        return None, [ROOT_MISSING]
    try:
        document = json.loads(raw.decode("utf-8"))
        payload, signature = document["payload"], bytes.fromhex(document["signature_hex"])
        root = public_key_from_hex(document["root_public_key_hex"])
    except Exception as exc:  # noqa: BLE001 - unlesbar = geschlossen
        return None, [f"Schluesselliste unlesbar ({type(exc).__name__})"]
    if document.get("schema") != KEYLIST_SCHEMA:
        return None, ["Schluesselliste hat ein unbekanntes Schema"]
    root_fp = fingerprint(root)
    if root_fp not in pinned_roots:
        return None, ["Schluesselliste nicht vom festgelegten Wurzelschluessel (fremder Wurzelschluessel)"]
    try:
        root.verify(signature, canonical(payload))
    except Exception:  # noqa: BLE001
        return None, ["Signatur der Schluesselliste ungueltig (manipuliert oder fremd)"]
    problems: list[str] = []
    if bool(payload.get("test_only")) != test_only:
        problems.append("Test-Schluesselliste im Produktivbetrieb bzw. umgekehrt - abgelehnt")
    version = payload.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version < min_version:
        problems.append(f"Schluesselliste veraltet oder ohne Version ({version!r} < Mindestversion {min_version})")
    try:
        issued, expires = _time(payload["ausgestellt"]), _time(payload["gueltig_bis"])
    except (KeyError, ValueError, TypeError):
        return None, problems + ["Gueltigkeitsangaben der Schluesselliste fehlen oder sind ungueltig"]
    if not issued <= now <= expires:
        problems.append("Schluesselliste abgelaufen oder noch nicht gueltig")
    if expires - issued > MAX_KEYLIST_VALIDITY:
        problems.append("Gueltigkeit der Schluesselliste laenger als 90 Tage")
    anchors: dict = {}
    revoked: dict = {}
    for entry in payload.get("widerrufen") or []:
        if not isinstance(entry, dict) or not entry.get("key_id") or not entry.get("seit"):
            problems.append("Widerrufseintrag unvollstaendig")
            continue
        revoked[entry["key_id"]] = {"seit": entry["seit"], "grund": entry.get("grund")}
    keys = payload.get("schluessel")
    if not isinstance(keys, list) or not keys:
        problems.append("Schluesselliste ohne Freigabeschluessel")
        keys = []
    for entry in keys:
        try:
            key_id = entry["key_id"]
            key = public_key_from_hex(entry["public_key_hex"])
            valid_from, valid_to = _time(entry["gueltig_von"]), _time(entry["gueltig_bis"])
            holder, role = entry["inhaber"], entry["rolle"]
        except Exception as exc:  # noqa: BLE001
            problems.append(f"Schluesseleintrag unvollstaendig ({type(exc).__name__})")
            continue
        if key_id in anchors:
            problems.append(f"Schluessel-ID {key_id!r} doppelt (widerspruechlich)")
        if key_id in revoked:
            problems.append(f"Schluessel {key_id!r} zugleich aktiv und widerrufen (widerspruechlich)")
        if fingerprint(key) == root_fp:
            problems.append("Wurzelschluessel als Freigabeschluessel eingetragen (Trennung verletzt)")
        if role != OWNER_ROLE or not holder:
            problems.append(f"Schluessel {key_id!r}: Rolle muss {OWNER_ROLE!r} mit benanntem Inhaber sein")
        if valid_to - valid_from > MAX_KEY_VALIDITY or valid_to <= valid_from:
            problems.append(f"Schluessel {key_id!r}: Gueltigkeit unzulaessig (max. 90 Tage)")
        anchors[key_id] = {"key": key, "inhaber": holder, "rolle": role, "gueltig_von": valid_from,
                           "gueltig_bis": valid_to}
    if problems:
        return None, problems
    return KeyList(version=version, sha256=hashlib.sha256(raw).hexdigest(), anchors=anchors, revoked=revoked,
                   root_fingerprint=root_fp), []


def sign_keylist(root_key, payload: dict) -> dict:  # noqa: ANN001
    return {"schema": KEYLIST_SCHEMA, "root_public_key_hex": root_key.public_key().public_bytes_raw().hex(),
            "payload": payload, "signature_hex": root_key.sign(canonical(payload)).hex()}


# --- Verschluesselte private Schluessel -------------------------------------------------------------------------------------


def _aad(purpose: str, key_id: str) -> bytes:
    return canonical({"schema": KEYFILE_SCHEMA, "zweck": purpose, "key_id": key_id})


def check_passphrase(passphrase: str) -> None:
    if not isinstance(passphrase, str) or len(passphrase) < MIN_PASSPHRASE_LENGTH:
        raise KeyFileError(f"Passphrase zu kurz (mindestens {MIN_PASSPHRASE_LENGTH} Zeichen)")


def encrypt_private_key(key, passphrase: str, *, purpose: str, key_id: str, n: int = SCRYPT_N) -> dict:  # noqa: ANN001
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

    if purpose not in PURPOSES:
        raise KeyFileError(f"unbekannter Zweck {purpose!r}")
    check_passphrase(passphrase)
    salt, nonce = os.urandom(16), os.urandom(12)
    secret = Scrypt(salt=salt, length=32, n=n, r=SCRYPT_R, p=SCRYPT_P).derive(passphrase.encode("utf-8"))
    from cryptography.hazmat.primitives import serialization

    seed = key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
    ciphertext = AESGCM(secret).encrypt(nonce, seed, _aad(purpose, key_id))
    return {"schema": KEYFILE_SCHEMA, "zweck": purpose, "key_id": key_id,
            "public_key_hex": key.public_key().public_bytes_raw().hex(),
            "kdf": {"name": "scrypt", "n": n, "r": SCRYPT_R, "p": SCRYPT_P, "salt_hex": salt.hex()},
            "cipher": {"name": "AES-256-GCM", "nonce_hex": nonce.hex(), "ciphertext_hex": ciphertext.hex()}}


def decrypt_private_key(document: dict, passphrase: str, *, purpose: str, min_n: int = SCRYPT_N):  # noqa: ANN201
    """Entschluesselt NUR mit passendem Zweck und ausreichend starker Ableitung; Fehler verraten keine Details."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

    try:
        if document["schema"] != KEYFILE_SCHEMA or document["zweck"] != purpose:
            raise KeyFileError(f"Schluesseldatei ist nicht fuer den Zweck {purpose!r}")
        kdf, cipher = document["kdf"], document["cipher"]
        if kdf["name"] != "scrypt" or cipher["name"] != "AES-256-GCM" or int(kdf["n"]) < min_n \
                or int(kdf["r"]) < SCRYPT_R or int(kdf["p"]) < SCRYPT_P:
            raise KeyFileError("Schluesseldatei zu schwach geschuetzt (scrypt-Parameter unter dem Minimum)")
        secret = Scrypt(salt=bytes.fromhex(kdf["salt_hex"]), length=32, n=int(kdf["n"]), r=int(kdf["r"]),
                        p=int(kdf["p"])).derive(passphrase.encode("utf-8"))
        seed = AESGCM(secret).decrypt(bytes.fromhex(cipher["nonce_hex"]), bytes.fromhex(cipher["ciphertext_hex"]),
                                      _aad(document["zweck"], document["key_id"]))
        key = Ed25519PrivateKey.from_private_bytes(seed)
    except KeyFileError:
        raise
    except Exception:  # noqa: BLE001 - falsche Passphrase und Manipulation sind nicht unterscheidbar
        raise KeyFileError("Schluesseldatei nicht entschluesselbar (Passphrase falsch oder Datei veraendert)") from None
    if key.public_key().public_bytes_raw().hex() != document.get("public_key_hex"):
        raise KeyFileError("oeffentlicher Schluessel der Datei passt nicht zum privaten Schluessel")
    return key
