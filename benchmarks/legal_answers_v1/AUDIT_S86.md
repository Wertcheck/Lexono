# §86 Isolierte Benchmark-Umgebung – Konzept einer extern durchgesetzten Netzwerkisolation

Stand 19.10.2026, Ausgangsstand `2d499b4` (§85, verifiziert: HEAD = Remote, Working Tree nur mit den 4 fremden
Logo-/UX-Dateien), Branch `feature/feedback-diagnose`; `main` unverändert (`5735e5b`).

**Konzept, keine Umsetzung.**
- Es wurde nichts installiert, keine VM erstellt und keine Firewall, kein Konto, keine Richtlinie und keine
  Systemkonfiguration geändert.
- Es gab keine Modell- oder Cloud-Aufrufe, keine externen DNS-Abfragen oder Verbindungen und keine Kosten.
- **Eine Isolation ist NICHT eingerichtet und NICHT abgenommen. C-14 bleibt GESPERRT.**
- J1–J6, F-1, C-7 und C-11 sind unverändert offen.
- Es war eine Architekturprüfung ohne Screenshots. Nachweise: `audit/s86_windows_umgebung.txt` und
  `audit/s86_abnahme_selbsttest.txt`.

**Ausgangslage (§84/§85):** Der Netz- und Prozesszaun im Python-Prozess ist eine nützliche Schutzschicht, aber **keine
Sicherheitsgrenze**. Ein direkter Overlapped-`ConnectEx` und vorab beschaffte native Funktionszeiger umgehen ihn
nachweislich (`tests/test_s85_isolation_boundaries.py`, B14/B15). Eine belastbare Grenze muss deshalb **außerhalb** des
Benchmark-Prozesses durchgesetzt werden.

## 1 Umgebung (nur gelesen)

`audit/s86_windows_umgebung.txt`:

| Eigenschaft | Befund | Einordnung |
|---|---|---|
| System | Windows 11 Pro, Build 26200 | belegt |
| Rechte | **keine Administratorrechte** (mittlere Integritätsstufe) | belegt |
| Hypervisor | aktiv (virtualisierungsbasierte Sicherheit läuft) | belegt; Hardware-Virtualisierung damit vorhanden |
| Hyper-V-Rolle, Windows Sandbox | **nicht installiert** (`vmms.exe` und `WindowsSandbox.exe` fehlen); in Windows 11 Pro grundsätzlich als Feature verfügbar | belegt bzw. Annahme (Installation nicht geprüft) |
| WSL | **nicht installiert** | belegt |
| IPv6 | auf allen Adaptern aktiv | belegt |
| Proxy | keiner (WinHTTP direkt, kein Benutzer-Proxy, keine PAC-Datei) | belegt |
| Firewall | in allen Profilen ein; ausgehend „NotConfigured“ (= zulassen); 245 ausgehende Regeln, **0** aktive Block-Regeln | belegt; erfüllt die Politik **nicht** (Test `test_current_machine_does_not_meet_the_policy`) |
| AppContainer- und Job-Object-API | vorhanden (nur nachgeschlagen, nicht aufgerufen) | belegt |

**Folgerung:** Diese Maschine ist der Arbeitsplatz mit der produktiven Kanzlei-Instanz und der echten Datenbank. Als
Benchmark-Umgebung ist sie **ungeeignet**:
- keine Administratorrechte;
- ausgehender Verkehr frei;
- dieselbe Maschine wie die Produktivdaten.

## 2 Optionsvergleich

Legende: **B** = belegt (hier geprüft bzw. Plattformfunktion dokumentiert), **A** = Annahme (vor Ort nicht geprüft).

| Kriterium | Dedizierter Rechner, restriktive Firewall | **Dedizierte VM, Netzsperre auf Host/Perimeter** | Windows Sandbox | AppContainer (nur Tesseract bzw. Teilprozesse) |
|---|---|---|---|---|
| Verfügbar hier | nein (Hardware nötig) | nein (hier keine Hyper-V-Rolle, kein Admin) | nein (nicht installiert) | API ja (B); Profil müsste angelegt werden |
| Durchsetzung außerhalb des Benchmark-Prozesses | ja, Betriebssystem bzw. Perimeter (A) | **ja, Host bzw. virtueller Switch; aus der VM nicht änderbar (A)** | ja, Host (A); Netzwerk nur an/aus, keine Zielliste | ja, Kernel (A) – aber nur für den einzelnen Prozess |
| Schutz vor nativen Aufrufen, Unterprozessen, rohen Sockets | ja, wenn auf Perimeter durchgesetzt (A) | **ja für alles in der VM (A)** | ja (A) | nur der Container-Prozess, **nicht** der Python-Lauf selbst |
| DNS, IPv4/IPv6, UDP | regelbar (A) | **regelbar am Host bzw. Perimeter (A)** | nur alles oder nichts (A) | ohne Netzwerk-Capability: alles gesperrt (A) |
| Freigabe genau eines Endpunkts | ja (A) | **ja (A)** | **nein**: kein Endpunkt erreichbar → für einen echten Lauf ungeeignet | nein für den Lauf selbst |
| Externe Administration und Prüfung | IT (A) | **IT bzw. Host-Admin; Benchmark-Prozess ohne Rechte (A)** | Benutzer startet sie selbst | Benutzerkonto |
| Wiederherstellung | Neuaufsetzen | **Snapshot bzw. Golden Image, schnell (A)** | jedes Mal frisch (B: Plattform) | – |
| Wartbarkeit | mittel (Hardware, Updates) | **gut (Image versioniert) (A)** | einfach, aber begrenzt | aufwendig (ACLs, ctypes) |
| Testbarkeit der Sperre von außen | ja (A) | **ja: Host bzw. IT liest Regeln, Kanarienziele (A)** | eingeschränkt | eingeschränkt |
| Bewertung | geeignet, teurer und weniger flexibel | **empfohlen** | als Abnahme-Sandbox für „alles gesperrt“ denkbar, nicht für den Lauf | allein **nicht ausreichend**; höchstens Zusatz für Tesseract |

## 3 Zielarchitektur (Empfehlung)

```
 Kanzlei-Arbeitsplatz (produktiv)                      IT-verwalteter Host / Perimeter
 ┌──────────────────────────────┐                      ┌──────────────────────────────────────────────┐
 │ echte DB, Kanzlei-Instanz    │  KEINE Verbindung     │ Host-/Perimeter-Firewall (Vertrauensgrenze 1)│
 │ (nicht Teil des Benchmarks)  │ ───────────────────── │  Standard: alles blockieren (v4/v6, TCP/UDP)  │
 └──────────────────────────────┘                      │  Ausnahme: Endpunkt-IP:Port (C-14, IT)        │
                                                       │  Protokoll: Regel-Export + Treffer-Log        │
                                                       │  ┌────────────────────────────────────────┐   │
                                                       │  │ Benchmark-VM (Vertrauensgrenze 2)        │   │
                                                       │  │  Windows-Firewall: ausgehend Block       │   │
                                                       │  │  (per Richtlinie gesperrt, kein Admin für│   │
                                                       │  │  das Benchmark-Konto)                    │   │
                                                       │  │  kein DNS, Endpunkt fest eingetragen     │   │
                                                       │  │  Lexono-Repo (Commit), synthetische Akten│   │
                                                       │  │  Python-Zaun §83–§85 (Schicht 3, keine   │   │
                                                       │  │  Grenze)                                 │   │
                                                       │  │  Ergebnisablage (C-11), Export nur über  │   │
                                                       │  │  kontrollierten Kanal nach dem Lauf      │   │
                                                       │  └────────────────────────────────────────┘   │
                                                       │ Kanarienziele (IT): Senken außerhalb, mit Log │
                                                       └──────────────────────────────────────────────┘
```

**Komponenten und Verantwortung:**

| Komponente | Verantwortung (Rolle, Person noch zu benennen) |
|---|---|
| Host bzw. Perimeter-Firewall, virtueller Switch, Regel-Export, Treffer-Log | IT |
| Golden Image der VM (Windows, Python, Repo-Stand, Tesseract-Bundle), Gast-Firewall per Richtlinie | IT plus Technik |
| Benchmark-Konto in der VM ohne Administratorrechte | IT |
| Kanarienziele (Senken außerhalb mit Protokoll) für die Abnahme | IT |
| Freigabe von Endpunkt und Plan | Owner (C-14, C-7) |
| Abnahme der Isolation (unabhängig von der Technik, die den Lauf baut) | prüfende Stelle, z. B. IT-Sicherheit – **zu benennen** |

**Vertrauensgrenzen:**
1. **Host bzw. Perimeter.** Er ist die eigentliche Grenze; die VM kann ihn nicht ändern.
2. **Gast-Firewall.** Sie ist die zweite Linie, aber änderbar, falls jemand in der VM Administratorrechte erlangt.
3. **Python-Zaun.** Er erkennt Fehler früh, ist aber keine Grenze.

**Fehlerverhalten:** Weichen Regeln von der Politik ab, ist der Regelnachweis älter als der Lauf, schlägt ein
Abnahmetest fehl oder trifft ein Kanarienziel ein, gibt es **keinen Start** bzw. einen **Abbruch**. Der Treiber würde
dafür zusätzlich eine extern erzeugte Bestätigung prüfen; das ist eine **spätere Code-Änderung** (C-14), heute nicht
umgesetzt.

**Wiederherstellung:**
- Nach jedem Lauf wird die VM auf das Golden Image zurückgesetzt.
- Ergebnisse verlassen die VM nur über den in C-11 festzulegenden Kanal.
- Bei Befunden bleibt die VM angehalten zur Analyse und wird danach verworfen.

## 4 Netzwerkpolitik (Entwurf)

Maschinenlesbar in `isolation/netzwerkpolitik.json`, geprüft durch `tests/test_s86_isolation_design.py`.

| Bereich | Regel |
|---|---|
| Standard | ausgehend und eingehend **blockieren**: IPv4, IPv6, TCP, UDP, ICMP und andere Protokolle |
| Freigabe | genau **ein** Ziel: Endpunkt-IP(s) und Port, TCP, TLS-Pflicht, befristet, mit Freigebendem. Heute **alles OFFEN** (`null`) |
| DNS | **kein DNS aus der VM** (UDP/TCP 53, DoH 443, DoT 853, LLMNR 5355, mDNS 5353, NetBIOS 137). Der Endpunkt wird außerhalb aufgelöst und fest eingetragen. Alternative: kontrollierter Resolver nur für den freigegebenen Namen. Entscheidung OFFEN |
| Direkte IP-Verbindungen | durch den Standard „blockieren“ auf IP-Ebene gesperrt (Host bzw. Perimeter); eine Ausnahme gilt nur für die eingetragenen Endpunkt-IPs |
| Proxy | keiner; WPAD/PAC aus, denn ein Proxy wäre ein zweiter Ausgang |
| Alternative Pfade | gesperrt: zweiter Adapter, WLAN, Bluetooth-PAN, USB-Netz, VPN, gemeinsame Host-Ordner, Host-Zwischenablage, ausgehendes RDP, Windows Update, Telemetrie, NTP |
| Unterprozesse, nativer Code | ohne Sonderregel: die Sperre gilt für die **ganze VM** (Host bzw. Perimeter), unabhängig vom Prozess |
| Loopback | nur innerhalb der VM (lokaler Recorder für Abnahmetests) |

**Offene Entscheidung zur Endpunkt-Adressierung:** Cloud-Endpunkte haben oft wechselnde IP-Adressen. Eine reine
IP-Freigabe braucht dann eine Pflege der Liste. Die Alternative ist eine FQDN- bzw. SNI-Filterung **am Perimeter**, das
heißt die IT-Firewall löst den Namen selbst auf, nicht die VM. Das ist eine IT-Entscheidung, die Wartbarkeit und
Risiko abwägt; **OFFEN**.

## 5 Abnahmematrix

Die Proben laufen in **getrennten Prozessen**: frischer Python-Interpreter mit `-I`, natives `ws2_32.connect` über
ctypes, `curl.exe`, `nslookup.exe`. Sie prüfen also die Wirkung **von außen**, nicht die des Lexono-Codes. Die
Schutzfunktion `_guard_target` verweigert jedes Nicht-Loopback-Ziel **vor** dem Prozessstart, solange nicht
`LEXONO_ISOLATION_ACCEPTANCE=1` und ein Manifest der IT gesetzt sind (Test
`test_probe_guard_refuses_external_targets_before_any_process_starts`).

| # | Abnahmekriterium | Test (`tests/test_s86_isolation_design.py`) | Ergebnis hier |
|---|---|---|---|
| I1 | Politik: Standard blockieren, nichts erfunden, Python-Hook nicht als Grenze | `test_policy_is_a_default_deny_draft_without_invented_decisions` | **PASS** |
| I2 | Regelprüfer akzeptiert nur den freigegebenen Endpunkt (synthetisch) | `test_rule_checker_accepts_only_the_approved_endpoint` | **PASS** |
| I3 | fehlende oder fehlerhafte Regeln verhindern den Start: Standard „zulassen“ bzw. „nicht konfiguriert“, leerer Export, UDP/DNS, beliebige Adresse, Adressbereich, beliebiger Port, DoT-Port, zusätzlicher Proxy, IPv6-Ziel | `test_rule_checker_rejects_missing_or_faulty_rules` (10 Fälle) | **PASS** |
| I4 | offene Endpunkt-Freigabe verhindert den Start | `test_rule_checker_refuses_start_while_endpoint_decision_is_open` | **PASS** |
| I5 | Ist-Stand dieser Maschine erfüllt die Politik nicht (ausgehend erlaubt) | `test_current_machine_does_not_meet_the_policy` | **PASS** (Befund: nicht isoliert) |
| I6 | Proben verweigern externe Ziele, bevor ein Prozess startet | `test_probe_guard_refuses_external_targets_before_any_process_starts` | **PASS** |
| I7 | Proben funktionieren in getrennten Prozessen (TCP, curl, UDP, DNS „localhost“, natives connect, nslookup gegen 127.0.0.1) | `test_probes_work_in_separate_processes_against_local_targets` | **PASS** (Selbsttest, **kein** Isolationsnachweis) |
| I8 | freigegebener Test-Endpunkt erreichbar | `test_acceptance_approved_test_endpoint_is_reachable` | **NICHT VERIFIZIERT** (nur in der Zielumgebung) |
| I9 | Kanarienziele (IPv4/IPv6) über TCP, curl, natives connect und UDP blockiert; IT bestätigt anhand der Protokolle, dass nichts ankam | `test_acceptance_canary_addresses_are_blocked` | **NICHT VERIFIZIERT** |
| I10 | DNS blockiert (eigene Resolver und Kanarienname) | `test_acceptance_dns_is_blocked` | **NICHT VERIFIZIERT** |
| I11 | Firewall-Export von außen entspricht der Politik | `test_acceptance_firewall_export_from_outside_matches_the_policy` | **NICHT VERIFIZIERT** |
| I12 | Start-Gate im Treiber prüft die extern erzeugte Bestätigung | – | **nicht umgesetzt** (spätere Code-Änderung, C-14) |

Ergebnis lokal: 16 bestanden, 7 übersprungen (I8–I11 für die Zielumgebung), Protokoll in
`audit/s86_abnahme_selbsttest.txt`.

**Manifest der Zielumgebung** (von der IT, nicht im Repository):
- `freigegebener_testendpunkt` {ip, port}: ein Testdienst, **kein** Modell-Endpunkt;
- `kanarienziele` [{ip, port}], IPv4 und IPv6;
- `kanarienname`;
- `dns_resolver`;
- `firewall_export`: Pfad zum Export des Hosts;
- `politik`: Pfad zur freigegebenen Fassung der Netzwerkpolitik.

## 6 Voraussetzungen und Nachweise

**Rechte und Infrastruktur** (alles **OFFEN**, Rollen zu benennen):
- Hypervisor-Host bzw. vorhandene Virtualisierung der IT, alternativ ein dedizierter Rechner;
- Administratorrechte für Host- bzw. Perimeter-Firewall und virtuellen Switch;
- Richtlinienverwaltung für die Gast-Firewall;
- ein Benchmark-Konto ohne Administratorrechte;
- Kanarienziele mit Protokollierung;
- ein Speicher für Golden Image und Snapshots.

**Kosten:** abhängig von vorhandener Infrastruktur (Hypervisor, Lizenzen, Arbeitszeit der IT). Hier **nicht
beziffert**, damit nichts erfunden wird; das ist eine Owner-Entscheidung.

**Nachweise vor einer Installation:**
1. Freigabe von Zielarchitektur und Politik durch Owner und IT; DNS-Modus und Adressierung des Endpunkts entschieden.
2. Benannte Verantwortliche für Host, Image, Abnahme und Kanarienziele.
3. Bestätigung, dass die Installation die produktive Kanzlei-Maschine nicht berührt.

**Nachweise vor C-14:**
1. Die Abnahmetests I8–I11 haben in der Zielumgebung bestanden; das Protokoll ist von der prüfenden Stelle bestätigt.
2. Die Kanarien-Logs bestätigen: nichts kam an.
3. Die Wiederherstellung auf das Golden Image ist geprüft.
4. Das Start-Gate (I12) ist als gesondert reviewte Code-Änderung umgesetzt und negativ getestet.
5. Dazu kommen die offenen Entscheidungen J1–J6, F-1, C-7 und C-11 laut `PILOT_CHECKLISTE.md`.

## 7 Restrisiken

- **Admin-Zugriff in der VM:** Wer in der VM Administratorrechte erlangt, kann die Gast-Firewall ändern. Deshalb gilt die
  Grenze erst am Host bzw. Perimeter.
- **Freigegebener Endpunkt:** Er ist ein Ausgang. Was dorthin gesendet wird, schützt nur der Datenschutzpfad (Gateway,
  Leak-Check), nicht die Isolation.
- **IP-Freigaben:** Sie können bei wechselnden Cloud-Adressen veralten (fail-closed: der Lauf scheitert). Eine
  FQDN-Filterung am Perimeter hat eigene Risiken, etwa gemeinsame CDN-Adressen.
- **Kanarienziele:** Sie belegen Sperren nur für die geprüften Ziele und Protokolle, nicht für alle denkbaren.
- **Isolation:** Bis zur Einrichtung und unabhängigen Abnahme ist sie **nicht vorhanden**. Diese Arbeit belegt nur das
  Konzept und die Werkzeuge.
