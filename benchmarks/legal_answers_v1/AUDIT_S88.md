# §88 Vorbereitung der technischen Sicherheitsabnahme

Stand 19.10.2026. Branch `feature/feedback-diagnose`, HEAD = `origin/feature/feedback-diagnose` = `b7e2daa`; `main` =
`origin/main` = `5735e5b` (geprüft per `git fetch` und `git rev-parse`).

**Grenzen dieser Arbeit:**
- Nur Bestandsaufnahme und Planung. Einzige Änderung im Arbeitsverzeichnis ist diese Datei; sie ist **nicht
  committet**.
- Es gab keine Installation, keine VM, keine Änderung an Windows, Firewall, Netzwerk oder Konten, keine externen
  Verbindungen und keine Provider-Aufrufe. Die Produktivdatenbank wurde nicht geöffnet.
- **Eine reale Sicherheitsabnahme hat nicht stattgefunden.** C-14 bleibt GESPERRT.
- J1–J6, F-1, C-7 und C-11 sind unverändert offen.
- Die Owner-Rolle liegt allein beim Auftraggeber; eine IT-Abteilung wird nicht vorausgesetzt.

## 1 Belegter Ist-Zustand

**Geprüft am 19.10.2026:**

| Prüfung | Ergebnis | Nachweis |
|---|---|---|
| Git-Stand | `b7e2daa` = Remote; `main` = `5735e5b`; Working Tree nur die 4 fremden Logo-/UX-Dateien | `git status`, `git rev-parse` |
| Isolations- und Gate-Tests (§84–§87) | **PASS**: 103 bestanden, 7 übersprungen (Abnahme in der Zielumgebung) | `pytest tests/test_s87_isolation_gate.py tests/test_s86_isolation_design.py tests/test_s85_isolation_boundaries.py tests/test_s84_network_process_fence.py` |
| Gateway- bzw. Relay-Tests (bestehend) | **PASS**: 176 bestanden | `pytest tests/test_gateway*.py tests/test_relay_*.py tests/test_privacy_gateway*.py tests/test_no_ai_gateway_proxy.py tests/test_e2e_gateway_pilot_scenario.py` |
| Volle Suite | zuletzt in §87: 5626 bestanden, 4 FAIL nur in `tests/test_design_refresh.py` (bekannte, fremde Logo-Tests) | AUDIT_S87.md; hier **nicht erneut ausgeführt** |

**Stand nach Bereich:**

| Bereich | implementiert | getestet (Software) | real abgenommen | offen |
|---|---|---|---|---|
| Datenschutz-Gateway (`app/privacy/gateway.py`, Siegel `gateway_schema.py`) | ja | ja (Leitungstests §78/§80, Fail-Closed §79) | – | – |
| Relay-Gateway (`gateway/`, `app/ai_providers/gateway_relay_client.py`, `gateway_writing_provider.py`; Schlüssel nur serverseitig, Mandanten-Authentifizierung, Rate-Limit) | ja | ja (176) | **nein** für den Benchmark: der Treiber sperrt `LEXONO_GATEWAY_URL` bewusst | Entscheidung, ob der Relay der freigegebene Endpunkt sein soll |
| Benchmark-Wächter, Treiber, Ablage, Kostenbuch (§79–§81) | ja | ja | – | C-7, C-11 |
| Endpunktsperre und Netz-/Prozesszaun (§83–§85) | ja | ja; Grenzen gegen nativen Code nachgewiesen | – | keine Sicherheitsgrenze |
| Netzwerkpolitik (`isolation/netzwerkpolitik.json`) | Entwurf | Struktur geprüft | – | Status **ENTWURF**; Endpunkt, DNS und Verwaltung `null` |
| Start-Gate I12 (`scripts/legal_bench/isolation_gate.py`) | ja, im Treiber vor Start und vor jedem Aufruf | ja (37) | – | **kein Vertrauensanker**, keine Bestätigung; heute geschlossen |
| VM-Abnahme V1–V13 (`isolation/vm_abnahme.json`) | Matrix | Konsistenz geprüft | **nein** | alle **NICHT DURCHGEFÜHRT** |
| Schlüsselverwaltung | – | – | – | **nicht vorhanden**: kein Schlüssel, kein Verfahren für Aufbewahrung, Rotation oder Widerruf |

## 2 Eignung des Lenovo (nur gelesen)

| Eigenschaft | Befund | Bewertung |
|---|---|---|
| Gerät | LENOVO 20W1S3GJ2H, ThinkPad T14 Gen 2i | belegt |
| CPU | Intel Core i5-1145G7, 4 Kerne / 8 Threads | ausreichend für eine VM |
| RAM | 15,7 GB, davon 7,4 GB frei zum Prüfzeitpunkt | für eine VM mit 4–6 GB ausreichend, **nicht gleichzeitig** mit Kanzlei-Instanz und Ollama-Vorwärmung (bekannter Speicherdruck) |
| Plattenplatz C: | **59,9 GB frei** von 237,5 GB | **knapp**: Windows-11-VM, Python-Umgebung, spaCy-Modell, Tesseract, Golden Image und Snapshot brauchen grob 40–60 GB (Schätzung) |
| Windows | 11 Pro, Build 26200 (Edition `Professional`) | Hyper-V und Windows Sandbox sind in Pro enthalten |
| Rechte | Konto ist **Mitglied der lokalen Administratorengruppe**, der Prozess ist **nicht erhöht** (UAC) | **Korrektur zu §85/§86** („keine Administratorrechte“): eine Erhöhung per UAC ist möglich; jede Aktion damit ist eine Systemänderung, die deine Freigabe braucht |
| Hypervisor | läuft (`HypervisorPresent=True`, VBS aktiv) | Hardware-Virtualisierung ist aktiv; das ist eine Voraussetzung für Hyper-V |
| Optionale Features | `Microsoft-Hyper-V-All`, `Microsoft-Hyper-V-Management-PowerShell`, `Containers-DisposableClientVM` (Sandbox), `VirtualMachinePlatform`, `Microsoft-Windows-Subsystem-Linux`: **vorhanden, deaktiviert** (Status 2) | aktivierbar, **nicht** aktiviert; braucht erhöhte Rechte und einen Neustart |
| Drittanbieter-Hypervisoren | VirtualBox und VMware nicht vorhanden | – |
| Firewall | alle Profile aktiv, ausgehend „NotConfigured“ (zulassen) | der Host selbst ist nicht abgeschottet; das ist für die VM-Lösung nicht nötig, die Regeln sitzen am VM-Port |
| BitLocker | ohne Administratorrechte nicht lesbar | **NICHT VERIFIZIERT** |

**Urteil:** Eine kontrollierte VM ist auf dem Lenovo **technisch möglich**. Dafür fehlt Folgendes, und jeder Punkt ist
eine Systemänderung, die deine ausdrückliche Freigabe braucht:
1. Hyper-V aktivieren (erhöht, Neustart).
2. Ausreichend Plattenplatz schaffen oder ein externes Laufwerk verwenden.
3. Ein Windows-Installationsmedium bzw. eine Lizenz für die Gast-VM bereitstellen (offene Owner-Entscheidung, mögliche
   Kosten).
4. Die VM erstellen.

**Durchsetzung außerhalb der VM auf diesem Gerät** (Annahme, in V1/V10 zu belegen):
- **Testphase:** Ein privater Hyper-V-Switch ohne Verbindung nach außen sperrt alles.
- **Echter Lauf:** Ein interner Switch mit NAT auf dem Host. Dazu **erweiterte Port-ACLs am VM-Netzadapter**
  (`Add-VMNetworkAdapterExtendedAcl`): Standard Deny, Allow nur Endpunkt-IP:443. Sie werden vom Host gesetzt, und die VM
  kann sie nicht ändern.

**Besonderes Risiko:** Der Host ist zugleich der Produktivrechner mit der echten Datenbank. Gemeinsame Ordner, die
Zwischenablage der erweiterten Sitzung, Gastdienste zum Dateiaustausch und Netzfreigaben zum Host müssen ausgeschaltet
sein (V9).

**Zusätzliche Hardware:** **nicht technisch zwingend.** Der einzige konkrete technische Engpass ist der Plattenplatz;
ein externes SSD-Laufwerk würde ihn beheben. Ein separates Gerät wäre eine **optionale** Risikoentscheidung, um
Produktivrechner und Benchmark-Host zu trennen.

## 3 Testplan V1–V13

Kategorien: **S** = Softwaretest (lokal möglich), **VM** = Test in der Gast-VM, **H** = Prüfung der Host- bzw.
Netzwerkgrenze von außen. Status heute: alle realen Tests **NICHT DURCHGEFÜHRT**, blockiert durch die fehlende VM.

| # | Kat. | Voraussetzungen | Durchführung | Soll | Nachweis | Status |
|---|---|---|---|---|---|---|
| V1 | H | Hyper-V, VM, Switch, Port-ACLs, freigegebene Politik | auf dem Host: `Get-VMNetworkAdapterExtendedAcl -VMName <vm>` und Switch-Typ als JSON exportieren; mit `check_rules` (`tests/test_s86_isolation_design.py`) prüfen | Standard Deny für IPv4 und IPv6; nur Endpunkt-IP:Port erlaubt | Export, SHA-256 des Exports, Prüfprotokoll | NICHT DURCHGEFÜHRT |
| V2 | VM | VM, Benchmark-Konto ohne Administratorrechte | in der VM `Get-NetFirewallProfile`; mit dem Benchmark-Konto `Set-NetFirewallProfile` versuchen | alle Profile aktiv, ausgehend Block; der Änderungsversuch scheitert | Ausgaben und Fehlermeldung | NICHT DURCHGEFÜHRT |
| V3 | VM | Testdienst am freigegebenen Ziel (Owner betreibt ihn, z. B. Relay-Testinstanz) | `test_acceptance_approved_test_endpoint_is_reachable` mit Manifest | erreichbar | pytest-Protokoll und Dienst-Log | NICHT DURCHGEFÜHRT |
| V4 | VM + H | Kanarienziel IPv4 (Owner-kontrollierte Senke mit Log) | `test_acceptance_canary_addresses_are_blocked` (tcp, curl) | blockiert; Senke ohne Treffer | Protokoll und Senken-Log | NICHT DURCHGEFÜHRT |
| V5 | VM + H | Kanarienziel IPv6 | dito mit IPv6-Ziel | blockiert | dito | NICHT DURCHGEFÜHRT |
| V6 | VM + H | Kanarienziel UDP | dito (udp) | blockiert | dito | NICHT DURCHGEFÜHRT |
| V7 | VM + H | Resolver-Adressen und Kanarienname | `test_acceptance_dns_is_blocked` | keine Auflösung; Resolver bzw. Senke ohne Abfrage | Protokoll und Log | NICHT DURCHGEFÜHRT |
| V8 | VM + H | wie V4 | dito (native, curl in getrennten Prozessen) | blockiert | dito | NICHT DURCHGEFÜHRT |
| V9 | H + VM | VM-Konfiguration | auf dem Host `Get-VMIntegrationService`, `Get-VM` (Enhanced Session), Netzadapter und Freigaben exportieren; in der VM `Get-NetAdapter` | nur ein Adapter; Gastdienst-Dateikopie, Zwischenablage, Host-Ordner und Freigaben aus | Exporte | NICHT DURCHGEFÜHRT |
| V10 | H | V1 | aus der VM versuchen, Host-ACLs bzw. Switch zu ändern bzw. zu umgehen; Export erneut vergleichen | unverändert, Versuch scheitert | Export vorher/nachher und Protokoll | NICHT DURCHGEFÜHRT |
| V11 | H | Golden Image und Prüfpunkt | VM auf das Image zurücksetzen, Hash vergleichen, V1–V10 kurz wiederholen | identisch; Kurzprüfung bestanden | Hashes und Protokolle | NICHT DURCHGEFÜHRT |
| V12 | H | Senken- und Resolver-Logs | Auswertung durch den Owner, getrennt vom Benchmark-Lauf | keine Treffer aus der VM | unterschriebene Auswertung | NICHT DURCHGEFÜHRT |
| V13 | S + VM | Gate-Tests; in der VM Anker (schreibgeschützt) und Bestätigung | lokal: `tests/test_s87_isolation_gate.py` (**PASS**, 37); in der VM zusätzlich das Produktiv-Gate gegen eine absichtlich veraltete bzw. fremde Bestätigung | lokal bestanden; in der VM ohne gültige Bestätigung kein Start | pytest-Protokolle und Diagnose | Software **PASS**, VM-Teil **NICHT DURCHGEFÜHRT** |

Kein V-Test gilt als bestanden, solange nicht alle seine Nachweise vorliegen.

## 4 Bewertung des I12-Freigabemodells bei alleiniger Owner-Freigabe

**Heutiges Modell:** Die Bestätigung trägt die Signatur einer **externen Prüfstelle**. Der Prüfer muss Inhaber des
Ankerschlüssels sein, der Anker muss für das Benchmark-Konto schreibgeschützt sein, und Test-Bestätigungen werden im
Betrieb abgelehnt. Die Texte sprechen von „IT“; technisch verlangt das Gate aber nur einen Schlüsselinhaber mit Rolle.

**Owner-Modell:** Du bist zugleich Betreiber und Freigebender. Signatur, Bindung an Umgebung, Lauf und Politik,
Aktualität und Manipulationsschutz der Bestätigung behalten dabei ihre volle Wirkung. **Wegfällt** die
**Unabhängigkeit**: Ein Vier-Augen-Prinzip ist mit einer Person nicht möglich. I12 belegt dann eine **bewusste,
nachvollziehbare Owner-Freigabe auf Basis vorliegender Nachweise**, keine unabhängige Prüfung. I12 bleibt aktiv und
wird nicht umgangen.

| Aspekt | Heute | Owner-Modell: nötige Änderung (nicht umgesetzt) |
|---|---|---|
| Rolle | Prüfstelle (Texte: IT) | Rolle „Owner“ in Anker und Bestätigung; Texte anpassen |
| Signatur | Ed25519 roh über kanonisches JSON | bleibt; Signieren **nur auf dem Host**, nie in der VM |
| Schlüsselaufbewahrung | nicht geregelt | **Variante A:** Software-Schlüssel, passwortgeschützt, auf dem Host bzw. einem verschlüsselten USB-Datenträger, nie in der VM (einfach; schwächer). **Variante B:** Hardware-Token; Ed25519 als OpenPGP- bzw. SSH-Signatur, dafür müsste die Prüfung im Gate das Format verstehen (Code-Änderung). Windows-TPM bzw. CNG kennt kein Ed25519; das ginge nur mit ECDSA P-256 und ebenfalls einer Code-Änderung |
| Vertrauensanker | Datei schreibgeschützt für das Benchmark-Konto | bleibt; der Anker wird vom Host ins Golden Image gelegt, und das Benchmark-Konto ist kein Administrator. **Zusätzlich:** den Fingerabdruck des Ankers außerhalb festhalten, etwa im Passwortmanager oder auf Papier, und vor jedem Lauf vergleichen |
| Widerruf und Rotation | nur über das Ablaufdatum des Ankers bzw. Entfernen des Schlüssels | Widerrufsliste neben dem Anker (ebenfalls schreibgeschützt) und dokumentiertes Rotationsverfahren (Code-Änderung) |
| Beleg der Prüfergebnisse | nur „bestanden“ je V-Test | SHA-256 der Protokolle bzw. Exporte je V-Test in der Bestätigung, damit die Signatur auch die Belege abdeckt (Code-Änderung) |
| Ausgleich für fehlende vier Augen | – | Signieren erst nach zeitlich getrennter Durchsicht der Protokolle; Belege unveränderbar archivieren (Commit mit Hash bzw. externe Sicherung); diese Grenze ausdrücklich dokumentieren |
| Selbstbestätigung | durch Test-/Betrieb-Trennung erschwert | bleibt; ein Produktiv-Gate akzeptiert nur Anker aus der schreibgeschützten Datei |

**Bewertung:** Das Owner-Modell ist technisch tragfähig, **wenn** drei Bedingungen gelten:
1. Der Schlüssel liegt außerhalb der VM.
2. Anker und Widerrufsliste sind vom Benchmark-Konto nicht änderbar.
3. Die Belege sind durch die Signatur gebunden.

Es ist **kein** Ersatz für eine unabhängige Prüfung. Das ist ein bewusst akzeptiertes Restrisiko, das du entscheidest
(F-1).

## 5 Priorisierter Umsetzungsplan

**N** = notwendig, **O** = optional, **Org** = organisatorisch. Jeder Schritt mit Systemänderung braucht deine
ausdrückliche Freigabe vorab.

| # | Art | Schritt | Abhängigkeit | Abnahmekriterium | Risiko |
|---|---|---|---|---|---|
| 1 | Org | **F-1 entscheiden:** Owner-Freigabemodell und Variante der Schlüsselaufbewahrung (A oder B) | – | dokumentierte Entscheidung | Unabhängigkeit fehlt (bewusst) |
| 2 | N | **I12 an das Owner-Modell anpassen:** Rolle, Belege als Hashes, Widerrufsliste, ggf. Signaturformat für B; mit Tests; I12 bleibt verpflichtend | 1 | neue Negativtests (Widerruf, fehlender Beleg-Hash, falsche Rolle); bestehende 37 grün | Fehler in der Gate-Logik (durch Tests abgedeckt) |
| 3 | Org | **Endpunkt entscheiden:** eigener Lexono-Relay (eine feste, eigene Adresse; Schlüssel bleibt serverseitig) oder Anthropic direkt (wechselnde IPs); dazu DNS-Modus. Beim Relay müsste die Treibersperre für `LEXONO_GATEWAY_URL` kontrolliert geöffnet werden (Code-Änderung, C-14) | – | Netzwerkpolitik auf `FREIGEGEBEN` gesetzt (durch dich) | IP-Wechsel bzw. Betrieb des Relays |
| 4 | N | **Plattenplatz schaffen** (≥ 60 GB frei) oder externes SSD | – | Messung | Datenverlust bei Aufräumen; nur eigene Dateien |
| 5 | Org | **Sicherung** der Produktivdatenbank und der Installation vor Systemänderungen; Kanzlei-Instanz geschlossen | – | Sicherung mit Hash geprüft | Neustart bei laufender Instanz |
| 6 | N | **Hyper-V aktivieren** (erhöht, Neustart) | 4, 5 | Feature aktiv, Kanzlei-Instanz läuft danach unverändert, DB-Hash gleich | Stabilität des Produktivrechners; Rückweg: Feature deaktivieren |
| 7 | N | **Golden Image** bauen: Windows-11-VM (Medium/Lizenz), Python, Repo-Stand, Tesseract, Benchmark-Konto ohne Admin, Gast-Firewall Block, Gastdienste aus. Danach Netzwerk trennen und Image festschreiben | 6 | Image-Hash, Konfigurationsexport | Lizenzkosten; Netz nur während des Baus |
| 8 | N | **Netzwerk:** zuerst privater Switch (alles gesperrt); für den Lauf interner Switch und Port-ACLs „Deny + Endpunkt“ | 7, 3 | Export der ACLs entspricht der Politik (`check_rules`) | Fehlkonfiguration (durch V1/V10 geprüft) |
| 9 | N | **Kanarienziel** außerhalb, mit Log (eigener kleiner Dienst bzw. separater Port des eigenen Servers; nicht der freigegebene Endpunkt) | 3 | Senke protokolliert Testtreffer von außerhalb der VM | Kosten des Dienstes; Senke darf nicht als Ausgang missbraucht werden |
| 10 | N | **Schlüssel erzeugen**, Anker und Widerrufsliste schreibgeschützt ins Image; Fingerabdruck notieren | 1, 2, 7 | Gate in der VM erkennt den Anker; ein schreibbarer Anker wird abgelehnt | Schlüsselverlust bzw. -diebstahl |
| 11 | N | **V1–V13 durchführen**, Belege sammeln, zeitlich getrennt durchsehen, Bestätigung auf dem Host signieren | 8, 9, 10 | alle Belege vorhanden; Gate in der VM akzeptiert nur diese Bestätigung | Fehlbewertung ohne vier Augen |
| 12 | Org | J1–J6, C-7, C-11 entscheiden (`PILOT_CHECKLISTE.md`) | – | Checkliste | – |
| 13 | N | **C-14:** gesondert reviewte Code-Änderung zum Öffnen der Endpunktsperre nur für den freigegebenen Plan | 11, 12 | Review, Leitungsprüfung, volle Suite | echter Lauf und Kosten |
| – | O | separates Gerät als Benchmark-Host | – | – | Kosten; nur bei gewünschter Trennung vom Produktivrechner |
| – | O | Hardware-Token (Variante B) | 1 | – | Code-Änderung im Gate |
| – | O | AppContainer bzw. Job Object für Tesseract innerhalb der VM | – | – | Aufwand; für die Grenze nicht nötig |

## 6 Risiken und offene Entscheidungen

**Risiken:**
- **Produktivrechner als Host:** Ein Fehler bei Hyper-V, beim Neustart oder bei der Plattenbelegung trifft die
  Kanzlei-Instanz. Gegenmittel sind Sicherung (Schritt 5), ein Zeitfenster und ein Rückweg.
- **Speicher:** 16 GB werden gemeinsam genutzt; den Benchmark nicht parallel zur Kanzlei-Instanz bzw. Ollama-Vorwärmung
  betreiben.
- **Plattenplatz:** knapp (59,9 GB frei).
- **Alleinige Freigabe:** keine unabhängige Prüfung; die Ausgleichsmaßnahmen stehen in Abschnitt 4.
- **Durchsetzung über Port-ACLs:** Dass sie auch IPv6, DNS und andere Protokolle zuverlässig sperren, ist **NICHT
  VERIFIZIERT**; das belegen V1, V4–V8 und V10.
- **BitLocker** des Hosts: **NICHT VERIFIZIERT**.

**Offene Entscheidungen (alle bei dir):**
1. F-1: Owner-Freigabemodell; Schlüsselaufbewahrung A oder B.
2. Endpunkt: Relay oder direkt; DNS-Modus; Freigabe der Netzwerkpolitik.
3. Freigabe der Systemänderungen auf dem Lenovo: Plattenplatz, Hyper-V, VM, Lizenz.
4. Kanarienziel: Ort und Kosten.
5. J1–J6, C-7, C-11.
6. Optional ein separates Gerät.

## Ergebnisübersicht

| Punkt | Ergebnis |
|---|---|
| Git-Stand verifiziert | **PASS** |
| Software-Tests Isolation/Gate/Gateway | **PASS** (103 + 7 übersprungen; 176) |
| Lenovo VM-fähig | **PASS** (technisch), Umsetzung **BLOCKIERT** bis zu deiner Freigabe der Systemänderungen; Plattenplatz knapp |
| V1–V12 | **NICHT DURCHGEFÜHRT** (VM fehlt) |
| V13 | Software **PASS**, VM-Teil **NICHT DURCHGEFÜHRT** |
| Owner-Freigabemodell | bewertet; Umsetzung **NICHT DURCHGEFÜHRT** (Code-Änderung nötig, Schritt 2) |
| reale Sicherheitsabnahme | **NICHT DURCHGEFÜHRT** |
