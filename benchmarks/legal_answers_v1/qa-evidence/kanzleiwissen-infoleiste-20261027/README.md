# Kanzleiwissen – rechte Informationsleiste ohne Scrollen (27.10.2026)

Alle Aufnahmen stammen aus der **laufenden Anwendung**:

- **Quelle:** Quellstand, `python run.py serve --no-window`.
- **Testumgebung:** isolierte QA-Ablage mit ausschließlich synthetischen Inhalten, ohne Netzwerkverbindungen.
- **Aufnahme:** headless Edge per CDP mit exakten Viewports (DPR 1).
- **Messwerte:** Die Messung der Karte (`karte` in `*/messwerte_*.json`) stammt aus dem DOM:
  - `scrollHeight` gegenüber `clientHeight`;
  - `overflow-y`;
  - Unterkante gegenüber Tabelle und Viewport;
  - ob die Seite (`.main`) scrollen müsste.

## Referenz und Inhalt

**Referenz:** Auf Rückfrage hat der Owner das Referenzbild im Ordner `assets` benannt: die Info-Karte in
`assets/ux-ui/43_Kanzleiwissen_Gesetze.png` (beigelegt als `referenz-43_Kanzleiwissen_Gesetze.png`) mit dem Panel-Bild
`assets/ux-ui/Kanzleiwissen_Panellbackground.png`.

**Inhalt der Leiste in dieser Reihenfolge (Informationshierarchie laut Auftrag):**

1. Illustration, Überschrift „Aktuelles Recht. Lokal verfügbar.“ und Beschreibung.
2. **Kanzlei-Team:** neu, aus echten Daten der Benutzerverwaltung – aktive Konten je Rolle, ohne Namen; deaktivierte
   Konten zählen nicht.
3. **Lokal verfügbar:** neu, aus echten Daten – lokal aktive Gesetze und deren Paragraphen; deaktivierte Gesetze zählen
   nicht.
4. Nutzungshinweise „Rechtssicher“, „Flexibel“ und „Immer aktuell“.

Es gibt keine Demo-Werte und keine neuen Bedienelemente. Kontaktdaten des Teams gibt es im Datenmodell nicht; sie werden
nicht angezeigt.

## Vorher (`vorher/messwerte_vorher.json`)

| Größe | Inhalt / sichtbare Höhe | Ergebnis |
|---|---|---|
| 1920×1080 | 712 / 692 px | **interne Scrollleiste** |
| 1280×720 | 631 / 392 px | **interne Scrollleiste**, ~40 % nur per Scrollen erreichbar |
| 960×600 | – | **Karte ausgeblendet** (`display: none`) |

## Änderungen

- **Kein internes Scrollen:** `max-height: none` und `overflow: visible` statt `max-height: 100%` und
  `overflow-y: auto`.
- **Referenzgrößen, wo Platz ist:** Bei ausreichender Höhe gelten die Größen der Referenz (Illustration ca. 190 px,
  Titel 24 px, Text 15 px, Merkmale 15 / 13,5 px). Sie schrumpfen nur so weit, wie die Fensterhöhe es verlangt:
  - fließend über `clamp(…vh…)`;
  - Stufe 801–900 px Höhe: kleinere Illustration, Merkmale als Fließtext „Titel – Text“;
  - Stufe unter 800 px Höhe: die rein dekorative Illustration entfällt (kein Informationsgehalt), Merkmale als
    Fließtext.
  - Text wird nie gekürzt.
- **Schmale Fenster (≤ 1100 px):** Die Karte steht als **kompakte Leiste über der Tabelle**:
  - links die Illustration;
  - in der Mitte Titel und Beschreibung;
  - rechts die drei Hinweise;
  - darunter Team und lokale Inhalte;
  - die Tabelle darunter behält ihren eigenen Scrollbereich.
- **Sehr niedrige schmale Fenster (960×600):** Kacheln einzeilig, kompakter Tabellenkopf. Leiste, Kacheln und erste
  Tabellenzeile sind dadurch gleichzeitig sichtbar, ohne Seitenscroll.
- **Titel:** Leerzeichen vor dem Zeilenumbruch, damit der einzeilige Titel korrekt getrennt ist.
- **Nebenbefund behoben:** Bei 1501–1600 px Breite stieß der Tabellenstatus an den Schalter.

## Ergebnis (`nachher/messwerte_nachher.json`)

| Größe | Inhalt / Höhe der Karte | intern scrollend | Karte endet / Tabelle endet | Seite muss scrollen |
|---|---|---|---|---|
| 1920×1080 | 656 / 656 | nein | 1020 / 1056 | nein |
| 1536×842 (maximiertes Desktopfenster bei 125 %) | 449 / 449 | nein | 813 / 818 | nein |
| 1366×768 | 392 / 392 | nein | 697 / 744 | nein |
| 1280×720 | 388 / 388 | nein | 692 / 696 | nein |
| 960×600 | 144 / 144 (Leiste über der Tabelle) | nein | 372 / 572 | **nein** |

Gleiche Werte im Dark Mode, in keiner Aufnahme Querscrollen. Die Aufnahme `960x600-*-01b-infoleiste-unten` ist jetzt
identisch mit `-01`, weil es nichts mehr zu scrollen gibt. Alle Aufnahmen wurden einzeln gesichtet und mit der
Referenzkarte verglichen.

**Zwischenstände (`zwischenstand/`), alle korrigiert:**

- `kompakt-*`: erste Fassung, auch bei viel Platz zu stark verkleinert.
- `referenzgroessen-ohne-team-*`: Referenzgrößen, noch ohne Team- und Inhaltszahlen; bei 960 px stand die Karte als
  Band **unter** der Tabelle und war erst nach Seitenscroll sichtbar.
- `1280x720-…-karte-ragt-unter-tabelle`, `960x600-…-band-titel-ohne-leerzeichen`: frühere Darstellungsfehler.

## Tests

- **Neu in `tests/test_web_knowledge_reference_layout.py`:**
  - kein internes Scrollen;
  - nie ausgeblendet;
  - Leerzeichen im Titel;
  - echte Team- und Inhaltszahlen: aktive Konten je Rolle, inaktive Konten und deaktivierte Gesetze werden nicht
    mitgezählt.
- **Kanzleiwissen, Gesetze, Design, Posteingang, Chat:** **439 bestanden**, 4 fehlgeschlagen. Die 4 Fehler sind
  ausschließlich die bekannten, vorbestehenden Logo-Tests in `test_design_refresh.py` (fremdgelöschte Logos).

## Verbleibende Einschränkungen

- **Illustration:** Unter 800 px Fensterhöhe (bei Breite > 1100 px) entfällt sie zugunsten der vollständigen
  Information; sie ist dekorativ.
- **960×600:** Neben Leiste und Kacheln ist nur die erste Tabellenzeile direkt sichtbar. Die übrigen Zeilen erreicht man
  über den eigenen Scrollbereich der Tabelle; er bestand schon vorher.
- **Kontaktinformationen des Teams:** gibt es im Datenmodell nicht und werden daher nicht angezeigt.
