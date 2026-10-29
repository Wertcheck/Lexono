# Kontrollierter E2E-Lauf der installierten EXE gegen einen lokalen Aufzeichnungsserver (26.10.2026)

**Das ist kein Test gegen die echte Cloud.**

## Ergebnis der Prüfung „sichere Testkonfiguration mit funktionierendem Cloud-Endpunkt“

- Es gibt **keine** getrennte Test-Konfiguration für die echte Cloud (kein Test-Gateway, kein Test-Schlüssel).
- Funktionierende Zugangsdaten liegen nur in der produktiven Konfiguration (`C:\ProgramData\Lexono\.env`) und in der
  Entwickler-`.env` des Repositorys. Beide wurden **nicht** verwendet: Es gilt die Vorgabe, Zugangsdaten nie in Tests
  zu verwenden. Ausgelesen wurden nur Schlüsselnamen, keine Werte.
- Bisherige Nachweise (PRIVACY_REVIEW_ARCHITECTURE.md) liefen ebenfalls gegen einen lokalen Aufzeichnungsserver mit
  Testschlüssel; dort heißt es „echter Cloud-E2E-Test: nein“.
- **Ein erfolgreicher Cloud-E2E-Test wird daher nicht behauptet.**

## Was stattdessen geprüft wurde

Die gesamte Kette bis zur fertigen Antwort, mit einem lokalen Endpunkt statt der Cloud.

**Konfiguration:**

- **Programm:** installierte `Lexono.exe` (`97f8deb6…`), `serve --no-window`, isolierte Ablage, frisches Testkonto.
- **Cloud-Ziel:** `ANTHROPIC_BASE_URL=http://127.0.0.1:8765` (lokaler Recorder, `qa_recorder.py` im Job-Ordner) mit
  `LEXONO_QA_LOOPBACK_CLOUD_ENDPOINT=1`. Das ist der im Code vorgesehene Prüfweg (`endpoint_policy.py`); jedes andere Ziel
  verweigert die App.
- **Schlüssel:** ungültiger Testschlüssel `sk-ant-qa-invalid-…`, Gateway-Pfad aus.
- **Netz:** Proxy für alles andere auf einen toten Port.
- **Daten:** ausschließlich synthetisch. Die produktive DB war vorher und nachher byte-gleich (`b9b34d30…`).

| Schritt | Ergebnis |
|---|---|
| 1. Upload PDF + Auftrag „Schriftsatz erstellen“ | Dokument-Gate blockiert; **0** Anfragen am Recorder |
| 2. Prüfansicht | Status „review“, Rückfrage zu „Richter“, 6 Funde, 2 Seiten |
| 3. Bestätigen mit Entscheidung „personal“ (Server prüft erneut) | Freigabe, Wiederaufnahme, Antwort erzeugt (`message_id` gesetzt) |
| 4. Cloud-Aufruf | genau **1** Anfrage, nur an `/v1/messages`, nur mit Testschlüssel |
| 5. Payload-Prüfung (tatsächlich gesendeter HTTP-Body) | **0 Lecks** über alle 8 synthetischen Originalwerte (Thomas Berger, Berger, Beispielstrasse 17, 10115 Berlin, 12/345/67890, Nordbauholding, Richter, Sabine Hartmann); nur Platzhalter `[PERSON_01]`, `[PERSON_02]`, `[ADRESSE_01]`, `[KENNUNG_01]`, `[ORGANISATION_01]`, `[ORT_01]` |
| 6. Fertige Antwort im Chat | Antwort des Recorders als Schriftsatz sichtbar; der Platzhalter wurde lokal zu „Thomas Berger“ zurückgeführt; kein Platzhalter im Chat |

Im nativen Fenster ist diese Antwort zusätzlich als Schriftsatz zu sehen:
`../native-desktop-6e4db30-20261026/quellstand-korrekturen/01-quellstand-chat-maximiert-ohne-querscroll.png`.

Messwerte: `e2e_loopback.json`.

Der Aufzeichnungsbericht des Recorders (`recorder/requests.jsonl`) enthält nur pseudonymisierte Texte und den
Testschlüssel-Präfix. Er wird nicht eingecheckt, die Auswertung steht in `e2e_loopback.json`.

## Einschränkungen

- Antwortqualität, Latenz und Verhalten der echten Cloud (Fehler, Ratenbegrenzung, Gateway-Relay) sind nicht geprüft.
- Die Antwort ist synthetisch und belegt nur Weg und Rückführung, nicht den Inhalt.
- Keine Datenschutz-, Pilot-, P0-08- oder C-14-Freigabe.
