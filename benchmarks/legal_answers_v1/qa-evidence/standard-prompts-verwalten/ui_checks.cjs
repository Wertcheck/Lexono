// UI-Prüfungen des Modals "Standard-Prompts verwalten" gegen eine frisch geseedete QA-Instanz (siehe README.md).
const { chromium } = require('playwright');
const BASE = process.env.BASE || 'http://localhost:8099';
let pass = 0, fail = 0;
const log = [];
function check(name, ok, info) { (ok ? pass++ : fail++); log.push(`${ok ? 'PASS' : 'FAIL'}  ${name}${ok || !info ? '' : ' – ' + info}`); }

async function login(page, email) {
  await page.goto(BASE + '/dashboard/login');
  await page.fill('input[name=email]', email);
  await page.fill('input[name=password]', 'QaPasswort12345');
  await page.click('button[type=submit]');
  await page.waitForURL('**/dashboard/chat**');
}
const names = (page) => page.$$eval('#pm-modal .pm-item__title', (n) => n.map((x) => x.textContent.trim()));
const homeNames = (page) => page.$$eval('#chat-home-prompts .chat-quick-action__title', (n) => n.map((x) => x.textContent.trim()));
const open = async (page) => { await page.click('[data-prompt-manager-open]'); await page.waitForSelector('#pm-modal[open]'); await page.waitForTimeout(250); };
const item = (page, title) => page.locator('#pm-modal .pm-item', { has: page.locator('.pm-item__title', { hasText: new RegExp('^' + title + '$') }) });

(async () => {
  const b = await chromium.launch();
  for (const [w, h] of [[1280, 720], [960, 600]]) {
    const tag = `${w}x${h}`;
    const ctx = await b.newContext({ viewport: { width: w, height: h } });
    const page = await ctx.newPage();
    await login(page, 'admin@qa.test');
    await page.goto(BASE + '/dashboard/chat?new=1');
    await page.waitForTimeout(400);

    check(`[${tag}] Startseite zeigt genau die 4 aktivierten Prompts in Reihenfolge`, JSON.stringify(await homeNames(page)) === JSON.stringify(['Dokument analysieren', 'Schriftsatz erstellen', 'Zusammenfassen', 'Akte öffnen']), JSON.stringify(await homeNames(page)));
    await open(page);
    check(`[${tag}] Modal ist zentriert und liegt vollständig im Fenster`, await page.evaluate(() => { const r = document.getElementById('pm-modal').getBoundingClientRect(); return r.left >= 0 && r.top >= 0 && r.right <= innerWidth && r.bottom <= innerHeight && Math.abs((r.left + r.right) / 2 - innerWidth / 2) < 3 && document.documentElement.scrollWidth <= innerWidth; }));
    check(`[${tag}] Beim Öffnen ist kein Prompt ausgewählt, rechts der Leerzustand`, await page.evaluate(() => !document.querySelector('.pm-item--selected') && !document.querySelector('[data-pm-empty]').hidden && document.querySelector('[data-pm-form]').hidden));
    check(`[${tag}] Zähler und Belegung: "4 von 6 aktiv", "4/4 Startplätze belegt"`, (await page.textContent('[data-pm-count]')).includes('4 von 6') && (await page.textContent('[data-pm-slots]')).includes('4/4 Startplätze belegt'));
    check(`[${tag}] Fußleiste (Abbrechen/Speichern) und Hinzufügen-Button sichtbar`, await page.evaluate(() => ['[data-pm-save]', '[data-pm-cancel]', '[data-pm-add]'].every((s) => { const r = document.querySelector(s).getBoundingClientRect(); return r.bottom <= innerHeight && r.height > 0; })));

    // Auswahl
    await item(page, 'Recherche').locator('.pm-item__main').click();
    check(`[${tag}] Auswahl lädt Titel/Beschreibung/Anweisung; Auswahl ≠ Aktivierung`, await page.evaluate(() => document.querySelector('[data-pm-input=name]').value === 'Recherche' && document.querySelector('[data-pm-input=description]').value.includes('Gesetze') && document.querySelector('[data-pm-input=content]').value === 'Bitte recherchiere.' && document.querySelector('.pm-item--selected .pm-switch').getAttribute('aria-checked') === 'false'));

    // Ungespeicherte Änderung + Schließen
    await page.fill('[data-pm-input=name]', 'Recherche X');
    await page.click('[data-pm-close]');
    check(`[${tag}] Schließen mit Änderungen fragt nach`, await page.isVisible('[data-pm-confirm]') && (await page.textContent('[data-pm-confirm-title]')).includes('verwerfen'));
    await page.click('[data-pm-confirm-no]');
    check(`[${tag}] "Weiter bearbeiten" behält Modal und Änderung`, (await page.evaluate(() => document.getElementById('pm-modal').open)) && (await page.inputValue('[data-pm-input=name]')) === 'Recherche X');
    await page.keyboard.press('Escape');
    check(`[${tag}] Escape bei Änderungen fragt ebenfalls nach`, await page.isVisible('[data-pm-confirm]'));
    await page.click('[data-pm-confirm-yes]');
    await page.waitForTimeout(200);
    check(`[${tag}] "Verwerfen" schließt, fokussiert den Auslöser`, !(await page.evaluate(() => document.getElementById('pm-modal').open)) && (await page.evaluate(() => document.activeElement.hasAttribute('data-prompt-manager-open'))));
    await open(page);
    check(`[${tag}] Verworfene Änderung wurde nicht gespeichert`, (await names(page)).includes('Recherche') && !(await names(page)).includes('Recherche X'));

    // Viererlimit
    await item(page, 'Recherche').locator('.pm-switch').click();
    check(`[${tag}] Fünfte Aktivierung wird verhindert und erklärt`, (await item(page, 'Recherche').locator('.pm-switch').getAttribute('aria-checked')) === 'false' && (await page.textContent('[data-pm-status]')).includes('zuerst einen anderen Prompt'));
    await item(page, 'Akte öffnen').locator('.pm-switch').click();
    check(`[${tag}] Deaktivieren gibt Startplatz frei (3/4)`, (await page.textContent('[data-pm-slots]')).includes('3/4'));
    await item(page, 'Recherche').locator('.pm-switch').click();
    check(`[${tag}] Danach lässt sich Recherche aktivieren (4/4)`, (await item(page, 'Recherche').locator('.pm-switch').getAttribute('aria-checked')) === 'true' && (await page.textContent('[data-pm-slots]')).includes('4/4'));

    // Sortierung: Maus-Drag & Tastatur
    // Echte Mausgeste: Griff greifen, zum oberen Listenrand ziehen (Autoscroll), auf den ersten Eintrag fallen lassen
    await item(page, 'Recherche').locator('.pm-grip').scrollIntoViewIfNeeded();
    const gb = await item(page, 'Recherche').locator('.pm-grip').boundingBox();
    await page.mouse.move(gb.x + gb.width / 2, gb.y + gb.height / 2);
    await page.mouse.down();
    const lb = await page.locator('#pm-modal [data-pm-list]').boundingBox();
    await page.mouse.move(lb.x + 120, lb.y + 60, { steps: 6 });
    await page.mouse.move(lb.x + 120, lb.y + 8, { steps: 6 });
    for (let i = 0; i < 40; i++) { await page.mouse.move(lb.x + 120, lb.y + 8 + (i % 2)); await page.waitForTimeout(25); }
    const fb = await item(page, 'Dokument analysieren').locator('.pm-item__main').boundingBox();
    await page.mouse.move(fb.x + 60, fb.y + 4, { steps: 6 });
    await page.mouse.up();
    await page.waitForTimeout(200);
    let order = await names(page);
    check(`[${tag}] Drag-and-drop verschiebt Recherche an den Anfang`, order[0] === 'Recherche', JSON.stringify(order));
    await item(page, 'Recherche').locator('.pm-grip').focus();
    await page.keyboard.press('ArrowDown');
    order = await names(page);
    check(`[${tag}] Tastatur (Pfeil runter am Griff) sortiert um`, order[0] === 'Dokument analysieren' && order[1] === 'Recherche', JSON.stringify(order));

    // Speichern + Startseite
    await page.click('[data-pm-save]');
    await page.waitForFunction(() => document.querySelector('[data-pm-status]').textContent.includes('Gespeichert'));
    const expectedHome = ['Dokument analysieren', 'Recherche', 'Schriftsatz erstellen', 'Zusammenfassen'];
    check(`[${tag}] Speichern bestätigt und aktualisiert die Startseite sofort (nur aktive, in Reihenfolge)`, JSON.stringify(await homeNames(page)) === JSON.stringify(expectedHome), JSON.stringify(await homeNames(page)));
    await page.click('[data-pm-cancel]');
    await page.reload();
    await page.waitForTimeout(300);
    check(`[${tag}] Nach Neuladen: Auswahl und Reihenfolge dauerhaft erhalten`, JSON.stringify(await homeNames(page)) === JSON.stringify(expectedHome), JSON.stringify(await homeNames(page)));
    await open(page);
    check(`[${tag}] Modal zeigt nach Neuladen dieselbe Reihenfolge`, JSON.stringify((await names(page)).slice(0, 4)) === JSON.stringify(['Dokument analysieren', 'Recherche', 'Schriftsatz erstellen', 'Zusammenfassen']), JSON.stringify(await names(page)));

    // Prompt anklicken: Startseitenkachel verwendet Anweisung
    await page.click('[data-pm-cancel]');
    await page.click('#chat-home-prompts .chat-quick-action >> nth=1');
    check(`[${tag}] Klick auf Startseiten-Kachel füllt den Chat mit der Anweisung`, (await page.inputValue('#chat-input')) === 'Bitte recherchiere.');

    // Neuer Prompt: Validierung, Anlegen
    await open(page);
    await page.click('[data-pm-add]');
    check(`[${tag}] Neu-Formular: leere Felder, Zähler 0/100, 0/200, 0/2000`, await page.evaluate(() => ['name', 'description', 'content'].every((f) => document.querySelector(`[data-pm-input=${f}]`).value === '') && document.querySelector('[data-pm-counter=name]').textContent === '0/100' && document.querySelector('[data-pm-counter=description]').textContent === '0/200' && document.querySelector('[data-pm-counter=content]').textContent === '0/2000'));
    check(`[${tag}] Neu-Formular bei 4/4: Hinweis zur Deaktivierung`, (await page.textContent('[data-pm-home-note]')).includes('Bitte einen bestehenden Prompt deaktivieren'));
    await page.fill('[data-pm-input=name]', 'Vertragsanalyse');
    await page.click('[data-pm-save]');
    check(`[${tag}] Pflichtfelder werden validiert (Beschreibung/Anweisung)`, (await page.textContent('[data-pm-err=description]')).includes('Beschreibung') && (await page.textContent('[data-pm-err=content]')).includes('Anweisung'));
    await page.fill('[data-pm-input=description]', 'Verträge prüfen');
    await page.fill('[data-pm-input=content]', 'Prüfe den Vertrag auf Risiken.');
    check(`[${tag}] Zeichenzähler läuft (15/100)`, (await page.textContent('[data-pm-counter=name]')) === '15/100');
    await page.click('[data-pm-icon-btn]');
    await page.locator('.pm-icon-opt[aria-label="Waage"]').click();
    await page.click('[data-pm-save]');
    await page.waitForFunction(() => document.querySelector('[data-pm-status]').textContent.includes('Gespeichert'));
    check(`[${tag}] Neuer Prompt gespeichert, erscheint (nicht aktiv) in der Liste`, (await names(page)).includes('Vertragsanalyse') && (await page.textContent('[data-pm-count]')).includes('4 von 7'));

    // Löschen mit Bestätigung
    await item(page, 'Vertragsanalyse').locator('.pm-more__btn').click();
    await item(page, 'Vertragsanalyse').locator('.pm-more__item--danger').click();
    check(`[${tag}] Löschen fragt nach`, await page.isVisible('[data-pm-confirm]'));
    await page.click('[data-pm-confirm-no]');
    check(`[${tag}] Abbrechen beim Löschen behält den Prompt`, (await names(page)).includes('Vertragsanalyse'));
    await item(page, 'Vertragsanalyse').locator('.pm-more__btn').click();
    await item(page, 'Vertragsanalyse').locator('.pm-more__item--danger').click();
    await page.click('[data-pm-confirm-yes]');
    await page.click('[data-pm-save]');
    await page.waitForFunction(() => document.querySelector('[data-pm-status]').textContent.includes('Gespeichert'));
    check(`[${tag}] Bestätigtes Löschen entfernt den Prompt dauerhaft`, !(await names(page)).includes('Vertragsanalyse'));
    check(`[${tag}] Kein horizontaler Überlauf im Modal`, await page.evaluate(() => { const m = document.getElementById('pm-modal'); return m.scrollWidth <= m.clientWidth + 1; }));
    await ctx.close();

    // Mitarbeiter: keine Verwaltung
    const ctx2 = await b.newContext({ viewport: { width: w, height: h } });
    const p2 = await ctx2.newPage();
    await login(p2, 'mitarbeiter@qa.test');
    await p2.goto(BASE + '/dashboard/chat?new=1');
    check(`[${tag}] Mitarbeiter: kein Verwalten-Button, Kacheln sichtbar`, (await p2.locator('[data-prompt-manager-open]').count()) === 0 && (await homeNames(p2)).length === 4);
    await ctx2.close();

    // Seed für den nächsten Durchlauf zurücksetzen (zweite Auflösung)
    if (w === 1280) { require('child_process').execSync(process.env.RESEED || 'true'); }
  }
  await b.close();
  console.log(log.join('\n'));
  console.log(`\n${pass}/${pass + fail} Prüfungen bestanden`);
  process.exit(fail ? 1 : 0);
})();
