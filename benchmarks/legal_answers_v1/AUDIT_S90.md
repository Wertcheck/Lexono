# §90 Phase A – Owner-Gate und automatisierte Sicherheitsbewertung

Stand 19.10.2026. Branch `feature/feedback-diagnose`, HEAD `b7e2daa` (unverändert, **nicht committet**); `main` = `5735e5b`.

**Grenzen dieser Arbeit:**
- Nur lokale Code-, Test- und Dokumentationsänderungen. Es gab keine Installation, keine VM, keine System-, Firewall-,
  Netzwerk-, Konto- oder Schlüsseländerung, keine externe Verbindung und keine Kosten.
- Die Produktivdatenbank wurde nicht geöffnet. Die vier fremden Logo-/UX-Dateien sind unverändert.
- **Eine reale Sicherheitsabnahme hat nicht stattgefunden:** V1–V13 bleiben NICHT DURCHGEFÜHRT, C-14 bleibt GESPERRT.
- Das Produktiv-Gate ist weiterhin **geschlossen**: Es gibt keinen Wurzelschlüssel, keine Schlüsselliste, keine Belege
  und keine Bestätigung; die Netzwerkpolitik steht auf ENTWURF.

## 1 Bestandsanalyse (vor der Änderung verifiziert)

- **I12 v1** (`scripts/legal_bench/isolation_gate.py`, §87):
  - Ed25519 über kanonisches JSON; Anker-Datei schreibgeschützt.
  - Bindung an Lauf, Maschine, Politik, Firewall-Export-Hash, Endpunkt, DNS-Modus und Prüfer.
  - Die Abnahme bestand nur aus der Liste `abnahme: {Vn: "bestanden"}`. Das war eine **reine Markierung ohne Belege**.
  - Es gab keinen Widerruf, keine Rotation und keinen Wurzelschlüssel.
- Der Treiber ruft das Gate vor dem Start (`require`) und vor jedem Aufruf (`problems`, ohne Firewall-Prozess) auf.
  Das bleibt unverändert.
- Das Relay-Gateway hat einen eigenen Widerruf (`revoked_at`, Argon2id). Er betrifft Mandanten-Geheimnisse, nicht die
  Isolation; wiederverwendet wurde dort nichts, weil es um einen anderen Gegenstand geht.

## 2 Architekturänderungen

| Datei | Art | Inhalt |
|---|---|---|
| `scripts/legal_bench/owner_keys.py` | neu | Wurzel-Pin `PINNED_ROOT_FINGERPRINTS = ()` (leer → Gate zu); `MIN_KEYLIST_VERSION`; signierte Schlüsselliste (Version, ≤ 90 Tage, Widerrufe, nur Rolle „Owner“, Schlüssel ≤ 90 Tage, Wurzel ≠ Freigabeschlüssel, Widersprüche verwerfen die ganze Liste); verschlüsselte private Schlüsseldateien |
| `scripts/legal_bench/isolation_evidence.py` | neu | Belegbündel (Manifest mit SHA-256, Test, Art, Erzeuger, Klasse, Zeitpunkt), Pflichtbelege je Test (`ANFORDERUNGEN`), automatisches Urteil; Regelprüfer `check_rules` aus §86 hierher verlagert (keine Kopie) und gegen Hostnamen gehärtet |
| `scripts/legal_bench/owner_sign.py` | neu | Werkzeug des Owners auf dem Host: Schlüssel erzeugen, Schlüsselliste signieren, Urteil anzeigen, Bestätigung signieren – **nur bei PASS aller 13 Tests** |
| `scripts/legal_bench/isolation_gate.py` | geändert | Schema v2; Schema v1 wird abgelehnt; prüft die Schlüsselliste bei jedem Aufruf (Widerruf wirkt sofort); rechnet das Urteil aus den Belegen nach; bindet Bündel-Hash, Schlüsselliste und Prüfwerkzeug; jeder unerwartete Fehler gilt als geschlossen |
| `benchmarks/legal_answers_v1/isolation/vm_abnahme.json` | geändert | Rollen „IT“ ersetzt durch „Owner“ mit Nachweisklasse, V3 ohne Modellaufruf, V12 als Host-Auswertung; Status unverändert NICHT DURCHGEFÜHRT |
| `tests/isolation_test_support.py`, `tests/test_s87_isolation_gate.py`, `tests/test_s86_isolation_design.py` | angepasst | v2-Testgates; s86 nutzt den verlagerten Regelprüfer |
| `tests/test_s90_owner_gate.py` | neu | 82 Tests |

### 2.1 Bestätigung v2 – gebundene Felder

Die Bestätigung bindet:
- Lauf-ID, Maschine (Hostname + MachineGuid);
- SHA-256 der Netzwerkpolitik, Endpunkte, DNS-Modus;
- `firewall_export_sha256`, der dem Beleg V1 entsprechen muss;
- Prüfer: Name gleich Inhaber, Rolle „Owner“;
- `schluesselliste {version, sha256}`;
- `pruefwerkzeug {commit, sha256}`: SHA-256 über die Gate-, Beleg- und Signierdateien, die Abnahmetests und die Matrix,
  mit normalisierten Zeilenenden;
- `buendel_sha256`, den SHA-256 des Manifests – damit ist jede Belegdatei mittelbar signiert;
- `urteil {gesamt, tests}`;
- Gültigkeit von/bis, höchstens 24 h.

Das Gate verlangt außerdem, dass die Bestätigung **innerhalb der Gültigkeit des Schlüssels** ausgestellt wurde.

### 2.2 Urteilslogik (automatisch, aus Belegen)

| Ergebnis | Kriterium |
|---|---|
| NICHT DURCHGEFÜHRT | Es gibt keinen einzigen Beleg für den Test, oder es fehlt das ganze Bündel. |
| BLOCKIERT | Ein Pflichtbeleg oder die geforderte Nachweisklasse fehlt (z. B. nur ein E0-Selbsttest statt einer E1-Host-Beobachtung), oder das Bündel ist ungültig. Ungültig heißt: Hash weicht ab (manipuliert), Datei fehlt, unlesbar, nicht im Manifest geführt, liegt außerhalb des Bündels, doppelt, Klasse widerspricht dem Erzeuger, Inhalt widerspricht dem Manifest, Beleg älter als 24 h oder aus der Zukunft, anderer Lauf, andere Maschine, andere Politik oder anderes Werkzeug, ungültige Feldtypen. |
| FAIL | Ein Kriterium ist verletzt, **oder es ist uneindeutig**: Kanarientreffer, Listener lief nicht über den ganzen Prüfzeitraum, Kanarienziele decken IPv6/UDP/DNS nicht ab, pytest fehlgeschlagen, übersprungen oder fehlend, Kriterium `false` oder nicht ausdrücklich `true`, Firewall-Export mit zusätzlicher Allow-Regel oder Standard „Zulassen“. |
| PASS | Alle Pflichtbelege sind vorhanden und gültig, in der geforderten Klasse, und alle Kriterien sind erfüllt. |

- **Gesamturteil:** PASS nur, wenn alle 13 Tests PASS sind; sonst gilt der schwerste Befund (FAIL vor BLOCKIERT vor
  NICHT DURCHGEFÜHRT). Eine leere oder unbekannte Statusliste ergibt BLOCKIERT.
- **Manuelle Markierung:** Behauptet eine signierte Bestätigung PASS, ohne dass die Belege das tragen, lehnt das Gate ab
  („Urteil widerspricht den Belegen“ bzw. „nicht vollständig PASS“).
- **Nachweisklassen:** Netzwerk-Tests (V1, V4–V8, V10, V12) verlangen mindestens E1; V3 verlangt E2.

### 2.3 Schlüssel, Widerruf, Rotation

**Vorher existierte nichts davon** (Code geprüft). Jetzt:
- **Wurzelschlüssel** (offline): signiert nur die Schlüsselliste. Sein Fingerabdruck kommt per **reviewter
  Code-Änderung** in `PINNED_ROOT_FINGERPRINTS`.
- **Widerruf:** Eintrag in der Liste. Das Gate lehnt den Schlüssel ab, auch mit gültiger Signatur und neuer Bindung,
  und zwar beim nächsten Aufruf, also auch mitten im Lauf.
- **Rotation:** neue Liste mit höherer Version; Überlappung mehrerer aktiver Schlüssel ist erlaubt. Bestätigungen, die
  an die alte Liste gebunden sind, werden abgelehnt.
- **Rückspielen alter Listen:** begrenzt durch die Listenlaufzeit (≤ 90 Tage) und hart durch `MIN_KEYLIST_VERSION`,
  per Review anzuheben.
- **Private Schlüssel:**
  - Verschlüsselung: scrypt (RFC 7914, N = 2^17, r = 8, p = 1) → AES-256-GCM; Zweck und Schlüssel-ID sind
    authentifiziert.
  - Passphrase mindestens 15 Zeichen, nur über `getpass`. Es gibt kein CLI-Argument und keine Umgebungsvariable dafür;
    auch eine Ausgabe der Passphrase gibt es nicht.
  - Die Datei darf nicht im Repository liegen und wird nie überschrieben.
  - Das Signieren wird verweigert, wenn die signierende Maschine die geprüfte Maschine ist.
- **Abweichung von §89 (begründet):**
  - PKCS#8 mit `BestAvailableEncryption` nutzt in `cryptography` 48 nur 2048 PBKDF2-Runden (gemessen). Das ist zu
    schwach.
  - Das OpenSSH-Format mit bcrypt bräuchte das Modul `bcrypt`, und Installationen sind ausgeschlossen.
  - Deshalb ein eigenes Container-Format mit etablierten Primitiven aus derselben Bibliothek.
- **Im Code, in Tests und Logs** gibt es keinen Schlüssel und keine Passphrase. Testschlüssel entstehen zufällig im
  Speicher bzw. in `tmp_path`; ein Test prüft, dass `scripts/`, `benchmarks/`, `app/` und `gateway/` keine
  Schlüsseldatei enthalten.

## 3 Tests und Ergebnisse

| Prüfung | Ergebnis |
|---|---|
| `tests/test_s90_owner_gate.py` (82): Schlüsselliste (12 Fehlerfälle, fremde Wurzel, Manipulation, doppelte ID, Wurzel als Freigabeschlüssel, Rückspielen), Widerruf, Rotation, Signatur durch die Wurzel bzw. unbekannten Schlüssel, Urteil je Test (14 Fälle), ungültiges Bündel (11 Fälle), unerwartete Typen, Manipulation nach dem Signieren, nachgezogenes Manifest, fehlendes Bündel, manuelle PASS-Markierung, geändertes Werkzeug, Signierwerkzeug (Verweigerungen, widerrufener Schlüssel), verschlüsselte Schlüsseldatei, CLI ohne Passphrasen-Argument, Produktivpfad nur mit gepinnter Wurzel und schreibgeschütztem Anker, **Treiber bricht vor jedem Provider-Aufruf ab** (5 Fälle sowie Belegänderung bzw. Widerruf mitten im Lauf → kein zweiter Aufruf) | **PASS** |
| `tests/test_s87_isolation_gate.py` (40, auf v2 umgestellt, darunter Schema v1 abgelehnt) und `tests/test_s86_isolation_design.py` | **PASS** (138 bestanden, 7 übersprungen = Zielumgebung) |
| Benchmark-Tests §77–§85 (Treiber, Härtung, Pilot, Zaun, Grenzen, Freigabe) | **PASS** (452 bestanden zusammen mit §86/§87/§90 vor dem letzten Zusatztest) |
| volle Suite in 6 Teilen | **5626 bestanden, 4 FAIL** – die bekannten fremden Logo-Tests (`tests/test_design_refresh.py`, fehlende Logo-Dateien), nicht behoben |
| Echte Provider-Aufrufe, externe Verbindungen | keine (nur Loopback; TEST-NET-Adressen nur als Daten) |

Gefundener und behobener Fehler während der Entwicklung: Kriterien eines Tests, die auf E0- und E1-Protokolle verteilt
sind, wurden zunächst einzeln bewertet; das führte zu FAIL. Jetzt werden sie zusammengeführt: ein `false` irgendwo ist
eine Verletzung, und jedes Pflichtkriterium muss mindestens einmal ausdrücklich `true` sein.

## 4 Restrisiken (ehrlich)

1. **Herkunft der Belege:** Die Klasse E1/E2 folgt aus dem im Manifest genannten Erzeuger. Software kann nicht beweisen,
   dass eine Datei wirklich vom Host stammt. Diese Zusicherung macht der Owner mit seiner Signatur auf dem Host. Ein
   kompromittierter Host kann Belege fälschen; das bleibt ein Restrisiko (AUDIT_S89 §8).
2. **Alleinfreigabe (F-1):** Ein einziger Owner signiert, es gibt keine vier Augen. Abgemildert ist das durch das
   automatische Urteil und die Pflichtklassen, aufgehoben ist es nicht.
3. **Gate läuft in der VM:** Wer in der VM Code oder Anker ändern kann, umgeht das Gate. Dagegen helfen nur ein
   schreibgeschütztes Golden Image und ein Konto ohne Administratorrechte (V2, V11, V13). Der Fingerabdruck des
   Prüfwerkzeugs erkennt veränderte Gate- und Testdateien, aber nicht den übrigen Treiber-Code; den prüft der
   Commit-Abgleich in `answer_run`.
4. **Rückspielen alter Listen:** Innerhalb der Listenlaufzeit ist das ohne Anhebung von `MIN_KEYLIST_VERSION` möglich.
   Nach jedem Widerruf muss diese Konstante angehoben werden.
5. **Maschinenvergleich beim Signieren:** Hostname und MachineGuid sind fälschbar. Der Vergleich ist nur ein Riegel
   gegen Bedienfehler; die echte Grenze ist, dass Schlüssel und Passphrase nie in die VM gelangen.
6. **Belegalter:** Belege dürfen beim Signieren bis zu 24 h alt sein, die Bestätigung gilt weitere bis zu 24 h. Ein
   Beleg kann bei der Nutzung also bis zu 48 h alt sein.
7. **Kriterien-IDs** für V2, V9, V11 und V13 sind technisch festgelegt (`ANFORDERUNGEN`) und reviewbar; ob sie das Soll
   vollständig abbilden, zeigt erst die reale Durchführung.
8. **Kein Hardware-Token:** Der Schlüssel ist eine passphrasengeschützte Datei (Variante A aus §88).

## 5 Offene Voraussetzungen (BLOCKIERT bzw. NICHT DURCHGEFÜHRT)

| Voraussetzung | Status | Wer |
|---|---|---|
| Wurzel- und Freigabeschlüssel erzeugen (`owner_sign schluessel-erzeugen`), Wurzel offline aufbewahren | NICHT DURCHGEFÜHRT | du (Passphrase); braucht eine Entscheidung über den Aufbewahrungsort (D4 aus §89) |
| Fingerabdruck in `PINNED_ROOT_FINGERPRINTS` eintragen (reviewte Code-Änderung) | BLOCKIERT bis zum vorigen Schritt | Claude nach deiner Freigabe |
| Netzwerkpolitik freigeben (Endpunkt, DNS) | BLOCKIERT (D3: Relay-Betrieb) | du |
| Host-/VM-Skripte als Trockenlauf, die die Belege im Bündelformat erzeugen (Roadmap A3) | NICHT DURCHGEFÜHRT | Claude |
| Hyper-V, VM, Golden Image, reale Durchführung von V1–V13 | BLOCKIERT (D1: Systemänderungen) | du (Freigabe) |
| C-14 | GESPERRT | – |

## 6 Ergebnisübersicht

| Punkt | Ergebnis |
|---|---|
| Owner-Gate v2 (Signatur, Bindungen, fail-closed, v1 abgelehnt) | **PASS** (lokal, automatisiert getestet) |
| Belegprüfung (Hash-Bindung, Manipulation, fehlend, veraltet, widersprüchlich) | **PASS** (lokal) |
| Urteilslogik PASS/FAIL/BLOCKIERT/NICHT DURCHGEFÜHRT | **PASS** (lokal) |
| Widerruf, Rotation, Schutz gegen Rückspielen, verschlüsselte Schlüssel | **PASS** (lokal) |
| Abbruch vor jedem Provider-Aufruf | **PASS** (lokal) |
| volle Suite | **PASS** bis auf **4 FAIL** (bekannte fremde Logo-Tests) |
| echte Schlüssel, Schlüsselliste, Bestätigung | **NICHT DURCHGEFÜHRT** |
| Produktiv-Gate | geschlossen (**BLOCKIERT**, wie beabsichtigt) |
| VM-Abnahme V1–V13 real | **NICHT DURCHGEFÜHRT** |
| reale Sicherheitsabnahme | **NICHT DURCHGEFÜHRT** |
