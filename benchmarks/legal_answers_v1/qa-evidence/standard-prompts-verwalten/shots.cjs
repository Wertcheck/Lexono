// Screenshots des Modals "Standard-Prompts verwalten" gegen eine laufende QA-Instanz (siehe README.md im Verzeichnis).
const { chromium } = require('playwright');
const out = __dirname + '/';
const BASE = process.env.BASE || 'http://localhost:8099';
(async () => {
  const b = await chromium.launch();
  for (const [w, h] of [[1280, 720], [960, 600]]) {
    for (const theme of ['light', 'dark']) {
      const ctx = await b.newContext({ viewport: { width: w, height: h } });
      const p = await ctx.newPage();
      await p.goto(BASE + '/dashboard/login');
      await p.fill('input[name=email]', 'admin@qa.test');
      await p.fill('input[name=password]', 'QaPasswort12345');
      await p.click('button[type=submit]');
      await p.waitForURL('**/dashboard/chat**');
      if (theme === 'dark') { await p.evaluate(() => localStorage.setItem('lexono_theme', 'dark')); }
      await p.goto(BASE + '/dashboard/chat?new=1');
      await p.waitForTimeout(600);
      const sfx = `${w}x${h}-${theme}`;
      await p.screenshot({ path: `${out}00-startseite-${sfx}.png` });
      await p.click('[data-prompt-manager-open]');
      await p.waitForSelector('#pm-modal[open]');
      await p.waitForTimeout(500);
      await p.screenshot({ path: `${out}01-uebersicht-leer-${sfx}.png` });
      await p.click('[data-pm-add]');
      await p.waitForTimeout(300);
      await p.screenshot({ path: `${out}02-neuer-prompt-${sfx}.png` });
      await ctx.close();
    }
  }
  await b.close();
})();
