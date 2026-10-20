# §93 D1 – Änderungs- und Ausführungsplan für die reale Isolationstestumgebung

Stand 20.10.2026. Branch `feature/feedback-diagnose`, HEAD `b7e2daa` (heute geprüft; **nicht committet**); `main` =
`5735e5b`.

**Grenzen dieser Arbeit:**
- Nur lesende Bestandsaufnahme (ohne Administratorrechte) und Dokumentation.
- Keine Installation, keine VM, keine Änderung an Windows, Firewall, ACL, Netzwerk oder Konten.
- Keine Schlüssel, keine externen Verbindungen.
- Weder die Produktivdatenbank noch die Anwendung wurden geöffnet; vom Lexono-Datenordner wurden nur
  Dateigrößen-Metadaten gelesen.
- Fremde Dateien und die Berichte AUDIT_S88–S92 sowie `PRIVACY_REVIEW_ARCHITECTURE.md` blieben unverändert.
- **Nichts in diesem Plan ist ausgeführt.** Jede Systemänderung braucht deine ausdrückliche Freigabe. C-14 bleibt
  GESPERRT.

Kennzeichnung im Dokument: **[V]** = heute verifiziert, **[A]** = ältere Beobachtung, **[S]** = Schluss/Annahme,
**OFFEN** = nicht ermittelbar ohne Freigabe bzw. Administratorrechte.

## 1 Bestandsaufnahme

### 1.1 Repository und Werkzeuge

- [V] **Git-Stand:** HEAD `b7e2daa`. Die §90–§92-Arbeit (`owner_keys.py`, `owner_sign.py`, `isolation_evidence.py`,
  `isolation_acceptance.py`, `test_s90`–`test_s92`, Änderungen an Gate, Testhilfe und Matrix) ist **uncommittet**.
- [S] **Folge des fehlenden Commits:** Eine VM, die den Commit auscheckt, hätte das Owner-Gate und die Abnahmeskripte
  nicht. Der Fingerabdruck des Prüfwerkzeugs würde auf Host und VM nicht übereinstimmen.
  - **Ein Commit (und ggf. Push) ist Voraussetzung für die reale Abnahme. Entscheidung F1.**
- [V] **Matrix:** `isolation/vm_abnahme.json` steht unverändert auf NICHT DURCHGEFÜHRT.
- [V] **Netzwerkpolitik:** `isolation/netzwerkpolitik.json` steht auf ENTWURF; Endpunkt und DNS-Entscheidung sind
  offen.
- [V] **Werkzeuge vorhanden:**
  - Gate v2 (Owner-Schlüsselliste, Belegbündel), Urteil mit Ableitung aus Beobachtungen;
  - Gegenprobe je Kanal (§92);
  - `CanaryListener` mit Phasen und Funktionskontrolle, `calibration_probe`;
  - gesperrte echte Befehle `REAL_COMMANDS` (Port-ACL, VM-Konfiguration, Gast-Firewall, Netzadapter,
    Änderungsversuch).
- [V] **Lücken vor einer realen Ausführung (Code, rein lokal schließbar; Schritt A5, BLOCKIERT für reale Tests):**
  1. Es fehlt eine Kommandozeile für den **realen** Modus: `vm-teil`, `host-teil`, `buendeln`. Heute gibt es nur
     `trockenlauf`.
  2. Es fehlt ein Generator für das Abnahme-Manifest der §86-Proben (Kanarienziele, Resolver, Export).
  3. PowerShell-Ausgaben müssen normalisiert werden (Einzelobjekt gegen Liste bei `ConvertTo-Json`).
  4. Es fehlen Beobachter für V11 (Image-Hash) und V13 (Gegenprobe des Produktiv-Gates).
  5. Es fehlt ein Befehl zum Setzen und Entfernen der Port-ACL. Er gehört **nicht** in `REAL_COMMANDS`, das nur liest,
     sondern in ein eigenes, freigabepflichtiges Host-Skript mit Rückbau.

### 1.2 Host (Lenovo)

| Punkt | Befund |
|---|---|
| Gerät | [V] LENOVO 20W1S3GJ2H, Intel i5-1145G7, 4 Kerne / 8 Threads |
| Windows | [V] Windows 11 Pro, Build 26200 (Hyper-V ist in Pro enthalten) |
| RAM | [V] 15,7 GB gesamt, 7,7 GB frei (Ollama läuft: 3 Prozesse) |
| Laufwerk C: | [V] 61,7 GB frei von 237,5 GB, NTFS; einziges festes Laufwerk |
| Hypervisor, VBS | [V] Hypervisor erkannt, VBS „wird ausgeführt“, HVCI aktiv. [S] VT-x/SLAT sind damit aktiv: Windows meldet `VirtualizationFirmwareEnabled`/`SLAT` = False, sobald bereits ein Hypervisor läuft – das ist kein Widerspruch. |
| Hyper-V-Features | [V] `Microsoft-Hyper-V-All`, `-Hypervisor`, `-Services`, `-Management-PowerShell`, `-Tools-All` deaktiviert (InstallState 2); Sandbox, VirtualMachinePlatform, WSL deaktiviert; Hyper-V-PowerShell-Modul nicht vorhanden |
| Rechte | [V] Konto `Bonit` ist Mitglied von „Administratoren“; der laufende Prozess ist **nicht** erhöht (UAC). Alle Änderungen brauchen eine erhöhte Sitzung. |
| **Neustart ausstehend** | [V] **Ja** (`RebootPending` und `WindowsUpdate\RebootRequired` gesetzt). Letzter Start am 24.09.2026. |
| Firewall | [V] alle Profile aktiv; Standard ein- und ausgehend `NotConfigured` |
| Netzwerk | [V] nur WLAN aktiv (IPv4 per DHCP /24, IPv6 nur Link-Local); Ethernet und Bluetooth getrennt; kein vSwitch |
| Dienst `SharedAccess` (ICS) | [V] läuft (manueller Start); wichtig für den DNS-Port, siehe §3 |
| Ports der Kanarien | [V] TCP/UDP 53 sowie 47101–47105 derzeit frei |
| Lexono-Daten | [V] `C:\ProgramData\Lexono` 1,81 GB (nur Metadaten); Repository ohne `.venv`/`.git` 3,82 GB |
| Akku | [V] 96 %, Netzbetrieb (Status 2) |
| BitLocker | **OFFEN** – `manage-bde -status` braucht Administratorrechte |
| Wiederherstellungspunkte, Sicherung | **OFFEN** – nicht ohne Administratorrechte lesbar |

## 2 Systemvoraussetzungen

### 2.1 Speicherbedarf

| Posten | Bedarf | Grundlage |
|---|---|---|
| Gast-VHDX (Windows 11) | virtuell mindestens **64 GB** (Mindestanforderung Windows 11), dynamisch; belegt nach Installation **OFFEN** ([S] grob 20–30 GB) | Microsoft-Mindestanforderung; Belegung nicht gemessen |
| Installationsmedium (ISO) | **OFFEN** ([S] 6–7 GB, nach Installation löschbar) | – |
| Offline-Daten-VHDX (Repository zum Commit, Python-Installer, Wheels) | [S] 5–6 GB (Repository 3,82 GB [V] + Python/Wheels) | – |
| Differenzdatenträger je Lauf | **OFFEN** ([S] 1–5 GB, wird verworfen) | – |
| Belege, Logs | [S] < 100 MB | – |
| Reserve für Windows (Updates, Auslagerung) | [S] ≥ 15 GB | – |

- [S] Summe grob 45–60 GB bei **61,7 GB frei** [V]. **Der Platz reicht nicht verlässlich.**
- Ein Windows-11-Gast mit 64 GB virtueller Größe kann C: im schlimmsten Fall vollschreiben.
- **Entscheidung F3 (Platz), Optionen:**
  - a) Speicher auf C: freiräumen (wer, was – deine Entscheidung);
  - b) externer Datenträger (SSD) für die VM;
  - c) schlankerer Gast, z. B. Windows Server Core Evaluation mit geringerer Mindestgröße (Lizenz und Eignung prüfen).

  Ohne F3: BLOCKIERT.

### 2.2 Windows-Funktionen, Rechte, Neustarts

- **Erhöhte Sitzung für jede Änderung.** Das Konto ist Administrator; ich führe nichts ohne deine Freigabe und deine
  UAC-Bestätigung aus.
- **Neustart 1:** die bereits ausstehenden Windows-Updates abschließen (F2), **vor** jeder Hyper-V-Änderung.
- **Neustart 2:** Hyper-V aktivieren (`Microsoft-Hyper-V-All` inkl. PowerShell-Modul). Das legt automatisch den
  „Default Switch“ (NAT) an. Die Test-VM darf **nie** daran hängen; das prüft V9 über den Switch-Namen.
- **Lesen von Port-ACL und VM-Konfiguration:** Gruppe „Hyper-V-Administratoren“ oder erhöhte Sitzung.
- **Firewall-Regeln anlegen und entfernen:** erhöhte Sitzung.
- **Kanarien-Listener:** braucht keinen Administrator (bindet nur an die vEthernet-Adressen).

### 2.3 VM und Benchmark-Konto

- **VM:** Generation 2, Secure Boot, vTPM (Windows-11-Pflicht), statisch 4 GB RAM, 2 vCPU.
  - Am Host gelten 7,7 GB frei [V]. Ollama sollte während der Abnahme beendet sein; das ist deine Entscheidung im
    Rahmen von F5. Der Lexono-Start lädt das Modell bis zu 30 min (bekannte Beobachtung).
- **Ausstattung:** genau ein Netzadapter am internen Switch `lexono-abnahme`. Gastdienste aus, keine gemeinsamen Ordner,
  keine automatischen Checkpoints.
  - Die erweiterte Sitzung (Zwischenablage) ist eine **hostweite** Einstellung (`Set-VMHost
    -EnableEnhancedSessionMode $false`) und Teil von F7.
- **Gast:**
  - lokales Benchmark-Konto ohne Administratorrechte;
  - Firewall per lokaler Richtlinie: alle Profile aktiv, ausgehend Block;
  - statische Adressen 10.88.0.2/24 und fd00:88::2/64, **kein DNS-Server, kein Gateway**;
  - Windows Update, Telemetrie und Zeitdienst ohne Ziel (es gibt ohnehin keinen Abfluss).
- **Bereitstellung offline:** Repository am freigegebenen Commit, Python und Wheels kommen über eine Daten-VHDX vom
  Host. Die VM braucht kein Internet.
  - Gast-ISO und Lizenz gehören zu Entscheidung **F6**; der Download ist eine externe Verbindung auf dem Host.

### 2.4 Sicherung und Wiederherstellung (vor Neustart 1)

1. **Lexono-Daten:** über die eingebaute Sicherungsfunktion der Anwendung oder eine Dateikopie bei beendeter Anwendung –
   von dir ausgeführt. Die Datenbank öffne ich nicht. Nachweis: SHA-256 der Sicherung, Prüfsumme notiert.
2. **BitLocker:** Status prüfen und den **Wiederherstellungsschlüssel bereithalten**, bevor sich Boot- oder
   Hypervisor-Konfiguration ändert. **OFFEN.**
3. **Systemwiederherstellungspunkt** (erhöht, `Checkpoint-Computer`) – sofern der Schutz aktiv ist. **OFFEN.**
4. **Rückweg für Hyper-V:** `Disable-WindowsOptionalFeature -Online -FeatureName Microsoft-Hyper-V-All`, danach
   Neustart. Die VM-Dateien werden getrennt gelöscht.

### 2.5 Auswirkungen auf den Kanzleibetrieb

- **Zwei Neustarts:** Zeitfenster außerhalb der Arbeitszeit (F2/F5).
- **Speicher:** Die VM belegt 4 GB fest, Ollama und Lexono gleichzeitig sind knapp.
- **Plattenplatz:** siehe F3.
- **Zusätzliche virtuelle Adapter:** Default Switch und `lexono-abnahme`. [S] Für WLAN, VPN und Drucker ist ein Einfluss
  wenig wahrscheinlich, aber **OFFEN**.
- [S] Leistung: Der Hypervisor läuft bereits (VBS), daher kaum zusätzliche Grundlast.
- Die Lexono-Anwendung und ihre Datenbank werden durch den Plan nicht verändert.

## 3 Netzwerk- und Firewallplan

### 3.1 Topologie

- **Stufe A (ohne Endpunkt):**
  - **interner** vSwitch `lexono-abnahme`, **ohne NAT und ohne Gateway**;
  - Host-vNIC `vEthernet (lexono-abnahme)` mit 10.88.0.1/24 und fd00:88::1/64 (ULA);
  - VM mit 10.88.0.2 und fd00:88::2.
  - [S] Damit gibt es keinen Weg ins Internet. Das ist eine Eigenschaft der Topologie und **kein** Nachweis der ACL.
- **Stufe B (nach D3):** NAT bzw. Route nur für den Relay-Endpunkt, ACL-Allow nur für Relay-IP:443. Erst jetzt ist die
  ACL die eigentliche Grenze; **V4–V8 und V12 müssen dann vollständig wiederholt werden.**
- **Grenze der Aussage:** Lokale Tests belegen nur Ziele am Host. Dass die Deny-all-Regel auch ins Internet wirkt, ist
  ein Schluss. Optional gäbe es ein Internet-Kanarienziel (E2): Kosten, Fremdsystem, eigene Entscheidung.

### 3.2 Kanäle

Alle Kanäle laufen in Richtung VM → Host (eingehend am Host, ausgehend an der VM).

| Kanal | Ziel (Host) | Port | Vorher (ohne ACL) erwartet | Nachher (mit ACL) erwartet | Abbruch |
|---|---|---|---|---|---|
| IPv4/TCP | 10.88.0.1 | TCP 47101 | VM: „erreicht“; Host: Treffer von 10.88.0.2 | VM-Verbindung scheitert; **kein** Treffer | vorher kein Treffer → BLOCKIERT, Lauf stoppen; nachher Treffer → FAIL, VM zurücksetzen |
| IPv6/TCP | fd00:88::1 | TCP 47102 | wie oben, Quelle fd00:88::2 | wie oben | wie oben |
| IPv4/UDP | 10.88.0.1 | UDP 47103 | VM: „gesendet“; Host: Treffer | kein Treffer | wie oben |
| IPv6/UDP | fd00:88::1 | UDP 47104 | wie oben | kein Treffer | wie oben |
| IPv4/DNS | 10.88.0.1 | UDP 53 | VM: `nslookup lexono-abnahme.invalid 10.88.0.1` bzw. Datagramm „gesendet“; Host: Treffer | kein Treffer | wie oben; außerdem Abbruch, wenn Port 53 vor dem Lauf belegt ist |

- **Port 53:** Er ist heute frei [V]. Der ICS-Dienst (`SharedAccess`) bindet DNS nur an NAT-Switches wie den Default
  Switch. [S] An `vEthernet (lexono-abnahme)` sollte er nicht binden – das ist vor jedem Lauf mit
  `Get-NetUDPEndpoint -LocalPort 53` zu **prüfen**.
- **Nicht abgedeckt** (bewusst, wird nicht als geprüft gewertet):
  - DNS über TCP 53, DoT 853, DNS über IPv6;
  - ICMP, andere Ports, andere Ziele.

### 3.3 Gegenprobe (phasenweise, wie in §92 implementiert)

- **a) Listener-Funktion:**
  - Der Host startet `CanaryListener` (Modus `real`, nur mit `LEXONO_ISOLATION_ACCEPTANCE=1`) auf 10.88.0.1 und
    fd00:88::1 mit den fünf Ports.
  - Die Funktionskontrolle muss alle fünf Ziele erfassen; sonst Abbruch.
- **b) Phase „vorher“:**
  - Die VM hat **keine** Extended ACL; die Host-Firewall-Regeln aus §3.4 sind aktiv.
  - Die VM führt `calibration_probe` aus. Der Host-Teil schreibt die Gegenprobe (E1) mit dem Hash der ACL zu diesem
    Zeitpunkt; die ACL-Liste ist leer.
- **c) Aktivierung:**
  - `Add-VMNetworkAdapterExtendedAcl`: ausgehend Deny `0.0.0.0/0` und `::/0`, alle Protokolle, Weight 1. In Stufe B
    kommt zusätzlich Allow Relay-IP/32 TCP 443 Stateful mit höherem Weight hinzu.
  - Unmittelbar danach den Export vom Host lesen (V1). Der Exportzeitpunkt gilt als Aktivierung; Vorher- und
    Nachher-Hash gehen in V10 ein.
- **d) Phase „nachher“:**
  - In der VM laufen die §86-Abnahmetests: tcp, curl, native, udp, dns.
  - Sie zielen auf **dieselben** fünf Ziele; der Listener läuft weiter.
  - Danach V9, V10 (ACL erneut exportiert und Hash verglichen) und V13.
- **e) Zuordnung:**
  - Treffer gehören über das Feld `phase` zu genau einer Phase.
  - Jeder Beleg trägt Test-ID, Zeit, Erzeuger und `lauf_sha256`.
  - Das Bündel baut der Host (`assemble`); das Urteil kommt aus `owner_sign urteil`.
- **Abbruchregeln:**
  - Vorher-Treffer fehlt für einen Kanal → Lauf beenden; dieser Kanal und die abhängigen Tests sind BLOCKIERT.
  - Treffer in „nachher“ → FAIL; VM zurücksetzen (V11), Ursache klären.
  - Listener- oder Portfehler → Abbruch.
  - Ein Lauf, der länger als 24 h dauert, ist veraltet (Belegalter).

### 3.4 Host-Firewall-Freigabe (eng begrenzt, befristet)

- **Gruppe:** `Lexono-Abnahme`. Regeln **eingehend**, Aktion Allow.
- **Einschränkungen jeder Regel:**
  - `-InterfaceAlias "vEthernet (lexono-abnahme)"`;
  - `-RemoteAddress 10.88.0.2,fd00:88::2`;
  - `-LocalAddress 10.88.0.1,fd00:88::1`.
- **Fünf Regeln:**
  - TCP 47101 (nur IPv4-Adressen);
  - TCP 47102 (IPv6);
  - UDP 47103 (IPv4);
  - UDP 47104 (IPv6);
  - UDP 53 (IPv4).
- **Kein Programmfilter.** Der Listener ist Python; ein Programmfilter auf `python.exe` würde jeden Python-Prozess
  zulassen und brächte keinen Gewinn.
- **Dauer:** angelegt direkt vor Phase „vorher“, entfernt direkt nach dem V12-Export. Höchstens ein Arbeitstag, in
  derselben Sitzung.
- **Nachweis:**
  - `Get-NetFirewallRule -Group Lexono-Abnahme` vor dem Anlegen exportieren (leer erwartet);
  - nach dem Anlegen exportieren (genau 5 Regeln);
  - nach dem Rückbau exportieren (leer).
- **Rückbau:** `Remove-NetFirewallRule -Group Lexono-Abnahme` und erneuter Export.

## 4 Ausführungsplan

Jeder Schritt ab Teil B ist **genehmigungspflichtig**. Bei einer Abbruchbedingung wird angehalten und berichtet; der
nächste Schritt folgt erst nach deiner Freigabe.

### Teil A – vorbereitende Prüfungen und Arbeiten (keine Systemänderung)

| # | Schritt | Rechte | Erwartet | Abbruch, Rückbau |
|---|---|---|---|---|
| A1 | §90–§93 reviewen und **committen** (F1); Fingerabdruck des Prüfwerkzeugs festhalten | – | Commit-ID, sauberer Arbeitsbaum ohne die fremden Dateien | fremde Dateien bleiben ungestaged |
| A2 | Code-Lücken A5 schließen (§1.1 Punkte 1–5), testen, committen | – | Tests grün; Trockenlauf PASS | – |
| A3 | Lesende Prüfung in erhöhter Sitzung: BitLocker-Status, Systemschutz, Port 53 und 47101–47105 frei | erhöht, nur lesen | Werte dokumentiert | BitLocker ohne verfügbaren Wiederherstellungsschlüssel → Stopp |
| A4 | Sicherung der Lexono-Daten (von dir), SHA-256 notieren; BitLocker-Schlüssel sichern | du | Hash der Sicherung | ohne Sicherung kein Teil B |

### Teil B – genehmigungspflichtige Windows-, Hyper-V- und Firewall-Änderungen

| # | Schritt | Rechte | Risiko | Rückbau |
|---|---|---|---|---|
| B1 (F2) | ausstehende Windows-Updates abschließen, Neustart | erhöht | Ausfallzeit; Update-Probleme | Windows-Bordmittel |
| B2 (F4) | Wiederherstellungspunkt anlegen (falls aktiv) | erhöht | – | – |
| B3 (F5) | Hyper-V aktivieren, Neustart; danach `Get-VMHost`, Default Switch dokumentieren | erhöht | Neustart; neue Adapter; BitLocker-Abfrage möglich | Feature deaktivieren, Neustart |
| B4 (F7) | interner Switch `lexono-abnahme` mit Host-IPs; erweiterte Sitzung hostweit aus | erhöht | gering | `Remove-VMSwitch`, `Set-VMHost -EnableEnhancedSessionMode $true` |
| B5 (F3/F6) | Gast-ISO beschaffen (externer Download), VM anlegen (Gen 2, vTPM, 64 GB dynamisch, 4 GB RAM, nur `lexono-abnahme`), Gast installieren **ohne Netz** | erhöht | Plattenplatz; Lizenz | VM und VHDX löschen |
| B6 | Gast konfigurieren (Benchmark-Konto, Firewall-Richtlinie, statische IPs, kein DNS), Offline-Daten-VHDX mit Repository am Commit A1/A2, Python | VM-Admin (nur bei der Einrichtung) | Fehlkonfiguration fällt in V2/V9 auf | Gast verwerfen |
| B7 | Golden Image: VM aus, Eltern-VHDX schreibgeschützt, SHA-256; je Lauf ein Differenzdatenträger | erhöht | Hash dauert bei großen Dateien Minuten | Differenzdatenträger löschen |
| B8 (F8) | Host-Firewall-Regeln §3.4 anlegen (nur für die Testsitzung) | erhöht | eingehende Öffnung, eng begrenzt | Gruppe löschen, Export |

### Teil C – reale Tests V1–V13 (Stufe A ohne Endpunkt, F9)

| # | Schritt | Erwartet / Hinweis |
|---|---|---|
| C1 | Listener starten (Funktionskontrolle), Phase „vorher“, Gegenprobe aus der VM | alle 5 Kanäle mit Treffer; sonst Abbruch |
| C2 | Port-ACL aktivieren und exportieren (V1-Rohdaten), Vorher-Hash V10 | ACL-Export mit Deny für v4 und v6 |
| C3 | Phase „nachher“: §86-Abnahmetests in der VM, V2- und V9-Beobachtungen in der VM | keine Treffer |
| C4 | Host-Teil: V9, V10 (Nachher-Hash), V11 (Zurücksetzen und Image-Hash), V12, V13 | – |
| C5 | Bündel und Urteil (`owner_sign urteil`). **V1 und V3 können in Stufe A nicht PASS sein**: Endpunkt in der Politik OFFEN, kein Relay (D3) | erwartet: Gesamturteil **nicht PASS** – Stufe A ist eine technische Vorabnahme und **nicht signierbar** |
| C6 | Rückbau: Firewall-Gruppe entfernen, VM aus, Differenzdatenträger verwerfen, Exporte sichern | Firewall-Export leer |

Die Belege aus Stufe A sind an die Politik im ENTWURF gebunden. Sie sind **nicht** in Stufe B übertragbar; dort folgt
eine vollständige Wiederholung.

### Teil D – spätere Entscheidungen

- **D3 (Relay):**
  - Betrieb, Adresse und Log-Format bestätigen; dann die Politik FREIGEGEBEN mit Endpunkt und
    `kein_dns_in_der_vm`;
  - NAT bzw. Route nur zum Relay, ACL-Allow Relay-IP:443;
  - **Stufe B:** vollständige Wiederholung von V1–V13, V3 mit E2-Log des Relays, ohne Modellaufruf.
- **D4 (Schlüssel):**
  - Wurzel- und Freigabeschlüssel erzeugen, auf dem Host, mit deiner Passphrase;
  - Fingerabdruck per reviewter Code-Änderung pinnen;
  - Schlüsselliste schreibgeschützt bereitstellen;
  - erst danach eine Bestätigung signieren, nur bei Gesamturteil PASS.

### Teil E – C-14

Bleibt **GESPERRT**, auch nach einer erfolgreichen Stufe B. Die Freigabe ist eine eigene Entscheidung mit J1–J6, C-7
und C-11 sowie einer reviewten Treiberänderung (Relay-Endpunkt laut Bestätigung).

## 5 Risikobewertung

| Thema | belegt | Annahme | noch zu prüfen |
|---|---|---|---|
| Hyper-V Extended ACL | Code und Tests prüfen Export und Kette lokal | Deny-all mit Weight 1 sperrt alle Protokolle beider Familien; Allow mit höherem Weight hat Vorrang | ob Extended ACLs auf **Windows 11 (Client-Hyper-V)** durchgesetzt werden; Verhalten bei zustandsbehafteten Regeln – genau das zeigt C1–C3 |
| IPv6 | Kanäle und Gegenprobe getrennt implementiert | statische ULA ohne Router Advertisement genügt für die Gegenprobe | ob Deny `::/0` Neighbor Discovery (ICMPv6) blockiert – die Sperre wäre dann trotzdem eine ACL-Wirkung; Link-Local-Ziele (fe80::) sind **nicht** abgedeckt |
| DNS | nur IPv4/UDP 53 zum Host geprüft | kein DNS-Server im Gast ⇒ keine Auflösung | DNS über TCP, DoT/DoH, IPv6-DNS: **nicht abgedeckt**; ICS auf Port 53 vor jedem Lauf prüfen |
| Absenderfälschung | Vorher-Treffer zählen nur mit VM-Quelladresse; nachher zählt **jede** Quelle | Hyper-V-Standard: MAC-Spoofing aus | IP-Spoofing aus der VM ist möglich. Es könnte eine Vorher-Erreichbarkeit vortäuschen, aber keine Nachher-Sperre (konservativ). Auf dem Host bräuchte das gefälschte Senden Raw-Sockets bzw. Administratorrechte. |
| Belegalter | höchstens 24 h bei Nutzung, Gate vor jedem Aufruf (§91) | ein Lauf A/B passt in einen Tag | Dauer der Hash-Berechnung und des ganzen Laufs messen |
| Golden-Image-Rücksetzung | Kriterien V11 implementiert | schreibgeschützte Eltern-VHDX plus Differenzdatenträger ist reproduzierbar | Hash-Dauer; ob Windows-Aktivierung oder vTPM das Zurücksetzen stören |
| Beobachtbarkeit | Listener, Phasen, Exporte, Hashes | Host-Belege sind korrekt (E1 ist nicht vom Owner unabhängig) | Gefälschte Host-Belege sind nur über Signatur und Owner-Verantwortung adressiert. Internet-Ziele werden nicht beobachtet. |
| Host-Firewall | Regeln eng begrenzt geplant | Profil des vEthernet-Adapters (meist „Öffentlich“) beeinflusst die Regel nicht, weil die Schnittstelle gebunden ist | nach B4 das Profil des neuen Adapters prüfen |
| Kanzleibetrieb | Neustart ausstehend, Platz knapp, RAM knapp | – | Auswirkung der vSwitches auf VPN/WLAN |

## 6 Offene Fragen

1. Ist BitLocker aktiv, und liegt der Wiederherstellungsschlüssel vor? (A3)
2. Ist der Systemschutz (Wiederherstellungspunkte) aktiv? (A3)
3. Speicherstrategie (F3) und Gastbetriebssystem samt Lizenz (F6).
4. Zeitfenster für zwei Neustarts (F2, F5).
5. Darf Ollama bzw. Lexono während der Abnahme beendet sein?
6. Relay-Daten für Stufe B (D3).

## 7 Freigabeentscheidungen (deine; ich entscheide nicht)

| # | Entscheidung | Empfehlung zur Reihenfolge |
|---|---|---|
| F1 | §90–§93 committen (und ggf. pushen) | zuerst – ohne Commit keine reproduzierbare VM-Abnahme |
| F2 | ausstehende Windows-Updates und Neustart | vor jeder Hyper-V-Änderung |
| F3 | Speicherstrategie | vor B5 |
| F4 | Sicherungen (Lexono-Daten, BitLocker-Schlüssel, Wiederherstellungspunkt) | vor B1 |
| F5 | Hyper-V aktivieren und Neustart | nach F2–F4 |
| F6 | Gastbetriebssystem, Lizenz, ISO-Download | vor B5 |
| F7 | interner Switch, Host-IPs, erweiterte Sitzung aus | nach F5 |
| F8 | befristete Host-Firewall-Regeln (§3.4) | erst unmittelbar vor C1 |
| F9 | Durchführung von Stufe A | nach A2 und B1–B8 |
| D3, D4 | Relay, Schlüssel | danach; Stufe B erst mit D3 |
| C-14 | Treiberfreigabe | bleibt GESPERRT |

Empfohlene Reihenfolge: F1 → A2 (Code-Lücken) → A3/A4 → F4 → F2 → F3/F6 → F5 → F7 → B5–B7 → F8 + F9 → D3 → Stufe B →
D4 → Signatur. C-14 bleibt getrennt.

## 8 Ergebnisübersicht

| Punkt | Ergebnis |
|---|---|
| Git-Stand verifiziert (Branch, HEAD, fremde Dateien unverändert) | **PASS** |
| Lesende Bestandsaufnahme des Hosts (ohne Administratorrechte) | **PASS** (Werte §1.2) |
| BitLocker, Systemschutz | **NICHT DURCHGEFÜHRT** (Administratorrechte nötig) – OFFEN |
| Speicher für VM, Golden Image und Reserve | **BLOCKIERT** – 61,7 GB frei reichen nicht verlässlich (F3) |
| Code für den realen Modus (A5) | **NICHT DURCHGEFÜHRT** – reale Tests BLOCKIERT bis A2 |
| Windows-, Hyper-V-, Firewall-Änderungen | **NICHT DURCHGEFÜHRT** (Freigaben F2–F8) |
| V1–V13 real, Gegenprobe über einen echten VM-Pfad | **NICHT DURCHGEFÜHRT** |
| Internet-Isolation | **nicht behauptet**; lokale Ziele belegen sie nicht |
| C-14 | **GESPERRT** |
