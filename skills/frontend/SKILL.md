# Skill: Frontend Change (Templates/CSS/JS)

## Zweck

Sichere Änderung an Jinja2-Templates, dem einzigen `app.css` oder Vanilla-
JS ohne Regression auf anderen Seiten.

## Wann einsetzen

Bei jeder UI-Änderung, insbesondere an gemeinsam genutzten Dateien
(`base.html`, `_icons.html`, `app.css`).

## Voraussetzungen

Verstehen, dass es KEINEN Build-Schritt gibt (kein Node.js/Webpack) –
Änderungen sind sofort wirksam.

## Vorgehensweise

1. Betroffene Templates lesen (Read vor Edit, Pflicht).
2. Grep nach der zu ändernden Zeichenkette/Klasse in `tests/` – prüfen,
   ob ein Test den alten Text/die alte Struktur erwartet.
3. Änderung vornehmen (Edit, kein Neuschreiben ganzer Dateien ohne Grund).
4. Bei Textänderungen: prüfen, ob dieselbe Zeichenkette an mehreren
   Stellen vorkommt (z. B. „KanzleiAI“ in mehreren Templates) – gezielt,
   nicht global ersetzen.
5. Volle Testsuite laufen lassen (siehe `skills/testing/SKILL.md`).

## Prüfungen

- Responsive bei 1366×768 und 1920×1080 (siehe `skills/visual_qa/SKILL.md`).
- Accessibility: `aria-label`/`title` bei interaktiven Icons erhalten.

## Typische Fehler

- CSS-Regeln für entfernte Elemente als „totes CSS“ zurücklassen.
- JS-Handler auf entfernte `data-*`-Attribute referenzieren lassen (führt
  zu stillen No-Ops, nicht zu Fehlern – schwer zu bemerken).

## Relevante Dateien

`app/web/templates/base.html`, `app/web/templates/_icons.html`,
`app/web/static/css/app.css`
