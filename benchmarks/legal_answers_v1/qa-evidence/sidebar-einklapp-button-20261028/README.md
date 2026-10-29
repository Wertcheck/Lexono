# Sidebar-Einklapp-Button höher (28.10.2026)

**Änderung:** Nur eine Regel: `.sidebar-edge-toggle { top: 8px → 2px }` in `app/web/static/css/app.css`.

- Die horizontale Position bleibt: mittig auf der rechten Sidebar-Kante (`left: var(--sidebar-width)` bzw. 68 px
  eingeklappt).
- Größe (22×22 px), Farben, Schatten, Rundung, Icon und Klickfunktion bleiben unverändert.
- **Höher geht nicht:** `.app-shell` beginnt unter der Kopfzeile und schneidet per `overflow: hidden` ab. Mit 2 px
  liegt der Button direkt unter der Kopfzeilenlinie.

**Messung:** Quellstand, headless Edge, DPR 1, isolierte Instanz; Messwerte in `*/messwerte_*.json`.

| Größe | Zustand | Oberkante vorher → nachher | Abstand zur Kopfzeile | überdeckt „Neuen Chat starten“ / Navigation | auf Sidebar-Kante |
|---|---|---|---|---|---|
| 1920×1080, 1280×720, 960×600 | ausgeklappt | 72 → 66 px | 8 → 2 px | nein / nein | ja |
| 1920×1080, 1280×720, 960×600 | eingeklappt | 72 → 66 px | 8 → 2 px | nein / nein | ja |

**Aufnahmen:**

- `nachher/*-ausschnitt.png`: oberer linker Bereich in 2-facher Vergrößerung;
- `nachher/*-fenster.png`: ganzes Fenster;
- `vorher/`: Ausschnitte vor der Änderung.

**Tests:** `test_design_refresh.py` prüft die Position jetzt mit `top: 2px`. Mit den Layout-Tests für Chat-Start,
Kanzleiwissen und Posteingang: 66 bestanden, 4 fehlgeschlagen. Die 4 Fehler sind ausschließlich die bekannten
Logo-Tests (Logos im Arbeitsbaum fremd gelöscht, bewusst nicht wiederhergestellt).

**Nicht installiert:** Die Desktop-App enthält die Änderung erst nach Neubau und Installation.
