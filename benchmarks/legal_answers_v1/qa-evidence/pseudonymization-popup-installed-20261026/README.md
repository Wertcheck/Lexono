# Zentrales Pseudonymisierungs-Pop-up – Prüfung der INSTALLIERTEN Anwendung (26.10.2026)

Getestet wurde mit synthetischen Dokumenten in einer isolierten Instanz. **Das ist kein Cloud-E2E-Test.**

## Build und Installation

- **Quellstand:** Commit `4306389`, gepusht. Gebaut mit `windows\build.ps1`.
- **Installer:** `dist/installer/Lexono_Setup.exe`, gebaut 26.10.2026 00:16:57, 556.672.304 Bytes.
  SHA-256 `a6957396c4153f0b69f29e08ea25db5529f7836577e307962345425c99068f78`.
- **EXE:** gebaut 00:11:59, 35.092.224 Bytes.
  SHA-256 `38115ebc10f19ddb046bece0865c2cb0f6d8a4e4594d6fcc97690a40eb5e188f`.
- **Inhaltsprüfung:**
  - 304 `app.*`-Module sind bytecode-gleich mit HEAD, darunter `app.documents.docx_layout`.
  - 140 Template-, Static- und Migrationsdateien sind byte-gleich.
- **Installation:** still installiert, Start 00:17:52, „Installation process succeeded“ um 00:19:02.
  - Protokoll: `C:\Users\Bonit\Lexono_backup_20261025_pre_install_00d5f8a\install_4306389.log`.
  - Installiert ist jetzt `%LOCALAPPDATA%\Lexono\Lexono.exe` mit SHA-256 `38115ebc…`.
- **Migrationen:** keine. Das Schema bleibt `schritt3_037`.
- **Echte Datenbank:** vor und nach Installation und Test unverändert. SHA-256 `B9B34D30…`, letzte Änderung
  25.10.2026 22:54:27 durch die App des Nutzers.
- **Frische Sicherung danach:** `C:\Users\Bonit\Lexono_backup_20261026_post_install_4306389\`. 907 Dateien
  identisch, `quick_check` ok.

## Testinstanz

- Die **installierte** EXE.
- `LEXONO_DATA_DIR` im temporären Job-Ordner, frische Datenbank, Modus `auto_review`.
- Port 8062, Cloud-Endpunkt `127.0.0.1:9` (tot).
- Im Serverprotokoll: 0 ERROR-Zeilen, kein Traceback.

## Abnahme (Screenshots in `ablauf/` und `randfaelle/`)

| Nr. | Prüfung | Ergebnis |
|---|---|---|
| A | Chat ohne Dialog | Chat unverändert; im Sperrhinweis „Pseudonymisierung prüfen“; **keine Dokumentspalte** |
| B | Pop-up mit markiertem PDF | 3 Seiten, Markierungen an der Fundstelle, 2 Rückfragen, Dokument-Tabs mit Badges |
| C | Originalansicht | dieselben Seiten ohne Markierungen |
| D | Pseudonymisiertes Ergebnis | Platzhaltertext |
| E | Rückfrage mit Vorschau | Rückfrage und Dokumentvorschau im selben Pop-up, Sprung zur Fundstelle |
| F/G | Mehrere Dokumente, DOCX mit Tabelle | Wechsel PDF↔DOCX; DOCX mit Kopfzeile, Tabellenrahmen und Markierungen in den Zellen; Entscheidungen bleiben beim Wechsel erhalten |
| H | Offene Rückfrage | Bestätigen gesperrt |
| I | Alle Rückfragen beantwortet | Bestätigen aktiv |
| J | Abbrechen | zurück im Chat; 0 Entscheidungen und 0 Freigaben gespeichert (vorher 0/0) |
| K | Zoom und Seitenwechsel | Zoom 140 %, Seite 2 / 3 |
| M | Bestätigen | Rückkehr in den Chat; danach technische Meldung, weil der Cloud-Endpunkt tot ist – **kein E2E** |
| L–L4 | Chat-Rückfrage beim Senden („Ruecksicht“) | im SELBEN Pop-up mit Dokumentvorschau, gekennzeichnet „aus Ihrer Nachricht im Chat“; Abbrechen → Blase „Rückfrage öffnen“ → erneut öffnen → beantworten → fortgesetzt |
| N/N2 | 70 Seiten, 280 Markierungen | in 8,5 s geprüft, Seite 65 / 70 erreichbar |
| O | Dokument ohne Treffer, ohne Auftrag | reine Prüfansicht ohne Bestätigen |
| P | Fenster 1280×720 | Dialog und Fußzeile vollständig sichtbar |

Diese Prüfung ist keine Datenschutz-, Pilot-, P0-08- oder C-14-Freigabe. Die Leckfreiheit ist nicht belegt.
