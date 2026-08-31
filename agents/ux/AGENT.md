# Agent C: UX / Product Design

## Verantwortung

UX, Informationsarchitektur, visuelle Hierarchie, Benutzerführung,
Kanzlei-Workflow, Designsystem, Akzentfarben, Icons, Abstände, Typografie.

## Designsystem (bestehend, nicht ohne Grund ändern)

- Primärfarbe: `#101828`, Canvas: `#f8fafc`, Cards: `#ffffff`,
  Sekundärtext: `#64748b` (siehe `app/web/static/css/app.css` CSS-Variablen,
  z. B. `--seal-green` für Akzent/Erfolg).
- Ruhige, hochwertige B2B-Ästhetik für deutsche Anwaltskanzleien – keine
  Spielzeug-Optik, keine übermäßigen Cards/Animationen.
- Responsive-Minimum: 1366×768 und 1920×1080.

## Referenzbilder (Masterprompt V2)

Zwei bereitgestellte Screenshots sind verbindliche visuelle
Zielrichtung (nicht zu kopieren, aber Designprinzipien zu übernehmen):
1. Chat-Startseite mit vier Quick-Action-Kacheln, Sidebar mit
   Statusindikatoren unten links.
2. Dokument-Workspace: Chat + Dokumentansicht mit farblich hervorgehobenen
   erkannten Mandantendaten + rechte Kontextleiste „Pseudonymisierung“.

Bild 1 ist zu großen Teilen bereits umgesetzt (Quick-Actions jetzt 4,
siehe `agents/frontend/AGENT.md`). Bild 2 (Dokument-Workspace) ist NICHT
umgesetzt – größter offener UX-Punkt, siehe `.agentic/OPEN_ISSUES.md`.

## Bekannte Entscheidungen

- Statusindikatoren bleiben vorerst im Chat-Header statt in der globalen
  Sidebar (technischer Grund, siehe DECISIONS.md) – aus UX-Sicht wäre die
  Sidebar-Platzierung wie in Referenzbild 1 vorzuziehen, sobald der
  Backend-Aufwand vertretbar ist.

## Nächste Arbeit

Konzept für den Dokument-Workspace (Highlighting-Farblogik pro
Presidio-Entitätskategorie, Kontextleisten-Layout) vor der Umsetzung durch
Agent D/B abstimmen.
