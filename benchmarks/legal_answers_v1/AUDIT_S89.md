# §89 Automatisiertes Sicherheits- und Freigabemodell – Konzept

> **Nachtrag §90:** Abschnitt 5 und die Roadmap-Schritte A1/A2 sind lokal umgesetzt, siehe `AUDIT_S90.md`.
> Die Schlüsselablage weicht begründet ab: scrypt + AES-256-GCM statt PKCS#8.

Stand 19.10.2026. Branch `feature/feedback-diagnose`, HEAD = `origin/feature/feedback-diagnose` = `b7e2daa`; `main` =
`origin/main` = `5735e5b` (geprüft per `git fetch` und `git rev-parse`).

**Grenzen dieser Arbeit:**
- **Nur Konzept.** Es gab keine Code-, System-, Firewall-, Konto- oder Schlüsseländerungen, keine Installation, keine
  VM, keine externen Verbindungen und keine Kosten. Die Produktivdatenbank wurde nicht geöffnet.
- Diese Datei ist neu (vorher geprüft: nicht vorhanden) und **nicht committet**. `AUDIT_S88.md` liegt weiterhin
  uncommittet vor.
- **Eine reale Sicherheitsabnahme hat nicht stattgefunden.** C-14 bleibt GESPERRT.

## 1 Bestandsanalyse

Belege von heute:
- `pytest tests/test_s87_isolation_gate.py tests/test_s86_isolation_design.py tests/test_gateway_hardening.py
  tests/test_gateway_tenant_scripts.py`: **78 bestanden, 7 übersprungen** (Tests der Zielumgebung).
- In §88 (heute): Isolationstests 103 bestanden und 7 übersprungen; Gateway-Tests 176 bestanden. Die volle Suite zuletzt
  in §87: 5626 bestanden, 4 bekannte fremde Logo-Fehler.

| Baustein | implementiert | automatisiert getestet | real geprüft | offen |
|---|---|---|---|---|
| I12-Gate (`scripts/legal_bench/isolation_gate.py`): Ed25519, Bindung an Lauf, Maschine, Politik, Firewall-Export, Endpunkt, DNS, Prüfer, V1–V13, ≤ 24 h | ja | ja (37 Tests) | nein | **kein Anker, keine Bestätigung**; Gate geschlossen |
| Schlüsselverwaltung für I12 | **nein** | – | – | kein Schlüssel; **kein Widerruf, keine Rotation, kein Wurzelschlüssel** (Code geprüft) |
| Beleg-Hashes in der Bestätigung | **nein** (nur „bestanden“ je Test) | – | – | Signatur deckt die Protokolle nicht ab |
| Integrität des Prüfwerkzeugs (Commit bzw. Hash der Abnahmetests) | **nein** | – | – | ein verändertes Testskript würde nicht erkannt |
| Abnahme-Proben in getrennten Prozessen, Regelprüfer (§86) | ja | ja (lokal, nur Loopback) | nein | Zielumgebung fehlt |
| VM-Abnahme V1–V13 (`isolation/vm_abnahme.json`) | Matrix | Konsistenz | **nein** | alle NICHT DURCHGEFÜHRT |
| Relay-Gateway (`gateway/`): Argon2id-gehashte Mandanten-Geheimnisse, Widerruf (`revoked_at`, `scripts/revoke_gateway_tenant.py`), Anthropic-Schlüssel nur serverseitig, fester Endpunkt `api.anthropic.com` | ja | ja (176 Tests) | Betrieb **NICHT VERIFIZIERT** (keine externe Verbindung) | Benchmark-Treiber sperrt `LEXONO_GATEWAY_URL` bewusst |
| Netz- und Prozesszaun, Endpunktsperre (§83–§85) | ja | ja | – | keine Sicherheitsgrenze gegen nativen Code (nachgewiesen) |
| Lenovo als Host (§88) | – | – | gelesen | Hyper-V vorhanden, aber deaktiviert; 59,9 GB frei; Konto in der Administratorengruppe, UAC |

## 2 Vertrauensmodell

**Grundsatz:** Wer ein System prüft, darf nicht allein durch dieses System prüfen. Weder eine Bestätigung, die Claude
erzeugt, noch ein Test, der **in** der VM läuft, ist ein unabhängiger Nachweis für die VM.

### 2.1 Nachweisklassen

Jeder Beleg erhält eine Klasse; sie wird automatisch aus seiner Herkunft vergeben, nicht von Hand.

| Klasse | Herkunft | Beispiel | Aussagekraft |
|---|---|---|---|
| **E0 Selbsttest** | Code bzw. Prozess innerhalb der geprüften Grenze (VM, Benchmark-Prozess) | pytest-Proben in der VM, Gate-Tests | belegt das Verhalten **aus Sicht des Geprüften**; allein **nie** ausreichend für Netzwerk-Tests |
| **E1 Host-Beobachtung** | Werkzeuge **außerhalb** der VM auf dem Host, mit Rechten, die die VM nicht hat | Export der Hyper-V-Port-ACLs, VM-Konfiguration, Kanarien-Listener **auf dem Host** mit Log | unabhängig von der VM; nicht unabhängig vom Owner bzw. Host |
| **E2 Fremdbeobachtung** | ein zweites System außerhalb des Hosts | Log des freigegebenen Endpunkts (Relay), optional ein Internet-Kanarienziel | unabhängig von Host und VM |

**Pflicht je Test** (automatisch erzwungen):
- **V1, V4–V8, V10, V12** (Netzwerkgrenze): mindestens **E1**.
- **V3** (Endpunkt erreichbar): E0 **und** E2, also Probe in der VM und Treffer im Log des Endpunkts.
- **V2, V9, V11, V13**: E0 genügt zusammen mit einem E1-Konfigurationsexport.

Fehlt die geforderte Klasse, lautet das Ergebnis **BLOCKIERT**, nie PASS.

### 2.2 Kanarienziele ohne Fremdinfrastruktur

Technisch entschieden: Kanarien-Listener laufen **auf dem Host**, an einer Adresse, die nur über den virtuellen Switch
erreichbar ist (IPv4 und IPv6, TCP und UDP, Port 53 eingeschlossen). Damit gilt:
- Ein Paket aus der VM muss die Port-ACL am VM-Adapter passieren, um sie zu erreichen. Kommt nichts an, belegt das die
  Sperre.
- Der Listener-Log entsteht außerhalb der VM (E1).
- Es entstehen keine Kosten und keine Internetverbindung.

Ein zusätzliches Internet-Kanarienziel (E2) ist **optional**. Die Port-ACL wirkt am Adapter unabhängig vom Ziel, deshalb
ist der Host-Kanarien-Nachweis für die Sperre hinreichend.

### 2.3 Schlüsselmodell (technisch entschieden)

| Entscheidung | Wahl | Begründung |
|---|---|---|
| Signaturverfahren | **Ed25519** (RFC 8032) über kanonisches JSON | bereits implementiert, Standard, deterministisch, keine Parameterwahl nötig |
| Hierarchie | **Wurzelschlüssel offline**, **Freigabeschlüssel online** | Gängiges Muster (z. B. TUF/Sigstore-Prinzip): Wird der Freigabeschlüssel kompromittiert, kann der Wurzelschlüssel ihn widerrufen und ersetzen, ohne dass der Vertrauensanker neu verteilt werden muss |
| Wurzelschlüssel | nur auf einem **verschlüsselten USB-Datenträger** (Datei mit Passphrase, scrypt), selten genutzt; zweite Kopie getrennt aufbewahrt | ein Ausfall des Rechners oder ein kompromittierter Host trifft ihn nicht |
| Freigabeschlüssel | Datei auf dem **Host** (nie in der VM, nie im Repository), mit Passphrase verschlüsselt (PKCS#8, scrypt); Gültigkeit ≤ 90 Tage | Passphrase-Abfrage beim Signieren = bewusste Handlung des Owners; Claude kennt die Passphrase nicht |
| Vertrauensanker in der VM | nur der **Fingerabdruck des Wurzelschlüssels** (im geprüften Code bzw. im Image, für das Benchmark-Konto schreibgeschützt); die Liste gültiger und widerrufener Freigabeschlüssel ist ein **vom Wurzelschlüssel signiertes** Dokument mit Versionsnummer und Ablauf | Manipulation des Ankers erfordert den Offline-Schlüssel; die Versionsnummer verhindert das Zurückspielen einer alten Liste |
| Widerruf | Eintrag in der signierten Schlüsselliste; das Gate lehnt widerrufene oder abgelaufene Schlüssel ab | ohne erreichbaren Dienst, offline prüfbar |
| Rotation | neue Freigabeschlüssel per neuer, höher versionierter Schlüsselliste; Bestätigungen gelten ohnehin ≤ 24 h | nichts muss migriert werden |
| Hardware-Token, TPM | **nicht** im ersten Schritt | TPM bzw. CNG kennen kein Ed25519; Token bräuchten ein anderes Signaturformat. Später optional |

### 2.4 Was Claude automatisieren kann – und was nicht

| Aufgabe | Claude automatisiert | Begründung |
|---|---|---|
| Gate- und Prüfcode schreiben, testen, reviewen lassen | ja | Software, durch Tests und Review prüfbar |
| Skripte für Host (Hyper-V, ACLs, Exporte, Kanarien) und VM (Golden Image, Proben) erstellen | ja | **Ausführung** mit erhöhten Rechten nur nach deiner Freigabe |
| Abnahmeläufe starten, Belege einsammeln, Hashes bilden, Bündel und Urteil erzeugen | ja | deterministisch, reproduzierbar |
| das **Urteil** (PASS/FAIL/BLOCKIERT/NICHT DURCHGEFÜHRT) berechnen | ja | nach festen Regeln (Abschnitt 4), nicht nach Ermessen |
| die **Bestätigung signieren** | **nein** | Die Signatur ist die Willenserklärung des Owners; der Schlüssel bzw. die Passphrase ist für Claude nicht zugänglich. Das Signierwerkzeug signiert nur ein Urteil „PASS“ |
| die Unabhängigkeit der Beobachtung herstellen | nein | Sie entsteht durch die **Grenze** (Host gegenüber VM, E1/E2), nicht durch Claude |

**Konsequenz:** Eine von Claude erzeugte Zusammenfassung ist ein **E0-Beleg der Werkzeugkette**, nie die Freigabe.

## 3 Automatisierte Abnahme – Ablauf

1. **Vorbedingungen** (automatisch): Repository-Stand sauber und gleich dem Commit des Prüfwerkzeugs; Netzwerkpolitik
   `FREIGEGEBEN`; Schlüsselliste gültig; VM aus dem Golden Image mit bekanntem Hash. Fehlt etwas: **BLOCKIERT**, Abbruch.
2. **Host-Teil** (E1, erhöht, von dir einmal freigegeben):
   - ACL- und VM-Konfiguration exportieren;
   - Kanarien-Listener starten;
   - Verkehrszähler am VM-Adapter sichern.
3. **VM-Teil** (E0): Abnahme-Proben aus `tests/test_s86_isolation_design.py` mit Manifest; Gate-Tests
   `tests/test_s87_isolation_gate.py`.
4. **Abschluss auf dem Host** (E1/E2):
   - Listener stoppen und Logs sichern;
   - ACL erneut exportieren (unverändert?);
   - Log des Endpunkts abrufen (E2, nur bei V3).
5. **Belegbündel:**
   - **Inhalt:** ein Manifest mit Datei, SHA-256, Klasse, Erzeuger, Zeitpunkt und V-Test je Beleg.
   - **Bindung:** Lauf-ID, Maschine, Politik-Hash, Commit und Hash des Prüfwerkzeugs, Golden-Image-Hash.
   - **Ablage:** unveränderbar archiviert, außerhalb der VM.
6. **Urteil** (automatisch, Abschnitt 4).
7. **Freigabe:** Das Signierwerkzeug auf dem Host zeigt Urteil und Bündel-Hash und signiert **nur** bei Urteil PASS nach
   Eingabe der Passphrase. Die Bestätigung enthält den Bündel-Hash und je Test Ergebnis, Klasse und Beleg-Hashes.
8. **Gate in der VM** prüft:
   - Signatur gegen eine gültige, nicht widerrufene Schlüsselliste mit gepinntem Wurzel-Fingerabdruck;
   - alle Bindungen;
   - Klassenpflichten je Test;
   - Frische.

   Jede Abweichung: **kein Start** (fail-closed).

## 4 Entscheidungslogik

| Ergebnis | Kriterium (objektiv) |
|---|---|
| **PASS** | Test unter den definierten Vorbedingungen ausgeführt; alle Pflichtbelege vorhanden, Hashes stimmen, geforderte Klasse erfüllt; Ergebnis entspricht exakt dem Soll; Belege jünger als 24 h |
| **FAIL** | ausgeführt, Belege gültig, aber Ergebnis weicht vom Soll ab. Beispiele: Kanarientreffer, zusätzliche Allow-Regel, erreichbares nicht freigegebenes Ziel, Gate startet ohne gültige Bestätigung, ACL nach dem Lauf verändert. Auch ein **uneindeutiges** Ergebnis gilt als FAIL, etwa ein Timeout beim freigegebenen Endpunkt oder ein Listener, der nicht lief |
| **BLOCKIERT** | Vorbedingung fehlt (VM, Schlüssel, Politik, Kanarien); Prüfwerkzeug nicht auf dem festgelegten Commit; geforderte Nachweisklasse fehlt; Beleg-Hash weicht ab |
| **NICHT DURCHGEFÜHRT** | nicht gestartet |

**Kritisch** (jedes Nicht-PASS verhindert die Freigabe vollständig und erzwingt nach der Behebung eine **vollständige**
Wiederholung von V1–V13): V1, V3–V8, V10, V12, V13. Die übrigen Tests (V2, V9, V11) sind ebenfalls Pflicht; ihr
Nicht-PASS verhindert die Freigabe ebenso, eine Wiederholung ist aber für den einzelnen Test zulässig. Eine Freigabe gibt
es **nur**, wenn alle 13 auf PASS stehen. Teilfreigaben gibt es nicht.

## 5 Notwendige Änderungen am Gate (I12 v2) – nicht umgesetzt

1. Schema v2: je Test `ergebnis`, `klasse`, `belege: [{datei, sha256}]`; dazu `buendel_sha256`, `pruefwerkzeug_commit`,
   `pruefwerkzeug_sha256`, `golden_image_sha256`. Version 1 wird im Betrieb abgelehnt.
2. Klassenpflichten je Test (Abschnitt 2.1) im Gate.
3. Schlüsselliste vom Wurzelschlüssel signiert, mit Version, Ablauf und Widerrufen; gepinnter Wurzel-Fingerabdruck; das
   Gate lehnt widerrufene oder abgelaufene Schlüssel sowie ältere Listenversionen ab.
4. Rolle „Owner“; die Texte „IT/Prüfstelle“ werden ersetzt, auch in `isolation/vm_abnahme.json`. Dort verlangt V3
   heute einen „Testdienst (kein Modell-Endpunkt)“. Für die Abnahme ruft V3 daher am Relay nur einen Prüfpfad auf, der
   ohne gültiges Mandanten-Geheimnis abgewiesen wird und nichts an Anthropic weiterleitet. Das Abweisen wird im Relay-Log
   als E2-Beleg festgehalten. Die Matrix wird entsprechend angepasst.
5. Ein Signierwerkzeug (Host), das nur bei Urteil PASS signiert; die Passphrase wird interaktiv abgefragt.
6. Ein Werkzeug für Urteil und Belegbündel, mit Tests für alle vier Ergebnisarten und Manipulationsfälle.

## 6 Roadmap bis zur VM-Abnahme und zur Entscheidung über C-14

| Phase | Schritt | Wer | Systemänderung | Abnahmekriterium |
|---|---|---|---|---|
| **A Code (lokal)** | A1 I12 v2 (Abschnitt 5) mit Negativtests | Claude | nein | alle Tests grün; Version-1-Bestätigungen abgelehnt |
| | A2 Werkzeug für Urteil und Belegbündel | Claude | nein | Tests für PASS, FAIL, BLOCKIERT, NICHT DURCHGEFÜHRT und Manipulation |
| | A3 Host- und VM-Skripte (Golden Image, ACL, Exporte, Kanarien) als **Trockenlauf** | Claude | nein | Skripte reviewt, Trockenlauf ohne Wirkung |
| **B Vorbereitung** | B1 Plattenplatz ≥ 60 GB, Sicherung der Produktiv-DB | du (Freigabe), Claude (Skript) | ja | Hash der Sicherung |
| | B2 Hyper-V aktivieren (Neustart) | du | **ja** | DB-Hash unverändert, Kanzlei-Instanz läuft |
| | B3 Wurzel- und Freigabeschlüssel erzeugen; Passphrase vergeben | du (Passphrase), Claude (Werkzeug) | Schlüssel | Fingerabdruck notiert; Wurzelschlüssel offline |
| **C Abnahme ohne Endpunkt** | C1 Golden Image, privater bzw. interner Switch, ACL „alles Deny“ | Claude-Skripte, Ausführung nach Freigabe | ja | Image-Hash, ACL-Export |
| | C2 V2, V4–V11, V13 mit Host-Kanarien (E1) | automatisiert | – | Urteil PASS je Test |
| **D Endpunkt** | D1 Endpunkt bestätigen (Empfehlung: eigener Relay, Abschnitt 7) und Politik freigeben | du (Bestätigung) | – | Politik `FREIGEGEBEN` |
| | D2 ACL um Relay-IP:443 erweitern; V1, V3 (E2-Log des Relays), V4–V8, V12 erneut | automatisiert | ja | alle 13 PASS |
| **E Freigabe** | E1 Urteil PASS → signieren (Passphrase) → Gate in der VM akzeptiert; Gegenprobe mit veralteter und fremder Bestätigung | du (Passphrase) | – | Gate akzeptiert nur diese Bestätigung |
| **F C-14** | F1 J1–J6, C-7, C-11 entscheiden | du (juristisch/organisatorisch) | – | Checkliste |
| | F2 gesondert reviewte Code-Änderung: Relay-Endpunkt laut Bestätigung im Treiber zulassen (statt Loopback); erster Lauf nur dev | Claude, Review | Code | Leitungsprüfung und volle Suite grün |

Wiederverwendet statt neu gebaut werden: der Relay (Authentifizierung, Widerruf, Argon2id, fester Anthropic-Endpunkt),
die Abnahme-Proben und der Regelprüfer (§86), das I12-Gate (wird erweitert, nicht ersetzt) sowie Treiber, Ablage und
Kostenbuch (§80/§81).

## 7 Entscheidungen

### 7.1 Technisch getroffen (keine Vorlage nötig)

- Ed25519 bleibt das Signaturverfahren; Wurzelschlüssel offline, Freigabeschlüssel mit Passphrase auf dem Host.
- Nachweisklassen E0, E1, E2 mit Pflichtklassen je Test; Kanarien auf dem Host.
- DNS: **kein DNS in der VM**; der Endpunkt wird fest eingetragen (geringste Angriffsfläche).
- Endpunkt: **eigener Lexono-Relay empfohlen**. Begründung: eine einzige, eigene, stabile Adresse statt wechselnder
  Anthropic-IPs; der Anthropic-Schlüssel verlässt nie die VM, weil er dort gar nicht existiert; Code, Tests, Widerruf
  und Rate-Limit sind vorhanden; der Relay liefert das E2-Log für V3.
- Gültigkeit: Bestätigung ≤ 24 h, Freigabeschlüssel ≤ 90 Tage.
- Freigabe nur, wenn alle 13 Tests PASS sind; keine Teilfreigaben.

### 7.2 Deine Entscheidungen (nicht automatisierbar)

| # | Entscheidung | Optionen | Risiken | Empfehlung |
|---|---|---|---|---|
| D1 | **Systemänderungen auf dem Lenovo** freigeben (Plattenplatz, Hyper-V, VM, Windows-Lizenz für die VM) | a) ja, auf dem Lenovo; b) separates Gerät | a) Produktivrechner wird Host (Neustart, Speicher); b) Kosten | **a**, mit Sicherung vorher und Zeitfenster ohne Kanzleibetrieb; technisch spricht nichts zwingend dagegen |
| D2 | **Alleinige Freigabe akzeptieren** (F-1): du signierst selbst; es gibt keine vier Augen | a) akzeptieren; b) zweite Person mit Wurzelschlüssel bzw. Gegenzeichnung | a) Fehlbewertung bleibt unentdeckt (abgemildert durch automatisches Urteil und E1/E2-Belege); b) Aufwand | **a**; dazu den Wurzelschlüssel getrennt aufbewahren |
| D3 | **Relay-Betrieb** bestätigen: läuft er, wer zahlt ihn, darf er Endpunkt des Benchmarks sein? | a) Relay; b) Anthropic direkt | a) Betrieb und Kosten des Servers; b) wechselnde IPs, Schlüssel müsste in die VM | **a** (Betrieb von hier aus **NICHT VERIFIZIERT**) |
| D4 | **Aufbewahrung der Passphrasen und des Offline-Datenträgers** | Passwortmanager plus Papier im Tresor bzw. getrennte Orte | Verlust: Neuaufbau der Schlüsselkette; Diebstahl: Widerruf über den Wurzelschlüssel | Passwortmanager für den Freigabeschlüssel; Wurzel-Passphrase und USB-Datenträger getrennt |

J1–J6, C-7 und C-11 bleiben davon getrennt offen.

## 8 Risiken

- **Gemeinsamer Host:** Host und Produktivrechner sind dasselbe Gerät; ein Fehler bei Hyper-V oder Neustart trifft den
  Kanzleibetrieb (D1).
- **E1 ist nicht vom Owner unabhängig:** Ein kompromittierter Host kann Belege fälschen. Abhilfe ist nur E2 bzw. ein
  separates Gerät. Das Restrisiko ist dokumentiert.
- **Port-ACLs:** Dass sie IPv6 und alle Protokolle wie geplant sperren, ist **NICHT VERIFIZIERT**; das belegen C2/D2.
- **Prüfwerkzeug:** Seine Integrität hängt am festgeschriebenen Commit; Änderungen daran müssen reviewt werden.
- **Schlüssel:** Verlust des Wurzelschlüssels bedeutet Neuaufbau; Diebstahl des Freigabeschlüssels bedeutet Widerruf
  über den Wurzelschlüssel.
- **Ressourcen:** Plattenplatz knapp (59,9 GB); RAM wird mit der Kanzlei-Instanz geteilt.

## Ergebnisübersicht

| Punkt | Ergebnis |
|---|---|
| Git-Stand verifiziert | **PASS** |
| Software-Tests Gate/Isolation/Gateway (heute) | **PASS** (78 bestanden, 7 übersprungen) |
| Schlüsselverwaltung, Widerruf, Rotation, Beleg-Hashes, Integrität des Prüfwerkzeugs | **NICHT DURCHGEFÜHRT** (Konzept; Code-Änderung A1/A2) |
| V1–V13 real | **NICHT DURCHGEFÜHRT** (VM fehlt) bzw. **BLOCKIERT** bis D1 |
| reale Sicherheitsabnahme | **NICHT DURCHGEFÜHRT** |
