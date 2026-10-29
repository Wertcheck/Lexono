# App-Test Build 88a13ac (25.10.2026) – synthetisch, isoliert, KEIN Cloud-E2E

- **Quelle:** Commit `88a13ac` (Branch `feature/feedback-diagnose`, gepusht). Gebaut mit `windows\build.ps1`
  (PyInstaller 6.22.2, Inno Setup 6.7.3).
- **EXE:** `dist/Lexono/Lexono.exe`
  - gebaut 25.10.2026 15:43:01;
  - 35.069.537 Bytes;
  - SHA-256 `e56373a86019c4c35d3f5867d8a0d783123791aec6f5c470ea8e8e3ac6d65e05`.
- **Installer:** `dist/installer/Lexono_Setup.exe`
  - gebaut 15:47:57;
  - 556.681.157 Bytes;
  - SHA-256 `6aae32ce9ba1be7cc98752b00a72d15375933eb8debc5e131a1f007b6186b4c0`.
- **Inhaltsprüfung:**
  - Alle 302 `app.*`-Module im PYZ der EXE sind bytecode-gleich mit `HEAD:88a13ac`. Konstanten vom Typ frozenset wurden
    reihenfolgeunabhängig verglichen.
  - 138 Template-, Static- und Migrationsdateien sind byte-gleich.
  - Grenzen der Methode: Abhängigkeiten aus der venv wurden nicht geprüft, Zeilennummern nicht verglichen.
- **Testinstanz:**
  - die nicht installierte `dist`-EXE;
  - `LEXONO_DATA_DIR` im temporären Job-Ordner, frische Datenbank per `migrate`, Wegwerf-Admin;
  - Port 8062, Cloud-Endpunkt `127.0.0.1:9` (vorher als abgewiesen geprüft), lokale KI aus.
  - Die installierte Version (`DEB6FA9B…`) und die Produktiv-Datenbank sind unverändert; deren SHA-256-Präfix ist vor
    und nach dem Test `20E78ADC1E3DA494`.
- **Ergebnisse:** siehe `inst_check3.json`, Screenshots `01`–`08`. Die Screenshots sind echte Aufnahmen; angefragte und
  angezeigte URL stehen im JSON. `01-login-maske` zeigt nach der Weiterleitung `/dashboard/chat` und keine Anmeldemaske.

| Fall | Beobachtet | Bewertung |
|---|---|---|
| Fachbegriffe (Rohergebnis, Steuermessbetrag, Antrag, Wareneinsatz, Gewerbeertrag, Umsatzerloese) | lesbar | wie erwartet |
| „§ 1 StVO“ | lesbar | wie erwartet |
| Thomas Berger, Nordbauholding GmbH, Kurzform NBH, Steuernummer 12/345/67890, Anschrift | geschützt | wie erwartet |
| Mehrdeutiger Name „Richter“ („Schreiben von Richter“) | Rückfrage, Dialog geöffnet | wie erwartet |
| Chat: „hat die Kaufmann …“ | Rückfrage „Kaufmann“, nichts gesendet | Korrektur vom 25.10. wirksam |
| Chat: „Unsere Mandantin, die Wendebrink Logistik GmbH …“ | Sendeversuch an den toten Endpunkt; lokal gegengeprüft: Firma als `[ORGANISATION_01]` | Korrektur wirksam |
| Chat: „mit Ruecksicht auf die laufende Frist“ | **Rückfrage „Ruecksicht“** (Fehlalarm, kein Leck) | **offen:** Die Umschrift-Korrektur wirkt nur auf dem NER-Pfad; die Rückfrage-Erkennung fragt in anderen Sätzen weiter. Am Quellcode reproduziert. |
| Chat ohne Akte: „Die Bremer Grundbesitz GmbH (BGB) … § 823 BGB … Wie gehen wir vor?“ | **keine Kollisionssperre**; Sendeversuch, Firma lokal gegengeprüft im Klartext | **offen:** Im Allgemeinwissen-Modus wird eine Firma ohne Mandatssignal bewusst nicht maskiert, daher greift die Kollisionsprüfung nicht. „Wie gehen wir vor?“ gilt nicht als Mandatssignal (bekannte Grenze „Mandatsumschreibung ohne Signalwort“). Im Akten- und Schriftsatzpfad greift die Sperre (`tests/test_web_short_form_collision.py`). |
| Antwort nach der Rückfrage | „Die Antwort konnte aus technischen Gründen nicht erzeugt werden“ (toter Endpunkt) | **kein E2E-Erfolg**, keine KI-Antwort |

**Keine Datenschutz-, Pilot-, P0-08- oder C-14-Freigabe. Keine Leckfreiheit. Kein echter Cloud-E2E-Test. Die
VM-Isolationstests V1–V13 sind offen.**
