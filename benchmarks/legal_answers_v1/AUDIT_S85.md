# §85 Sicherheitsarchitektur und Windows-Isolation

Stand 19.10.2026, Ausgangsstand `f2fd382` (§84, verifiziert: HEAD = Remote, Working Tree nur mit den 4 fremden
Logo-/UX-Dateien), Branch `feature/feedback-diagnose`; `main` unverändert (`5735e5b`).

**Grenzen dieser Arbeit:**
- Es gab keine Modell- oder Cloud-Aufrufe, keine externen DNS-Abfragen oder Verbindungen, keinen echten API-Schlüssel,
  keine Kosten und keinen Zugriff auf die produktive Datenbank.
- Firewall, Konten, Richtlinien und Systemkonfiguration wurden **nicht** verändert; die Windows-Umgebung wurde nur
  gelesen.
- **C-14 bleibt GESPERRT.** J1–J6, F-1, C-7 und C-11 sind unverändert offen.
- Es waren reine Backend- und Sicherheitstests ohne Screenshots. Nachweise: `audit/s85_*`.

**Kernaussage:** Der Schutz ist eine **Schutzschicht im Python-Prozess**, keine Sicherheitsgrenze. Gegen nativen Code
hält er nachweislich nicht; das zeigen zwei Tests (Abschnitt 4). Eine Isolation durch das Betriebssystem besteht
**nicht**.

## 1 Bedrohungsmodell und Garantien

| Kontrolle | Ort | Einordnung |
|---|---|---|
| Providerprüfung (Cloud- und HTTP-Clients im Provider, rekursiv) | `answer_driver.assert_local_only` | **getestet** für `anthropic`, `httpx`, `requests`, `urllib3`, `openai`, `app.ai_providers`; andere Bibliotheken nur über den Netzzaun |
| Sockets: `connect`/`connect_ex`, roher `_socket`, `sendto`/`sendmsg` | Audit-Hook | **getestet blockiert** (§84) |
| DNS: `getaddrinfo`, `gethostbyname(_ex)`, `gethostbyaddr`, `getnameinfo` | Audit-Hook, vor dem C-Aufruf | **getestet blockiert** (§84) |
| asyncio unter Windows (Proactor) | Ummantelung `IocpProactor.connect`/`sendto` | **getestet blockiert** (§84) |
| ctypes: Netz-DLL laden, Netzfunktion nachschlagen | Audit-Hook | **getestet blockiert** (§84); nur Namen auf der Liste |
| ctypes: **vorab** beschaffter Funktionszeiger | – | **nicht abgedeckt**, Umgehung nachgewiesen (Abschnitt 4) |
| direkter Overlapped-`ConnectEx` (ohne Proactor) | – | **nicht abgedeckt**, Umgehung nachgewiesen (Abschnitt 4) |
| C-Erweiterungen mit eigenem Netzwerk-Stack | – | **nicht abgedeckt** (theoretisch; im Projekt keine bekannt) – **NICHT VERIFIZIERT** |
| Loopback nur zu validierten Prüfendpunkt-Ports (neu) | Audit-Hook | **getestet** |
| Prozessstart nur per `ProgramRule` (Pfad, Hash, Ordner, Argumente, Umgebung) | Audit-Hook | **getestet** |
| Netzwerk- und Prozessverhalten zugelassener Programme (Tesseract) und ihrer Kindprozesse | – | **NICHT VERIFIZIERT** (keine OS-Isolation; Argumente eng begrenzt) |
| absichtliche Manipulation des Zauns (Zustand, Hook-Liste, Code) | – | **nicht abgedeckt**, nur über Review |

## 2 Prozess-Zulassungsliste – Befunde und Behebung

**Probe** (`audit/s85_zulassungsliste_vorher.txt` und `…_nachher.txt`; die Zaunentscheidung wurde direkt geprüft,
**ohne** Prozessstart): Vorher wurden **alle 9** manipulierten Aufrufe zugelassen.

| # | Befund (vorher) | Risiko | Behebung |
|---|---|---|---|
| R1 | **Tesseract mit beliebigen Argumenten** zugelassen. Das gebündelte Tesseract enthält `libcurl.dll`/`libssh2.dll`; eine **URL als Eingabe** wäre ein Netzzugriff, ebenso UNC-Pfade (SMB) für Eingabe, Ausgabe und `--tessdata-dir` | externer Zugriff über ein zugelassenes Programm | exaktes Argumentmuster wie bei pytesseract in Lexono: `--version`, `--list-langs` oder `<Eingabe> <Ausgabe> [-l <Sprachen>] txt`; nur lokale absolute Pfade (keine URL, kein UNC, kein Gerätepfad); Sprachkürzel per Muster; keine Zusatzoptionen |
| R2 | Tesseract liest eine Nicht-Bilddatei als **Bildliste** (Zeilen = Pfade/URLs) | indirekte URL-Abrufe | die Eingabe muss eine echte Bilddatei sein (Signaturprüfung PNG/BMP/TIFF/JPEG/GIF/PNM) |
| R3 | Start als **bloßer Name** (`git`) zugelassen. Windows-`CreateProcess` sucht dann zuerst im Programmverzeichnis von Python und im aktuellen Verzeichnis; geprüft wurde aber der Treffer von `shutil.which` | anderer Binärcode als geprüft | Aufruf **nur mit absolutem, festgeschriebenem Pfad**; Auflösung nur über absolute PATH-Einträge, nie über das aktuelle Verzeichnis, relative oder UNC-Einträge |
| R4 | Binärdatei bzw. danebenliegende DLLs konnten nach der Prüfung ausgetauscht werden | anderer Code unter zugelassenem Namen | SHA-256 der Datei und Fingerabdruck des Programmordners (Name, Größe, Zeitstempel) beim Aktivieren; Abgleich bei **jedem** Start |
| R5 | Umgebung ungeprüft (`GIT_EXEC_PATH`, `GIT_CONFIG_*`, `GIT_SSH_COMMAND`, `TESSDATA_PREFIX` als UNC) | Umlenkung auf fremde Programme oder Netzwerkpfade | Umgebungsprüfung je Programm: `TESSDATA_PREFIX` nur als lokales vorhandenes Verzeichnis; für git keine `GIT_*`-Variable |
| R6 | **git**: Der Starter von Git für Windows (`cmd\git.exe`) startet ein **weiteres** `git.exe`; diesen Enkelprozess sieht der Hook nicht | ungeprüfter Kindprozess | git **nicht mehr zugelassen** (fail-closed). Der Commit wird **ohne Prozess** aus `.git` gelesen (`HEAD`, lose Refs, `packed-refs`, Worktree/`commondir`); identisch mit `git rev-parse HEAD` (Test) |
| R7 | War der Commit nicht ermittelbar, entfiel die Prüfung auf einen veralteten Commit **still** (fail-open) | Lauf mit unbekanntem Stand | jetzt ein Verstoß: „aktueller Commit nicht ermittelbar – fail-closed“ |
| R8 | Loopback zu **jedem** lokalen Port erlaubt | Umweg über einen lokalen Proxy oder Dienst | Loopback nur zu den Ports der validierten Prüfendpunkte (`assert_local_only` liefert sie); Mock-Provider ohne Endpunkt = **kein** Loopback |
| R9 | Das Audit-Argument `command_line` von `_winapi.CreateProcess` ist in diesem Python-Build unbrauchbar (beobachtet: `'\x02'`) | ein Abgleich darauf wäre wirkungslos | die Prüfung stützt sich auf das `subprocess.Popen`-Ereignis, das schon die fertige Befehlszeile enthält; `CreateProcess` nur nach Zulassung, `application_name` muss passen |

**Auswirkung:** Während eines Laufs startet nur noch Tesseract, im engen Muster. git läuft nicht mehr (getestet:
Treiberlauf ohne git-Prozess).

## 3 Windows-Isolation

Umgebung (`audit/s85_windows_umgebung.txt`, nur gelesen):
- Windows 11 Pro, Build 26200;
- **keine Administratorrechte** (mittlere Integritätsstufe);
- Firewall in allen Profilen ein, ausgehend „nicht konfiguriert“ (also zulassen), keine Firewall-Gruppenrichtlinie;
- Windows Sandbox nicht installiert, Hyper-V-Dienste nicht vorhanden, Docker nicht vorhanden, `wsl.exe` vorhanden.

| Option | Voraussetzung | Wirkung | Bewertung hier | Status |
|---|---|---|---|---|
| Ausgehende Firewall-Regel je Programm (z. B. Block für `tesseract.exe`, `python.exe` außer Loopback) | **Administratorrechte**; systemweite Änderung | OS-Durchsetzung auch für Kindprozesse | ohne Freigabe nicht zulässig (Auftrag: keine Firewall-Änderung); Regelpflege bei jedem Update nötig | **nicht umgesetzt, NICHT VERIFIZIERT** |
| AppContainer / LPAC ohne Netzwerk-Capability für Tesseract (Start über `CreateProcess` mit `SECURITY_CAPABILITIES`) | kein Admin nötig, legt aber ein **AppContainer-Profil** im Benutzerkonto an; Dateizugriff nur mit ACL-Freigabe für das Container-SID | starke OS-Grenze für das Programm | Profil anlegen = Änderung am Konto (Freigabe nötig); aufwendig (ctypes, ACLs für Ein- und Ausgabe); nicht praktisch geprüft | **nicht umgesetzt, NICHT VERIFIZIERT** |
| Job Object (Kindprozesse begrenzen) | kein Admin | verhindert Enkelprozesse, **nicht** Netzwerk | begrenzter Nutzen; Prozess müsste angehalten gestartet werden | **nicht umgesetzt** |
| Windows Sandbox / Hyper-V-VM | Feature-Installation, Administrator, Neustart | starke Grenze für den **ganzen** Lauf | nicht vorhanden; Installation = Systemänderung | **nicht verfügbar** |
| WSL2 mit eigenem Netzwerk-Namespace bzw. Container | WSL-Distribution (nicht geprüft), Konfiguration | Netzwerk des Laufs isolierbar | Lexono-Stack ist auf Windows ausgelegt (Tesseract-Bundle, WebView2); Aufwand hoch | **NICHT VERIFIZIERT** |
| Separater Rechner bzw. VM ohne Internet für den Benchmark | Betriebsentscheidung | einfachste belastbare Grenze | organisatorisch; für einen echten Lauf gilt ohnehin die Freigabe C-14 | **Empfehlung** |

**Bewertung:** Keine Option ist hier ohne Systemänderung bzw. Freigabe umsetzbar und praktisch nachweisbar. Ich behaupte
deshalb **keine** Isolation. Der Lauf bleibt fail-closed: Außer dem eng begrenzten Tesseract startet kein Prozess.

## 4 Angriffsmatrix

`tests/test_s85_isolation_boundaries.py` (28 Tests), dazu §84 (22) und §83 (17). Netzwerkziele sind nur Loopback-Ports
dieses Prozesses; Prozesse werden vor dem Start abgewiesen.

| # | Angriffspfad | Schicht | Ergebnis |
|---|---|---|---|
| B1 | Programm im aktuellen Verzeichnis, relative, `.`- und UNC-PATH-Einträge | Prozess | **PASS** (nicht aufgelöst) |
| B2 | bloßer Name, andere Kopie derselben Binärdatei, fremde Binärdatei, `shell=True` mit Befehlsverkettung | Prozess | **PASS** (verweigert) |
| B3 | Binärdatei nach dem Aktivieren verändert; DLL in den Programmordner gelegt | Prozess | **PASS** (verweigert) |
| B4 | Tesseract: URL (libcurl), UNC-Eingabe, UNC-Ausgabe, `--tessdata-dir` UNC, `-c`-Option, Injektion im Sprachargument, Bildliste, relativer Pfad, Standardeingabe, andere Ausgabeart | Prozess | **PASS** (10 Fälle verweigert) |
| B5 | Tesseract-Umgebung (`TESSDATA_PREFIX` UNC bzw. nicht vorhanden) | Prozess | **PASS** |
| B6 | git-Umgebungsvariablen | Prozess | **PASS** (Regel) |
| B7 | Validator-Fehler bzw. Regel ohne Argumentprüfung | Lebenszyklus | **PASS** (fail-closed) |
| B8 | Commit ohne Prozess, identisch mit git; kaputtes, ausbrechendes oder fehlendes `HEAD` | Gate | **PASS**; nicht ermittelbar = Verstoß (R7) |
| B9 | Treiberlauf startet keinen git-Prozess | Prozess | **PASS** |
| B10 | Loopback zu nicht freigegebenem Port; leere Portliste; Zurücksetzen beim Abbau | Netzwerk | **PASS** |
| B11 | Mock-Provider spricht heimlich einen anderen lokalen Dienst an | Netzwerk | **PASS** (Lauf gestoppt) |
| B12 | Ports werden aus dem validierten Recorder abgeleitet; Mock ohne Endpunkt hat keine Ports | Provider | **PASS** |
| B13 | Positiv: regulärer OCR-Aufruf über pytesseract, `tesseract --version`, Recorder-Lauf | alle | **PASS** |
| B14 | **direkter Overlapped-`ConnectEx`** | Netzwerk | **FAIL – Grenze nachgewiesen**: Verbindung zu einem gesperrten lokalen Port kam zustande (`test_limit_direct_overlapped_connectex_bypasses_the_audit_hook`) |
| B15 | **vorab beschaffter `ws2_32.connect`-Zeiger** | Netzwerk | **FAIL – Grenze nachgewiesen** (`test_limit_preresolved_native_function_pointer_bypasses_the_audit_hook`) |
| B16 | C-Erweiterungen mit eigenem Netzwerk-Stack; Kindprozesse von Tesseract; Netzwerkverhalten von Tesseract bei zulässigen Argumenten | – | **NICHT VERIFIZIERT** |

B14 und B15 sind Tests, die die **Grenze belegen**: Sie bestehen, solange die Umgehung möglich ist. Wird sie eines Tages
geschlossen, schlagen sie fehl, und der Bericht muss angepasst werden. Sie laufen nur gegen einen lokalen Port.

## Änderungen

| Datei | Änderung |
|---|---|
| `scripts/legal_bench/net_fence.py` | `ProgramRule`, `trusted_executable`; Pfad, Hash und Ordner festschreiben; nur absolute Aufrufe; Argument- und Umgebungsprüfung; Loopback-Portbindung; `CreateProcess` nur nach Zulassung |
| `scripts/legal_bench/answer_driver.py` | Zulassungsliste nur noch Tesseract mit Argument-, Signatur- und Umgebungsprüfung; Ports aus `assert_local_only` |
| `scripts/legal_bench/answer_run.py` | `_git_commit` ohne Prozess; nicht ermittelbarer Commit = Verstoß |
| `tests/test_s85_isolation_boundaries.py` (neu) | 28 Tests |
| `tests/test_s84_network_process_fence.py` | an die strengere Zulassung angepasst (absolute Pfade, Regel-Objekte) |
| `benchmarks/legal_answers_v1/audit/s85_*` (neu) | Probe der Zulassungsliste vorher/nachher, Windows-Umgebung |
| `PILOT_CHECKLISTE.md`, `README.md`, `PRIVACY_REVIEW_ARCHITECTURE.md`, `FUTURE_ROADMAP.md` | Verweise; T-8 präzisiert |

Nicht geändert wurden Spezifikationen, Rubrik, LOCK, Vorlage, Freigabestatus, Datenschutzprüfungen, App-Code und
Systemkonfiguration.

## Tests

| Test | Ergebnis |
|---|---|
| `tests/test_s85_isolation_boundaries.py` (neu) | 28 bestanden (darunter 2 Grenznachweise) |
| §78–§85-Benchmarkdateien gemeinsam | 277 bestanden |
| Volle Suite in 6 Teilen | 5626 bestanden; 4 FAILED nur in `tests/test_design_refresh.py` (Logo-Tests, fremde Asset-Änderungen, bekannt) |

## Empfehlungen

1. Für einen echten Lauf (C-14) eine **OS-Grenze** vorsehen: am einfachsten ein eigener Rechner bzw. eine eigene VM,
   deren Netz nur den freigegebenen Endpunkt erlaubt (Firewall-Regel durch die IT); alternativ ein AppContainer für
   Tesseract. Beides braucht eine ausdrückliche Freigabe und einen praktischen Nachweis.
2. Bis dahin den Benchmark nur mit Mock bzw. lokalem Recorder betreiben, wie heute, und die Grenznachweise B14/B15 als
   bekannte Restrisiken führen.
3. OCR für Benchmark-Akten optional **vor** dem Lauf erledigen (Akten mit Textschicht bzw. vorab extrahiert). Dann müsste
   während des Laufs gar kein Prozess starten; das ist eine Betriebsentscheidung.
4. Den Zaun ausschließlich per gesondert reviewter Code-Änderung lockern.

## Restrisiken

- Nativer Code (eigene Netzwerk-Stacks, Overlapped, vorab beschaffte Zeiger) umgeht den Hook; das ist nachgewiesen.
- Tesseract läuft ohne OS-Isolation; begrenzt sind nur Argumente, Eingabe, Umgebung und Binärdatei, nicht das Verhalten.
- Der Ordner-Fingerabdruck stützt sich auf Name, Größe und Zeitstempel, nicht auf Hashes aller DLLs. Ein Austausch mit
  identischen Metadaten wäre theoretisch möglich.
- Gegen eine absichtliche Manipulation des Zauns schützt nur das Review.
