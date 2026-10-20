"""§94: Host-Steuerung der Abnahme - Port-ACL kontrolliert setzen, pruefen, entfernen; Golden Image zuruecksetzen.

Dies ist das EINZIGE Werkzeug, das am Host etwas veraendert. Es ist bewusst von `isolation_acceptance.REAL_COMMANDS`
(nur lesend) getrennt. Jede Aenderung braucht ALLE drei Bedingungen:
1. `LEXONO_ISOLATION_ACCEPTANCE=1` in der Umgebung,
2. `--ausfuehren` (ohne: nur Plan - die Befehle werden angezeigt, nichts wird ausgefuehrt),
3. eine erhoehte PowerShell (Hyper-V-Cmdlets scheitern sonst - das Werkzeug fordert keine Rechte an).

Ablauf `acl-setzen`:
- Ist-ACL lesen; sie muss LEER sein (unbekannter Zustand -> Abbruch, nichts wird geaendert).
- Soll setzen: Deny ausgehend 0.0.0.0/0 (Weight 1) und ::/0 (Weight 2), alle Protokolle; Allow nur fuer Endpunkte der
  FREIGEGEBENEN Netzwerkpolitik (TCP, Stateful, Weight ab 10).
- Ist erneut lesen und mit dem Soll vergleichen. Abweichung -> automatischer Rueckbau und Abbruch.
- Aktivierungsprotokoll (Zeit, Ist vorher/nachher, Soll, Hash) exklusiv schreiben - es wird Beleg fuer V1/V10.

`acl-entfernen` entfernt NUR die selbst gesetzten Eintraege (gleiche Weights und Inhalte); fremde Eintraege -> Abbruch.
`image-zuruecksetzen` (V11): VM muss aus sein; Eltern-VHDX wird vor und nach gehasht (nur gelesen), der
Differenzdatentraeger neu angelegt; das Protokoll wird Beleg fuer V11.

Die Semantik der Hyper-V Extended ACL auf Windows 11 (Gewichtung, Parameter von Remove-) ist NICHT VERIFIZIERT - genau
das zeigt erst die reale Gegenprobe (§92)."""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts.legal_bench import isolation_acceptance as acc

ENV_REAL = acc.ENV_REAL
RECORD_SCHEMA = "lexono-isolation-acl-aktivierung/1"
FW_RECORD_SCHEMA = "lexono-isolation-firewall-regeln/1"
#: §96 (M7): Gruppe der EIGENEN, befristeten Host-Firewall-Regeln fuer die Kanarien
FW_GROUP = "Lexono-Abnahme"
FW_FIELDS = ("DisplayName", "Enabled", "Direction", "Action", "Protocol", "LocalPort", "LocalAddress", "RemoteAddress",
             "InterfaceAlias")
FW_MAX_HOURS = 12
RESET_SCHEMA = "lexono-isolation-image-ruecksetzung/1"
ACL_FIELDS = ("Direction", "Action", "RemoteIPAddress", "RemotePort", "Protocol", "Weight", "Stateful")


class ControlRefused(RuntimeError):
    def __init__(self, reasons: list[str]) -> None:
        super().__init__("; ".join(reasons[:5]))
        self.reasons = list(reasons)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _q(value: str) -> str:
    """PowerShell-String in einfachen Anfuehrungszeichen; nur geprufte Zeichen."""
    if not all(c.isalnum() or c in "-_.:/\\ ()=;+" for c in value):
        raise ControlRefused([f"unzulaessige Zeichen in {value!r}"])
    return "'" + value + "'"


def soll_acl(policy: dict) -> list[dict]:
    """Soll-ACL: Deny-all fuer beide Familien, Allow nur fuer freigegebene Endpunkte (TCP)."""
    from scripts.legal_bench import isolation_gate

    soll = [{"Direction": "Outbound", "Action": "Deny", "RemoteIPAddress": "0.0.0.0/0", "RemotePort": "",
             "Protocol": "", "Weight": 1, "Stateful": False},
            {"Direction": "Outbound", "Action": "Deny", "RemoteIPAddress": "::/0", "RemotePort": "",
             "Protocol": "", "Weight": 2, "Stateful": False}]
    if policy.get("freigaben") and isolation_gate._approved_endpoints(policy) and policy.get("status") != "FREIGEGEBEN":
        raise ControlRefused(["Endpunkte nur aus einer FREIGEGEBENEN Netzwerkpolitik"])
    for weight, (ip, port) in enumerate(sorted(isolation_gate._approved_endpoints(policy)), start=10):
        address = ipaddress.ip_address(ip)
        soll.append({"Direction": "Outbound", "Action": "Allow",
                     "RemoteIPAddress": f"{address}/{128 if address.version == 6 else 32}", "RemotePort": str(int(port)),
                     "Protocol": "TCP", "Weight": weight, "Stateful": True})
    return soll


def normalize_acl(entries: list[dict]) -> list[dict]:
    """Ist-ACL deterministisch (Felder, Typen, Reihenfolge nach Weight/Richtung). Unbekannte Werte -> Fehler."""
    result = []
    for entry in entries:
        missing = [f for f in ACL_FIELDS if f not in entry]
        if missing:
            raise ControlRefused([f"ACL-Eintrag ohne {', '.join(missing)}"])
        if not isinstance(entry["Weight"], int) or isinstance(entry["Weight"], bool) \
                or not isinstance(entry["Stateful"], bool):
            raise ControlRefused(["ACL-Eintrag mit unklaren Typen (Weight/Stateful)"])
        if entry["Direction"] not in ("Inbound", "Outbound") or entry["Action"] not in ("Allow", "Deny", "Meter"):
            raise ControlRefused([f"ACL-Eintrag mit unbekannter Richtung/Aktion ({entry['Direction']!r}/{entry['Action']!r})"])
        result.append({f: (entry[f] if f in ("Weight", "Stateful") else str(entry[f] or "").strip()) for f in ACL_FIELDS})
    return sorted(result, key=lambda e: (e["Direction"], e["Weight"], e["RemoteIPAddress"]))


def _add_command(vm: str, entry: dict) -> str:
    parts = [f"Add-VMNetworkAdapterExtendedAcl -VMName {_q(vm)} -Action {entry['Action']} -Direction {entry['Direction']}",
             f"-RemoteIPAddress {_q(entry['RemoteIPAddress'])}", f"-Weight {int(entry['Weight'])}"]
    if entry["RemotePort"]:
        parts.append(f"-RemotePort {_q(entry['RemotePort'])}")
    if entry["Protocol"]:
        parts.append(f"-Protocol {_q(entry['Protocol'])}")
    if entry["Stateful"]:
        parts.append("-Stateful $true")
    return " ".join(parts) + " -ErrorAction Stop"


def _remove_command(vm: str, entry: dict) -> str:
    return (f"Remove-VMNetworkAdapterExtendedAcl -VMName {_q(vm)} -Weight {int(entry['Weight'])} "
            f"-Direction {entry['Direction']} -ErrorAction Stop")


FINAL_STATES = ("abgeschlossen", "zurueckgebaut", "entfernt")


def read_journal(path: Path | None) -> list[dict]:
    """Aenderungsjournal (JSONL). Unlesbare Zeilen werden als eigener Eintrag gemeldet (nie still uebergangen)."""
    if path is None or not Path(path).is_file():
        return []
    entries = []
    for number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            entries.append(json.loads(line))
        except ValueError:
            entries.append({"status": "unlesbar", "zeile": number})
    return entries


def journal_offen(entries: list[dict]) -> list[dict]:
    """§98 (M4-M6): Vorgaenge ohne Endzustand bzw. mit begonnenen Befehlen ohne Ergebnis (Absturz/Stromausfall)."""
    operations: dict[str, dict] = {}
    open_items = []
    for entry in entries:
        if entry.get("status") == "unlesbar":
            open_items.append({"vorgang_id": None, "grund": f"Journalzeile {entry.get('zeile')} unlesbar"})
            continue
        op = operations.setdefault(entry.get("vorgang_id"), {"lauf": entry.get("lauf"), "vorgang": entry.get("vorgang"),
                                                             "status": None, "offen": []})
        status = entry.get("status")
        if status == "begonnen":
            op["offen"].append(entry.get("befehl"))
        elif status in ("erfolgreich", "fehlgeschlagen", "abgebrochen") and entry.get("befehl") in op["offen"]:
            op["offen"].remove(entry.get("befehl"))
            if status == "abgebrochen":
                op["offen"].append(entry.get("befehl"))  # Ergebnis unbekannt
        if status not in ("begonnen", "erfolgreich", "fehlgeschlagen", "abgebrochen"):
            op["status"] = status
    for operation_id, op in operations.items():
        # Endzustand wurde aus dem NACHGELESENEN Ist-Zustand bestimmt -> unklare Einzelbefehle sind damit aufgeloest
        if op["status"] not in FINAL_STATES:
            open_items.append({"vorgang_id": operation_id, "lauf": op["lauf"], "vorgang": op["vorgang"],
                               "status": op["status"], "offene_befehle": op["offen"]})
    return open_items


class HostControl:
    """`runner` ist injizierbar (Tests: simuliertes Hyper-V); real nur subprocess mit allen Freigaben.

    §98 (M4-M6): Jede Aenderung wird VOR und NACH ihrer Ausfuehrung im Aenderungsjournal festgehalten (append-only,
    fsync): Vorgang "geplant" (mit Soll), je Befehl "begonnen" und "erfolgreich"/"fehlgeschlagen"/"abgebrochen", am Ende
    "abgeschlossen", "zurueckgebaut" oder "unvollstaendig". Ein Absturz hinterlaesst "begonnen" ohne Ergebnis - der
    Startcheck meldet das. Ohne Journaldatei wird nur im Speicher protokolliert (Tests); die CLI verlangt eine Datei."""

    def __init__(self, vm: str, *, ausfuehren: bool, runner=subprocess.run, clock=utcnow,  # noqa: ANN001, PLR0913
                 journal: Path | None = None, run_id: str | None = None) -> None:
        acc._safe_vm_name(vm)
        self.vm, self.ausfuehren, self.runner, self.clock = vm, ausfuehren, runner, clock
        self.journal, self.run_id = (Path(journal) if journal else None), run_id
        self.journal_entries: list[dict] = []
        self.plan: list[str] = []
        self._operation: str | None = None

    def _allowed(self) -> bool:
        return self.ausfuehren and os.environ.get(ENV_REAL) == "1"

    def _log(self, status: str, **fields) -> None:  # noqa: ANN003
        entry = {"zeit": self.clock().isoformat(), "lauf": fields.pop("lauf", self.run_id), "vm": self.vm,
                 "vorgang_id": self._operation, "status": status, **fields}
        self.journal_entries.append(entry)
        if self.journal is not None:
            self.journal.parent.mkdir(parents=True, exist_ok=True)
            with open(self.journal, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
                handle.flush()
                os.fsync(handle.fileno())

    def _begin(self, vorgang: str, lauf: str | None, **fields) -> None:  # noqa: ANN003
        import uuid

        self._operation = uuid.uuid4().hex
        self._log("geplant", vorgang=vorgang, lauf=lauf, **fields)

    def _change(self, command: str) -> None:
        self.plan.append(command)
        if not self._allowed():
            raise ControlRefused([f"Aenderung nicht freigegeben ({ENV_REAL}=1 und --ausfuehren noetig): {command}"])
        self._log("begonnen", befehl=command)
        try:
            result = self.runner(["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
                                 capture_output=True, text=True, timeout=300, check=False)
        except BaseException:
            self._log("abgebrochen", befehl=command)  # Ergebnis unbekannt (Abbruch, Timeout, Prozessende)
            raise
        if result.returncode != 0:
            self._log("fehlgeschlagen", befehl=command, exit_code=result.returncode)
            raise ControlRefused([f"Befehl fehlgeschlagen (Exit {result.returncode}): {command}",
                                  (result.stderr or "")[:300]])
        self._log("erfolgreich", befehl=command)

    def read_acl(self) -> list[dict]:
        if os.environ.get(ENV_REAL) != "1":
            raise ControlRefused([f"Lesen der ACL nur mit {ENV_REAL}=1"])
        result = acc.run_real("host.port_acl", real=True, vm=self.vm, runner=self.runner)
        if result.returncode != 0:
            raise ControlRefused([f"ACL nicht lesbar (Exit {result.returncode})"])
        return normalize_acl(acc.normalize_ps_json(result.stdout))

    def vm_state(self) -> str:
        result = acc.run_real("host.vm_zustand", real=True, vm=self.vm, runner=self.runner)
        items = acc.normalize_ps_json(result.stdout)
        if result.returncode != 0 or len(items) != 1 or not isinstance(items[0].get("state"), str):
            raise ControlRefused(["VM-Zustand nicht eindeutig lesbar"])
        return items[0]["state"]

    def _finish(self, removed_from: list[dict], still: list[dict]) -> str:
        """Endzustand eines abgebrochenen/fehlgeschlagenen Vorgangs aus dem TATSAECHLICHEN Ist-Zustand."""
        status = "zurueckgebaut" if not still else "unvollstaendig"
        self._log(status, rest=still)
        return "vollstaendig" if not still else "UNVOLLSTAENDIG - manuell pruefen: " + json.dumps(still)

    # --- ACL ------------------------------------------------------------------------------------------------------------

    def acl_plan(self, policy: dict) -> list[str]:
        return [_add_command(self.vm, e) for e in soll_acl(policy)]

    def acl_setzen(self, policy: dict, record_path: Path) -> dict:
        if not self._allowed():
            raise ControlRefused([f"acl-setzen nicht freigegeben ({ENV_REAL}=1 und --ausfuehren noetig)"])
        record_path = Path(record_path)
        if record_path.exists():
            raise ControlRefused([f"{record_path} existiert bereits - Protokolle werden nie ueberschrieben"])
        soll = normalize_acl(soll_acl(policy))
        before = self.read_acl()
        if before:
            raise ControlRefused(["Ist-ACL nicht leer - unbekannter Zustand, nichts wird veraendert", json.dumps(before)])
        self._begin("acl-setzen", self.run_id, soll=soll)
        attempted = []
        try:
            for entry in soll:
                attempted.append(entry)  # auch der laufende Befehl wird zurueckgebaut (Ergebnis evtl. unbekannt)
                self._change(_add_command(self.vm, entry))
            after = self.read_acl()
            if after != soll:
                raise ControlRefused(["Ist-ACL nach dem Setzen weicht vom Soll ab", json.dumps(after)])
        except BaseException as exc:  # §98 (M4): auch Abbruch (Strg+C, Prozessende) -> Rueckbau versuchen
            rollback = self._rollback(attempted)
            if not isinstance(exc, Exception):
                raise
            reasons = exc.reasons if isinstance(exc, ControlRefused) else [f"{type(exc).__name__}: {exc}"]
            raise ControlRefused([*reasons, f"Rueckbau: {rollback}"]) from None
        record = {"schema": RECORD_SCHEMA, "vm": self.vm, "zeit_gesetzt": self.clock().isoformat(), "acl_vorher": before,
                  "soll": soll, "acl_nachher": after, "lauf": self.run_id,
                  "acl_nachher_sha256": hashlib.sha256(acc._canonical(after)).hexdigest()}
        with open(record_path, "x", encoding="utf-8") as handle:
            json.dump(record, handle, ensure_ascii=False, indent=1)
        self._log("abgeschlossen", protokoll=str(record_path))
        return record

    def _rollback(self, entries: list[dict]) -> str:
        for entry in reversed(entries):
            try:
                self._change(_remove_command(self.vm, entry))
            except (ControlRefused, Exception):  # noqa: BLE001 - Ergebnis steht im Journal; massgeblich ist das Nachlesen
                pass
        try:
            still = [e for e in self.read_acl() if e in entries]
        except Exception as exc:  # noqa: BLE001
            still = [{"unlesbar": f"{type(exc).__name__}"}]
        return self._finish(entries, still)

    def acl_pruefen(self, policy: dict) -> dict:
        actual, soll = self.read_acl(), normalize_acl(soll_acl(policy))
        return {"ist": actual, "soll": soll, "gleich": actual == soll,
                "ist_sha256": hashlib.sha256(acc._canonical(actual)).hexdigest()}

    def acl_entfernen(self, policy: dict, *, lauf: str | None = None) -> dict:
        """Ohne `lauf`: nur Eintraege des aktuellen Solls, fremde -> Abbruch. Mit `lauf` (Rest eines frueheren Laufs):
        nur Eintraege, die laut Journal genau dieser Lauf gesetzt hat; alles andere bleibt unberuehrt."""
        if not self._allowed():
            raise ControlRefused([f"acl-entfernen nicht freigegeben ({ENV_REAL}=1 und --ausfuehren noetig)"])
        actual = self.read_acl()
        if lauf is None:
            soll = normalize_acl(soll_acl(policy))
            own = [e for e in actual if e in soll]
            foreign = [e for e in actual if e not in soll]
            if foreign:
                raise ControlRefused(["fremde ACL-Eintraege vorhanden - nichts wird entfernt", json.dumps(foreign)])
        else:
            recorded = [e for j in read_journal(self.journal) + self.journal_entries
                        if j.get("lauf") == lauf and j.get("vorgang") == "acl-setzen" and j.get("status") == "geplant"
                        for e in j.get("soll") or []]
            if not recorded:
                raise ControlRefused([f"kein Journaleintrag fuer Lauf {lauf!r} - Herkunft nicht belegbar, nichts entfernt"])
            own = [e for e in actual if e in recorded]
            foreign = [e for e in actual if e not in recorded]
        self._begin("acl-entfernen", lauf or self.run_id, eintraege=own)
        for entry in own:
            try:
                self._change(_remove_command(self.vm, entry))
            except ControlRefused:
                pass
        remaining = [e for e in self.read_acl() if e in own]
        self._log("entfernt" if not remaining else "unvollstaendig", rest=remaining)
        if remaining:
            raise ControlRefused(["eigene ACL-Eintraege nach dem Entfernen noch vorhanden", json.dumps(remaining)])
        return {"entfernt": own, "rest": remaining, "unberuehrt": foreign, "zeit": self.clock().isoformat()}

    # --- Host-Firewall (§96, M7) ------------------------------------------------------------------------------------------

    def read_firewall(self) -> list[dict]:
        result = acc.run_real("host.firewall_gruppe", real=True, runner=self.runner)
        if result.returncode != 0:
            raise ControlRefused([f"Firewall-Gruppe nicht lesbar (Exit {result.returncode})"])
        return normalize_firewall(acc.normalize_ps_json(result.stdout))

    def fw_plan(self, konfig: dict) -> list[str]:
        return [_fw_add_command(r, f"run_id={konfig['run_id']};ablauf=PLAN") for r in soll_firewall(konfig)]

    def fw_pruefen(self, konfig: dict) -> dict:
        """Ist == Soll (ohne Beschreibung) und keine Regel abgelaufen bzw. fuer einen anderen Lauf."""
        actual = self.read_firewall()
        target = sorted(soll_firewall(konfig), key=lambda e: e["DisplayName"])
        stripped = [{f: e[f] for f in FW_FIELDS} for e in actual]
        problems = []
        now = self.clock()
        for entry in actual:
            meta = dict(part.split("=", 1) for part in entry["Description"].split(";") if "=" in part)
            if meta.get("run_id") != konfig["run_id"]:
                problems.append(f"Regel {entry['DisplayName']!r} gehoert zu anderem Lauf")
            try:
                if datetime.fromisoformat(meta.get("ablauf", "")) < now:
                    problems.append(f"Regel {entry['DisplayName']!r} abgelaufen")
            except ValueError:
                problems.append(f"Regel {entry['DisplayName']!r} ohne gueltigen Ablauf")
        return {"ist": actual, "soll": target, "gleich": stripped == target and not problems, "probleme": problems,
                "ist_sha256": hashlib.sha256(acc._canonical(actual)).hexdigest()}

    def fw_setzen(self, konfig: dict, record_path: Path, *, hours: float = FW_MAX_HOURS) -> dict:
        if not self._allowed():
            raise ControlRefused([f"fw-setzen nicht freigegeben ({ENV_REAL}=1 und --ausfuehren noetig)"])
        if not 0 < hours <= FW_MAX_HOURS:
            raise ControlRefused([f"Gueltigkeit der Firewall-Regeln 0 bis {FW_MAX_HOURS} h"])
        record_path = Path(record_path)
        if record_path.exists():
            raise ControlRefused([f"{record_path} existiert bereits"])
        before = self.read_firewall()
        if before:
            raise ControlRefused(["Reste in der Firewall-Gruppe - erst pruefen/entfernen", json.dumps(before)])
        expiry = (self.clock() + timedelta(hours=hours)).isoformat()
        description = f"run_id={konfig['run_id']};ablauf={expiry}"
        rules = soll_firewall(konfig)
        self._begin("fw-setzen", konfig["run_id"], regeln=[r["DisplayName"] for r in rules], ablauf=expiry)
        attempted = []
        try:
            for rule in rules:
                attempted.append(rule)
                self._change(_fw_add_command(rule, description))
            check = self.fw_pruefen(konfig)
            if not check["gleich"]:
                raise ControlRefused(["Firewall-Regeln nach dem Setzen weichen vom Soll ab", *check["probleme"]])
        except BaseException as exc:  # §98 (M4): auch Abbruch -> Rueckbau versuchen
            rollback = self._fw_rollback(attempted)
            if not isinstance(exc, Exception):
                raise
            reasons = exc.reasons if isinstance(exc, ControlRefused) else [f"{type(exc).__name__}: {exc}"]
            raise ControlRefused([*reasons, f"Rueckbau: {rollback}"]) from None
        record = {"schema": FW_RECORD_SCHEMA, "run_id": konfig["run_id"], "zeit": self.clock().isoformat(),
                  "ablauf": expiry, "regeln": check["ist"]}
        with open(record_path, "x", encoding="utf-8") as handle:
            json.dump(record, handle, ensure_ascii=False, indent=1)
        self._log("abgeschlossen", protokoll=str(record_path))
        return record

    def _fw_rollback(self, rules: list[dict]) -> str:
        for rule in reversed(rules):
            try:
                self._change(_fw_remove_command(rule))
            except (ControlRefused, Exception):  # noqa: BLE001
                pass
        names = {r["DisplayName"] for r in rules}
        try:
            still = [e for e in self.read_firewall() if e["DisplayName"] in names]
        except Exception as exc:  # noqa: BLE001
            still = [{"unlesbar": f"{type(exc).__name__}"}]
        return self._finish(rules, still)

    def fw_entfernen(self, konfig: dict, *, lauf: str | None = None) -> dict:
        """Entfernt NUR eigene Regeln. Ohne `lauf`: Regeln des aktuellen Laufs; fremde in der Gruppe -> Abbruch ohne
        Aenderung. Mit `lauf` (Rest eines frueheren Laufs): nur Regeln, deren Name UND Beschreibung zu diesem Lauf passen
        UND die laut Journal von diesem Lauf angelegt wurden; alle anderen bleiben unberuehrt."""
        if not self._allowed():
            raise ControlRefused([f"fw-entfernen nicht freigegeben ({ENV_REAL}=1 und --ausfuehren noetig)"])
        actual = self.read_firewall()
        if lauf is None:
            own_names = {r["DisplayName"] for r in soll_firewall(konfig)}
            foreign = [e for e in actual if e["DisplayName"] not in own_names]
            if foreign:
                raise ControlRefused(["fremde Regeln in der Gruppe - nichts wird entfernt", json.dumps(foreign)])
            own = actual
        else:
            created = {name for j in read_journal(self.journal) + self.journal_entries
                       if j.get("lauf") == lauf and j.get("vorgang") == "fw-setzen" and j.get("status") == "geplant"
                       for name in j.get("regeln") or []}
            if not created:
                raise ControlRefused([f"kein Journaleintrag fuer Lauf {lauf!r} - Herkunft nicht belegbar, nichts entfernt"])
            own = [e for e in actual if e["DisplayName"] in created and e["DisplayName"].startswith(f"{FW_GROUP} {lauf} ")
                   and f"run_id={lauf};" in e["Description"] + ";"]
            foreign = [e for e in actual if e not in own]
        self._begin("fw-entfernen", lauf or konfig["run_id"], regeln=[e["DisplayName"] for e in own])
        for entry in own:
            try:
                self._change(_fw_remove_command(entry))
            except ControlRefused:
                pass
        names = {e["DisplayName"] for e in own}
        remaining = [e for e in self.read_firewall() if e["DisplayName"] in names]
        self._log("entfernt" if not remaining else "unvollstaendig", rest=[e["DisplayName"] for e in remaining])
        if remaining:
            raise ControlRefused(["eigene Firewall-Regeln nach dem Entfernen noch vorhanden", json.dumps(remaining)])
        return {"entfernt": own, "rest": remaining, "unberuehrt": foreign if lauf else [], "zeit": self.clock().isoformat()}

    def startcheck(self, konfig: dict) -> dict:
        """§96/§98 (M7, N1): Reste frueherer Laeufe? ACL leer, eigene Firewall-Gruppe leer, VM aus, Kinddatentraeger
        vorhanden, keine alten Protokolle, KEINE offenen Vorgaenge im Aenderungsjournal. Nur lesend."""
        findings = []
        try:
            if self.read_acl():
                findings.append("Port-ACL nicht leer (Rest eines frueheren Laufs) - acl-entfernen [--lauf ...]")
        except ControlRefused as exc:
            findings += exc.reasons
        try:
            rest = self.read_firewall()
            runs = sorted({dict(p.split("=", 1) for p in e["Description"].split(";") if "=" in p).get("run_id", "?")
                           for e in rest})
            if rest:
                findings.append(f"Firewall-Gruppe {FW_GROUP} enthaelt {len(rest)} Regel(n) der Laeufe {runs} - "
                                "fw-entfernen --lauf <run_id>")
        except ControlRefused as exc:
            findings += exc.reasons
        try:
            if self.vm_state() != "Off":
                findings.append("VM laeuft - vor dem Lauf ausschalten und zuruecksetzen")
        except ControlRefused as exc:
            findings += exc.reasons
        for key in ("acl_aktivierung", "image_ruecksetzung"):
            if Path(konfig["protokolle"][key]).exists():
                findings.append(f"Protokoll {key} existiert bereits - neuer Lauf braucht neue Protokolle")
        if not Path(konfig["golden_image"]["eltern_vhdx"]).is_file():
            findings.append("Eltern-VHDX (Golden Image) nicht gefunden")
        if not Path(konfig["golden_image"]["kind_vhdx"]).is_file():
            findings.append("Kinddatentraeger fehlt (abgebrochene Ruecksetzung?) - Golden Image pruefen, neu anlegen")
        for item in journal_offen(read_journal(self.journal) + self.journal_entries):
            findings.append(f"Aenderungsjournal: offener Vorgang {item}")
        return {"ok": not findings, "befunde": findings, "zeit": self.clock().isoformat()}

    # --- Golden Image (V11) -------------------------------------------------------------------------------------------------

    def image_zuruecksetzen(self, parent: Path, child: Path, soll_sha256: str, record_path: Path) -> dict:
        if not self._allowed():
            raise ControlRefused([f"image-zuruecksetzen nicht freigegeben ({ENV_REAL}=1 und --ausfuehren noetig)"])
        record_path = Path(record_path)
        if record_path.exists():
            raise ControlRefused([f"{record_path} existiert bereits"])
        state = self.vm_state()
        if state != "Off":
            raise ControlRefused([f"VM muss ausgeschaltet sein (Zustand {state!r})"])
        before = sha256_file(parent)
        self._begin("image-zuruecksetzen", self.run_id, kind=str(child), eltern=str(parent))
        exit_code = 0
        try:
            self._change(f"Remove-Item -LiteralPath {_q(str(child))} -ErrorAction Stop")
            self._change(f"New-VHD -Path {_q(str(child))} -ParentPath {_q(str(parent))} -Differencing -ErrorAction Stop")
        except ControlRefused:
            exit_code = 1
        record = {"schema": RESET_SCHEMA, "vm": self.vm, "zeit": self.clock().isoformat(),
                  "zuruecksetzen": {"exit_code": exit_code, "zeit": self.clock().isoformat()},
                  "image_sha256_soll": soll_sha256, "image_sha256_vorher": before,
                  "image_sha256_nachher": sha256_file(parent)}
        with open(record_path, "x", encoding="utf-8") as handle:
            json.dump(record, handle, ensure_ascii=False, indent=1)
        self._log("abgeschlossen" if not exit_code else "unvollstaendig", protokoll=str(record_path))
        if exit_code:
            raise ControlRefused(["Ruecksetzung fehlgeschlagen - Protokoll geschrieben (V11 wird FAIL)"])
        return record


# --- §96 (M7): eigene Host-Firewall-Regeln -------------------------------------------------------------------------------


def soll_firewall(konfig: dict) -> list[dict]:
    """Je Kanarienziel eine eingehende Allow-Regel: nur Schnittstelle vEthernet des Abnahme-Switches, nur Host-Adresse
    und Port des Ziels, nur die VM-Adressen derselben Familie. Keine weiteren Ports, keine Programmfreigabe."""
    rules = []
    for target in sorted(konfig["kanarien"], key=lambda t: (t["ip"], int(t["port"]), t["protokoll"])):
        ip = ipaddress.ip_address(target["ip"])
        remote = sorted(str(a) for a in map(ipaddress.ip_address, konfig["vm_ips"]) if a.version == ip.version)
        if not remote:
            raise ControlRefused([f"keine VM-Adresse der Familie IPv{ip.version} fuer {ip}"])
        protocol = "TCP" if target["protokoll"] == "tcp" else "UDP"
        rules.append({"DisplayName": f"{FW_GROUP} {konfig['run_id']} {protocol}-{int(target['port'])} {ip}",
                      "Enabled": "True", "Direction": "Inbound", "Action": "Allow", "Protocol": protocol,
                      "LocalPort": str(int(target["port"])), "LocalAddress": str(ip), "RemoteAddress": ",".join(remote),
                      "InterfaceAlias": f"vEthernet ({konfig['switch']['name']})"})
    return rules


def normalize_firewall(entries: list[dict]) -> list[dict]:
    result = []
    for entry in entries:
        missing = [f for f in (*FW_FIELDS, "Description") if f not in entry]
        if missing:
            raise ControlRefused([f"Firewall-Regel ohne {', '.join(missing)}"])
        result.append({f: str(entry[f] or "").strip() for f in (*FW_FIELDS, "Description")})
    return sorted(result, key=lambda e: e["DisplayName"])


def _fw_add_command(rule: dict, description: str) -> str:
    return (f"New-NetFirewallRule -DisplayName {_q(rule['DisplayName'])} -Group {_q(FW_GROUP)} -Direction Inbound "
            f"-Action Allow -Protocol {rule['Protocol']} -LocalPort {int(rule['LocalPort'])} "
            f"-LocalAddress {_q(rule['LocalAddress'])} -RemoteAddress {','.join(_q(a) for a in rule['RemoteAddress'].split(','))} "
            f"-InterfaceAlias {_q(rule['InterfaceAlias'])} -Profile Any -Description {_q(description)} -ErrorAction Stop")


def _fw_remove_command(rule: dict) -> str:
    return f"Remove-NetFirewallRule -DisplayName {_q(rule['DisplayName'])} -ErrorAction Stop"


def journal_path(konfig: dict) -> Path:
    """Gemeinsames, laufuebergreifendes Aenderungsjournal neben den Protokollen (append-only)."""
    return Path(konfig["protokolle"]["acl_aktivierung"]).parent / "aenderungsjournal.jsonl"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="isolation_host_control", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("acl-plan", "acl-setzen", "acl-pruefen", "acl-entfernen", "image-zuruecksetzen", "fw-plan", "fw-setzen",
                 "fw-pruefen", "fw-entfernen", "startcheck"):
        p = sub.add_parser(name)
        p.add_argument("--konfig", type=Path, required=True)
        p.add_argument("--ausfuehren", action="store_true", help="ohne: nur Plan bzw. Ablehnung")
        if name in ("acl-entfernen", "fw-entfernen"):
            p.add_argument("--lauf", help="Rest eines FRUEHEREN Laufs entfernen (nur laut Journal eigene Eintraege)")
        if name in ("acl-setzen", "image-zuruecksetzen", "fw-setzen"):
            p.add_argument("--protokoll", type=Path, required=True)
    args = parser.parse_args(argv)
    from scripts.legal_bench import isolation_real

    konfig = isolation_real.load_konfig(args.konfig)
    policy = json.loads(Path(konfig["netzwerkpolitik"]).read_text(encoding="utf-8"))
    control = HostControl(konfig["vm_name"], ausfuehren=args.ausfuehren, run_id=konfig["run_id"],
                          journal=journal_path(konfig))
    try:
        if args.cmd == "acl-plan":
            print("\n".join(control.acl_plan(policy)))
        elif args.cmd == "acl-setzen":
            print(json.dumps(control.acl_setzen(policy, args.protokoll), ensure_ascii=False, indent=1))
        elif args.cmd == "acl-pruefen":
            print(json.dumps(control.acl_pruefen(policy), ensure_ascii=False, indent=1))
        elif args.cmd == "acl-entfernen":
            print(json.dumps(control.acl_entfernen(policy, lauf=args.lauf), ensure_ascii=False, indent=1))
        elif args.cmd == "fw-plan":
            print("\n".join(control.fw_plan(konfig)))
        elif args.cmd == "fw-setzen":
            print(json.dumps(control.fw_setzen(konfig, args.protokoll), ensure_ascii=False, indent=1))
        elif args.cmd == "fw-pruefen":
            print(json.dumps(control.fw_pruefen(konfig), ensure_ascii=False, indent=1))
        elif args.cmd == "fw-entfernen":
            print(json.dumps(control.fw_entfernen(konfig, lauf=args.lauf), ensure_ascii=False, indent=1))
        elif args.cmd == "startcheck":
            result = control.startcheck(konfig)
            print(json.dumps(result, ensure_ascii=False, indent=1))
            return 0 if result["ok"] else 3
        else:
            image = konfig["golden_image"]
            print(json.dumps(control.image_zuruecksetzen(Path(image["eltern_vhdx"]), Path(image["kind_vhdx"]),
                                                         image["soll_sha256"], args.protokoll), indent=1))
    except (ControlRefused, acc.AcceptanceRefused) as exc:
        print("ABGELEHNT: " + "; ".join(exc.reasons), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
