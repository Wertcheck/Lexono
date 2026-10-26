# Sicherheitsstatus Isolations-/Signierkette und Cloud-E2E (26.10.2026)

Bezug: `benchmarks/legal_answers_v1/SECURITY_EXIT_PLAN.md`. Am 26.10.2026 nur lesend geprüft. **Keine
Sicherheits-, Datenschutz-, P0-08-, C-14- oder Pilotfreigabe. D1, Signierung und C-14 bleiben GESPERRT.**

## Reale Abnahme V1–V13

**Nicht durchgeführt, nicht verifiziert.**

- Auf diesem Rechner gibt es keine VM.
- Die Hyper-V-Cmdlets (`Get-VM`) sind nicht verfügbar.
- Die D1-Vorbereitung ist freigabepflichtig (§4) und wurde **nicht begonnen**. Eine Owner-Freigabe liegt nicht vor.

## Voraussetzungen §2.1 (binär)

| Nr. | Voraussetzung | Ist-Stand | Status |
|---|---|---|---|
| 1 | Commit + Signaturbasis, D4-Wurzelschlüssel, `PINNED_ROOT_FINGERPRINTS` gesetzt | Stand ist committet (`3e6aa98`). `scripts/legal_bench/owner_keys.py:32` enthält `PINNED_ROOT_FINGERPRINTS = ()`. Kein D4-Wurzelschlüssel. | **nicht erfüllt** |
| 2 | Repository/venv/Standardbibliothek gehören einem getrennten Eigentümerkonto | Eigentümer von Repository und `.venv` ist das Arbeitskonto `DESKTOP-2HEQK46\Bonit` | **nicht erfüllt** |
| 3 | Bytecode-/Plugin-Isolation in der VM | keine VM vorhanden | **nicht prüfbar** |
| 4 | Python systemweit installiert | `C:\Users\Bonit\AppData\Local\Programs\Python\Python313` (Benutzerprofil) | **nicht erfüllt** |
| 5 | Gate schließt ohne gültige Kette | lokal verifiziert durch die Isolationssuiten (zuletzt 540 bestanden / 9 übersprungen) und den fail-closed-Befund aus `test-isolation-s80-20261026/` | erfüllt (nur lokal) |
| 6 | Startcheck grün unmittelbar vor dem Lauf | setzt die VM voraus | **nicht prüfbar** |

Die §2.2-Punkte (Timing, Hyper-V-ACL/Firewall, Owner-Rights-ACE, Hyper-V-Kanäle) sind ohne reale VM nicht bewertbar.
Die §2.3-Restrisiken sind vom Owner bisher nicht schriftlich akzeptiert.

## Risiken bei Fortsetzung ohne diese Voraussetzungen

- Das Benchmark-Konto könnte venv oder Repository verändern (Nr. 2 und 4). Gate-Belege wären damit nicht belastbar.
- Ohne gepinnten Wurzelschlüssel (Nr. 1) gibt es keine prüfbare Owner-Signatur.

## Cloud-E2E

**Kein echter Cloud-E2E-Test durchgeführt.**

- Es existiert keine getrennte, freigegebene Testkonfiguration (eigener Schlüssel, eigenes Konto, synthetische Daten).
- Die produktiven Zugangsdaten wurden weder verwendet noch ausgegeben.

Der lokale Aufzeichnungsserver (Loopback, `LEXONO_QA_LOOPBACK_CLOUD_ENDPOINT=1`, ungültiger Testschlüssel) ist nur ein
**begrenzter Testnachweis**. Er zeigt, was die App nach der Pseudonymisierung an einen Endpunkt senden würde. Nicht
gezeigt sind:

- das Verhalten des echten Anbieters;
- TLS, Zertifikate und Proxy;
- Latenzen;
- Fehlercodes;
- die Antwortqualität.

**Voraussetzung für einen echten Lauf:** eine Owner-Entscheidung über ein getrenntes Testkonto und einen getrennten
Testschlüssel, ausschließlich synthetische Daten und eine Begrenzung von Kosten und Rate.
