"""§90 (I12 v2): Werkzeug des Owners - Schluessel erzeugen, Schluesselliste signieren, Urteil pruefen, Bestaetigung signieren.

Laeuft AUF DEM HOST (nie in der VM): Das Signieren wird verweigert, wenn die Maschine, auf der signiert wird, die
geprufte Maschine der Bestaetigung ist. Claude bzw. der Benchmark kennen die Passphrase nicht: sie wird ausschliesslich
interaktiv abgefragt (getpass), nie als Argument, Umgebungsvariable, Datei oder Log angenommen bzw. ausgegeben.

Signiert wird NUR, wenn das automatisch aus den Belegen berechnete Urteil fuer alle 13 Abnahmetests PASS ist
(isolation_evidence.evaluate) - unabhaengig von jeder Behauptung im Buendel. Private Schluesseldateien duerfen nicht im
Repository liegen und werden nie ueberschrieben.

Aufruf (Beispiele, Pfade frei waehlbar ausserhalb des Repositorys):
  python -m scripts.legal_bench.owner_sign schluessel-erzeugen --zweck wurzel --key-id owner-root-1 --ausgabe E:\\wurzel.json
  python -m scripts.legal_bench.owner_sign schluesselliste-signieren --wurzel E:\\wurzel.json --entwurf liste.json --ausgabe anker.json
  python -m scripts.legal_bench.owner_sign urteil --buendel D:\\belege --lauf <run_id> --maschine vm.json
  python -m scripts.legal_bench.owner_sign bestaetigung-signieren --schluessel C:\\owner\\freigabe.json --anker anker.json ...
"""

from __future__ import annotations

import argparse
import getpass
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts.legal_bench import answer_specs, isolation_evidence, isolation_gate, owner_keys


class SigningRefused(RuntimeError):
    """Signieren verweigert - der Grund wird genannt, es entsteht keine Datei."""

    def __init__(self, reasons: list[str]) -> None:
        super().__init__("Signieren verweigert: " + "; ".join(reasons[:5]))
        self.reasons = list(reasons)


def _outside_repository(path: Path) -> None:
    resolved = Path(path).resolve()
    if resolved.is_relative_to(answer_specs.REPO.resolve()):
        raise SigningRefused([f"privater Schluessel darf nicht im Repository liegen ({resolved})"])


def _write_new(path: Path, document: dict) -> None:
    path = Path(path)
    if path.exists():
        raise SigningRefused([f"{path} existiert bereits - wird nicht ueberschrieben"])
    with open(path, "x", encoding="utf-8") as handle:  # exklusiv: kein Ueberschreiben, auch nicht im Wettlauf
        json.dump(document, handle, ensure_ascii=False, indent=1)


def create_key(path: Path, *, purpose: str, key_id: str, passphrase: str, n: int = owner_keys.SCRYPT_N) -> dict:
    """Erzeugt einen Ed25519-Schluessel und speichert ihn NUR verschluesselt. Rueckgabe: oeffentliche Angaben."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    _outside_repository(path)
    key = Ed25519PrivateKey.generate()
    document = owner_keys.encrypt_private_key(key, passphrase, purpose=purpose, key_id=key_id, n=n)
    _write_new(path, document)
    return {"key_id": key_id, "zweck": purpose, "public_key_hex": document["public_key_hex"],
            "fingerprint_sha256": owner_keys.fingerprint(key.public_key())}


def load_private_key(path: Path, passphrase: str, *, purpose: str, min_n: int = owner_keys.SCRYPT_N):  # noqa: ANN201
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    return document["key_id"], owner_keys.decrypt_private_key(document, passphrase, purpose=purpose, min_n=min_n)


def sign_keylist(root_key, draft: dict, *, now: datetime) -> dict:  # noqa: ANN001
    """Signiert eine Schluesselliste und prueft sie sofort wie das Gate (nur mit genau diesem Wurzelschluessel)."""
    document = owner_keys.sign_keylist(root_key, draft)
    _, problems = owner_keys.verify_keylist(
        json.dumps(document).encode("utf-8"), pinned_roots=(owner_keys.fingerprint(root_key.public_key()),),
        min_version=owner_keys.MIN_KEYLIST_VERSION, now=now, test_only=bool(draft.get("test_only")))
    if problems:
        raise SigningRefused(problems)
    return document


def judge(bundle: Path, *, policy_path: Path, run_id: str, maschine: dict, tool: dict, reference: datetime,
          allow_dry_run: bool = False) -> dict:
    policy = json.loads(Path(policy_path).read_text(encoding="utf-8"))
    return isolation_evidence.evaluate(bundle, policy=policy, reference=reference, now=reference, allow_dry_run=allow_dry_run, lauf={
        "run_id": run_id, "maschine": maschine, "netzwerkpolitik_sha256": isolation_gate.sha256_file(policy_path),
        "pruefwerkzeug": tool})


def commit_fingerprint_namen(commit: str, run) -> list[str] | None:  # noqa: ANN001
    """§105 (A10): die im COMMIT verfolgten Fingerabdruck-Dateien (git ls-tree), gefiltert wie tool_files(). So faellt
    eine im Arbeitsbaum geloeschte oder hinzugefuegte Fingerabdruck-Datei auf, statt still ausgelassen zu werden."""
    result = run(["git", "ls-tree", "-r", "--name-only", commit], cwd=answer_specs.REPO, capture_output=True,
                 timeout=120, check=False)
    if result.returncode != 0:
        return None
    out = result.stdout
    text = out.decode("utf-8", "surrogateescape") if isinstance(out, bytes) else out
    return sorted(n for n in text.splitlines() if n and isolation_gate.im_fingerprint(n))


def committed_stand(commit: str, runner=None) -> dict:  # noqa: ANN001
    """§96/§98/§105 (M4/H2/A10): Fingerabdruck des Pruefwerkzeugs aus dem Inhalt des COMMITS - `git cat-file --batch`
    (nur lesend, auf dem Host) fuer die im Commit verfolgten Fingerabdruck-Dateien. Weicht die Dateimenge des Commits
    von der des Arbeitsbaums ab (geloescht/hinzugefuegt/untracked) oder ist eine Datei kein normales Blob -> sha256 None
    (Signieren verweigert). Damit bindet die Signatur exakt den festgeschriebenen Stand."""
    import hashlib
    import subprocess

    run = runner or subprocess.run
    worktree = set(isolation_gate.tool_files())
    commit_names = commit_fingerprint_namen(commit, run)
    if commit_names is None:
        return {"commit": commit, "sha256": None, "fehlend": "git ls-tree fehlgeschlagen"}
    geloescht = [n for n in commit_names if n not in worktree]  # im Commit verfolgt, im Arbeitsbaum fehlend
    hinzugefuegt = [n for n in sorted(worktree) if n not in set(commit_names)]  # im Arbeitsbaum, nicht im Commit
    if geloescht or hinzugefuegt:
        details = []
        if geloescht:
            details.append("geloescht/fehlend: " + ", ".join(geloescht[:5]))
        if hinzugefuegt:
            details.append("nicht committet: " + ", ".join(hinzugefuegt[:5]))
        return {"commit": commit, "sha256": None, "fehlend": "; ".join(details)}
    names = commit_names
    request = "".join(f"{commit}:{name}\n" for name in names).encode("utf-8")
    result = run(["git", "cat-file", "--batch"], cwd=answer_specs.REPO, input=request, capture_output=True,
                 timeout=120, check=False)
    if result.returncode != 0:
        return {"commit": commit, "sha256": None, "fehlend": "git cat-file fehlgeschlagen"}
    data, pos, digests = result.stdout, 0, {}
    try:
        for name in names:
            end = data.index(b"\n", pos)
            header = data[pos:end].split()
            pos = end + 1
            if len(header) != 3 or header[1] != b"blob":
                return {"commit": commit, "sha256": None, "fehlend": name}
            size = int(header[2])
            digests[name] = hashlib.sha256(data[pos:pos + size].replace(b"\r\n", b"\n")).hexdigest()
            pos += size + 1
    except (ValueError, IndexError):
        return {"commit": commit, "sha256": None, "fehlend": "Antwort von git unvollstaendig"}
    return {"commit": commit, "sha256": hashlib.sha256(isolation_gate.canonical(digests)).hexdigest()}


def sign_attestation(owner_key, key_id: str, *, keylist_path: Path, bundle: Path, policy_path: Path, run_id: str,  # noqa: ANN001, PLR0913
                     maschine: dict, pruefer_name: str, hours: float, pinned_roots: tuple[str, ...], test_only: bool,
                     now: datetime, signer_identity=isolation_gate.machine_identity,  # noqa: ANN001
                     tool=isolation_gate.pruefwerkzeug_stand, committed=committed_stand) -> dict:  # noqa: ANN001
    """Bestaetigung v2 - NUR bei Urteil PASS fuer alle 13 Tests, gueltigem eigenem Schluessel und Signatur ausserhalb der
    geprueften Maschine. Jeder Verstoss -> SigningRefused, keine Signatur."""
    reasons: list[str] = []
    if signer_identity() == maschine:
        reasons.append("Signieren auf der geprueften Maschine (VM) ist verboten - nur auf dem Host signieren")
    if not 0 < hours <= isolation_gate.MAX_VALIDITY.total_seconds() / 3600:
        reasons.append("Gueltigkeit muss zwischen 0 und 24 Stunden liegen")
    keylist, problems = owner_keys.verify_keylist(Path(keylist_path).read_bytes(), pinned_roots=pinned_roots,
                                                  min_version=owner_keys.MIN_KEYLIST_VERSION, now=now,
                                                  test_only=test_only)
    reasons += problems
    if keylist is not None:
        anchor = keylist.anchors.get(key_id)
        if key_id in keylist.revoked or anchor is None:
            reasons.append(f"Schluessel {key_id!r} ist nicht aktiv (widerrufen oder nicht in der Schluesselliste)")
        elif anchor["key"].public_bytes_raw() != owner_key.public_key().public_bytes_raw():
            reasons.append(f"Schluesseldatei passt nicht zum Eintrag {key_id!r} der Schluesselliste")
        elif anchor["inhaber"] != pruefer_name:
            reasons.append("Pruefer ist nicht der Inhaber des Schluessels")
        elif not anchor["gueltig_von"] <= now <= anchor["gueltig_bis"]:
            reasons.append(f"Schluessel {key_id!r} ist nicht (mehr) gueltig")
    stand = tool()
    if not stand.get("commit") or not stand.get("sha256") or not stand.get("laufzeit_sha256"):
        reasons.append("Pruefwerkzeug auf dem Host nicht bestimmbar (Commit/Dateien/Laufzeitumgebung) - fail-closed")
    else:
        fixed = committed(stand["commit"])
        if fixed.get("sha256") != stand["sha256"]:
            reasons.append("Arbeitsbaum des Hosts weicht vom Commit ab (oder Datei nicht committet: "
                           f"{fixed.get('fehlend', '-')}) - signiert wird nur der festgeschriebene Stand")
    verdict = judge(bundle, policy_path=policy_path, run_id=run_id, maschine=maschine, tool=stand, reference=now,
                    allow_dry_run=test_only)
    reasons += verdict["probleme"]
    if verdict["gesamt"] != isolation_evidence.PASS:
        reasons.append("Urteil nicht PASS: " + ", ".join(f"{t} {r['status']}" for t, r in verdict["tests"].items()
                                                         if r["status"] != isolation_evidence.PASS))
    if stand.get("sha256") and tool() != stand:  # §105 (A8): kein TOCTOU - Pruefwerkzeug waehrend des Signierens stabil
        reasons.append("Pruefwerkzeug hat sich waehrend des Signierens veraendert - abgebrochen")
    if reasons:
        raise SigningRefused(reasons)
    payload = attestation_payload(verdict, policy_path=policy_path, keylist=keylist, run_id=run_id, maschine=maschine,
                                  pruefer_name=pruefer_name, stand=stand, test_only=test_only, now=now, hours=hours)
    return {"key_id": key_id, "payload": payload, "signature_hex": owner_key.sign(isolation_gate.canonical(payload)).hex()}


def attestation_payload(verdict: dict, *, policy_path: Path, keylist: owner_keys.KeyList, run_id: str, maschine: dict,  # noqa: PLR0913
                        pruefer_name: str, stand: dict, test_only: bool, now: datetime, hours: float) -> dict:
    """Inhalt der Bestaetigung v2 aus einem berechneten Urteil (ohne Pruefungen - die macht sign_attestation)."""
    policy = json.loads(Path(policy_path).read_text(encoding="utf-8"))
    return {
        "schema": isolation_gate.SCHEMA, "test_only": test_only, "run_id": run_id, "maschine": maschine,
        "netzwerkpolitik_sha256": isolation_gate.sha256_file(policy_path),
        "firewall_export_sha256": verdict["firewall_export_sha256"],
        "endpunkte": [{"ip": ip, "port": port} for ip, port in sorted(isolation_gate._approved_endpoints(policy))],
        "dns_modus": policy.get("dns", {}).get("entscheidung"),
        "pruefer": {"name": pruefer_name, "rolle": owner_keys.OWNER_ROLE},
        "schluesselliste": {"version": keylist.version, "sha256": keylist.sha256},
        "pruefwerkzeug": stand, "buendel_sha256": verdict["manifest_sha256"],
        "urteil": {"gesamt": verdict["gesamt"], "tests": {t: r["status"] for t, r in verdict["tests"].items()}},
        "gueltig_von": now.isoformat(), "gueltig_bis": (now + timedelta(hours=hours)).isoformat(),
    }


# --- Kommandozeile --------------------------------------------------------------------------------------------------------


def _passphrase(confirm: bool) -> str:
    value = getpass.getpass("Passphrase (wird nicht angezeigt): ")
    if confirm and getpass.getpass("Passphrase wiederholen: ") != value:
        raise SigningRefused(["Passphrasen stimmen nicht ueberein"])
    return value


def _print_verdict(verdict: dict) -> None:
    print(f"Gesamturteil: {verdict['gesamt']}")
    for problem in verdict["probleme"]:
        print(f"  Buendel: {problem}")
    for test, result in verdict["tests"].items():
        print(f"  {test}: {result['status']}" + (f" - {'; '.join(result['gruende'][:2])}" if result["gruende"] else ""))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="owner_sign", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("schluessel-erzeugen")
    p.add_argument("--zweck", choices=owner_keys.PURPOSES, required=True)
    p.add_argument("--key-id", required=True)
    p.add_argument("--ausgabe", type=Path, required=True)
    p = sub.add_parser("schluesselliste-signieren")
    p.add_argument("--wurzel", type=Path, required=True)
    p.add_argument("--entwurf", type=Path, required=True)
    p.add_argument("--ausgabe", type=Path, required=True)
    for name in ("urteil", "bestaetigung-signieren"):
        p = sub.add_parser(name)
        p.add_argument("--buendel", type=Path, required=True)
        p.add_argument("--lauf", required=True)
        p.add_argument("--maschine", type=Path, required=True, help="JSON {hostname, machine_guid} der VM")
        p.add_argument("--politik", type=Path, default=isolation_gate.POLICY_PATH)
        if name == "bestaetigung-signieren":
            p.add_argument("--schluessel", type=Path, required=True)
            p.add_argument("--anker", type=Path, required=True)
            p.add_argument("--pruefer", required=True)
            p.add_argument("--stunden", type=float, default=8.0)
            p.add_argument("--ausgabe", type=Path, required=True)
    args = parser.parse_args(argv)
    now = datetime.now(timezone.utc)
    try:
        if args.cmd == "schluessel-erzeugen":
            info = create_key(args.ausgabe, purpose=args.zweck, key_id=args.key_id, passphrase=_passphrase(True))
            print(json.dumps(info, ensure_ascii=False, indent=1))  # nur oeffentliche Angaben
        elif args.cmd == "schluesselliste-signieren":
            _, root = load_private_key(args.wurzel, _passphrase(False), purpose="wurzel")
            draft = json.loads(args.entwurf.read_text(encoding="utf-8"))
            _write_new(args.ausgabe, sign_keylist(root, draft, now=now))
            print(f"Schluesselliste signiert: {args.ausgabe} (Wurzel {owner_keys.fingerprint(root.public_key())})")
        else:
            maschine = json.loads(args.maschine.read_text(encoding="utf-8"))
            if args.cmd == "urteil":
                _print_verdict(judge(args.buendel, policy_path=args.politik, run_id=args.lauf, maschine=maschine,
                                     tool=isolation_gate.pruefwerkzeug_stand(), reference=now))
                return 0
            key_id, key = load_private_key(args.schluessel, _passphrase(False), purpose="freigabe")
            document = sign_attestation(key, key_id, keylist_path=args.anker, bundle=args.buendel,
                                        policy_path=args.politik, run_id=args.lauf, maschine=maschine,
                                        pruefer_name=args.pruefer, hours=args.stunden,
                                        pinned_roots=owner_keys.PINNED_ROOT_FINGERPRINTS, test_only=False, now=now)
            _write_new(args.ausgabe, document)
            print(f"Bestaetigung signiert: {args.ausgabe} (Buendel {document['payload']['buendel_sha256'][:16]}...)")
    except (SigningRefused, owner_keys.KeyFileError) as exc:
        print(str(exc), file=sys.stderr)
        for reason in getattr(exc, "reasons", [])[5:]:
            print(f"  {reason}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
