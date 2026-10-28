/* Modal "Standard-Prompts verwalten" (Chat-Startseite).
 *
 * Arbeitskopie im Speicher: Anlegen, Bearbeiten, Loeschen, Aktivieren und
 * Sortieren aendern erst die Kopie; "Speichern" sendet den ganzen Zustand in
 * EINEM Request (POST /dashboard/library/prompts/manage/state, atomar). Das
 * Modal ist die einzige Quelle: danach werden die Startseiten-Kacheln aus dem
 * gespeicherten Zustand neu aufgebaut. Kein Framework, kein Build-Schritt.
 */
(function () {
  "use strict";

  var dlg = document.getElementById("pm-modal");
  if (!dlg || typeof dlg.showModal !== "function") { return; }

  var $ = function (sel, root) { return (root || dlg).querySelector(sel); };
  var els = {
    list: $("[data-pm-list]"), count: $("[data-pm-count]"), slots: $("[data-pm-slots]"),
    empty: $("[data-pm-empty]"), form: $("[data-pm-form]"),
    formTitle: $("[data-pm-form-title]"), formSub: $("[data-pm-form-sub]"),
    iconBtn: $("[data-pm-icon-btn]"), iconCurrent: $("[data-pm-icon-current]"), iconMenu: $("[data-pm-icon-menu]"),
    homeToggle: $("[data-pm-home-toggle]"), homeNote: $("[data-pm-home-note]"),
    attach: $("[data-pm-attach]"), delWrap: $("[data-pm-delete-wrap]"), del: $("[data-pm-delete]"),
    status: $("[data-pm-status]"), save: $("[data-pm-save]"), cancel: $("[data-pm-cancel]"),
    confirm: $("[data-pm-confirm]"), confirmTitle: $("[data-pm-confirm-title]"), confirmText: $("[data-pm-confirm-text]"),
    confirmYes: $("[data-pm-confirm-yes]"), confirmNo: $("[data-pm-confirm-no]")
  };
  var FIELDS = ["name", "description", "content"];

  var icons = {};      // key -> {svg, label, tint}
  var extra = {};      // grip / more
  document.querySelectorAll("template[data-pm-icon]").forEach(function (t) {
    icons[t.getAttribute("data-pm-icon")] = { svg: t.innerHTML.trim(), label: t.getAttribute("data-label"), tint: t.getAttribute("data-tint") };
  });
  document.querySelectorAll("template[data-pm-icon-extra]").forEach(function (t) {
    extra[t.getAttribute("data-pm-icon-extra")] = t.innerHTML.trim();
  });

  var csrf = dlg.getAttribute("data-csrf");
  var stateUrl = dlg.getAttribute("data-state-url");
  var limit = 4;
  var max = { name: 100, description: 200, content: 2000 };
  var original = [];   // zuletzt gespeicherter Zustand (Serialisierung)
  var items = [];      // Arbeitskopie (Reihenfolge = Reihenfolge der Liste)
  var deleted = [];    // ids geloeschter, gespeicherter Prompts
  var selected = null; // Referenz auf Eintrag in items
  var trigger = null;
  var errors = {};     // key (client id) -> {field: message}
  var uid = 0;
  var confirmAction = null;
  var busy = false;
  var dragFrom = null;

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) { n.className = cls; }
    if (text != null) { n.textContent = text; }
    return n;
  }
  function plain(p) {
    return { id: p.id, name: p.name, description: p.description || "", content: p.content,
             icon: p.icon || "document", show_on_home: !!p.show_on_home, attach_on_use: !!p.attach_on_use };
  }
  function snapshot() {
    return JSON.stringify({ items: items.filter(function (i) { return !isBlankDraft(i); }).map(plain), deleted: deleted });
  }
  function isBlankDraft(i) {
    return !i.id && !i.name.trim() && !i.description.trim() && !i.content.trim();
  }
  function isDirty() { return snapshot() !== original; }
  function activeCount() {
    return items.filter(function (i) { return i.show_on_home && !isBlankDraft(i); }).length;
  }
  function tint(key) { return (icons[key] && icons[key].tint) || "green"; }
  function setStatus(text, kind) {
    els.status.textContent = text || "";
    els.status.className = "pm-status" + (kind ? " pm-status--" + kind : "");
  }

  /* ---------- Laden / Oeffnen / Schliessen ---------- */
  function load() {
    return fetch(stateUrl, { headers: { "Accept": "application/json" }, credentials: "same-origin" })
      .then(function (r) { if (!r.ok) { throw new Error("load"); } return r.json(); })
      .then(function (data) {
        limit = data.limit || 4;
        max = data.max || max;
        items = data.prompts.map(function (p) { var q = plain(p); q._k = ++uid; return q; });
        deleted = [];
        selected = null;
        errors = {};
        original = snapshot();
        dlg.querySelectorAll("[data-pm-limit]").forEach(function (n) { n.textContent = String(limit); });
        render();
      });
  }
  function open(from) {
    trigger = from || null;
    setStatus("");
    load().then(function () {
      document.documentElement.classList.add("modal-open");
      dlg.showModal();
      els.list.querySelector(".pm-item__main") && els.list.querySelector(".pm-item__main").focus();
    }).catch(function () {
      window.alert("Die Standard-Prompts konnten nicht geladen werden. Bitte versuchen Sie es erneut.");
    });
  }
  function closeNow() {
    dlg.close();
  }
  function requestClose() {
    if (busy) { return; }
    if (!isDirty()) { closeNow(); return; }
    ask({
      title: "Ungespeicherte Änderungen verwerfen?",
      text: "Ihre Änderungen an den Standard-Prompts wurden noch nicht gespeichert und gehen verloren.",
      yes: "Verwerfen", no: "Weiter bearbeiten"
    }, closeNow);
  }
  dlg.addEventListener("close", function () {
    document.documentElement.classList.remove("modal-open");
    hideConfirm();
    closeIconMenu();
    if (trigger && trigger.isConnected) { trigger.focus(); }
  });
  dlg.addEventListener("cancel", function (e) {  // Escape
    e.preventDefault();
    if (!els.confirm.hidden) { hideConfirm(); return; }
    if (!els.iconMenu.hidden) { closeIconMenu(); els.iconBtn.focus(); return; }
    requestClose();
  });
  dlg.addEventListener("click", function (e) {  // Klick auf den Hintergrund
    if (e.target === dlg) { requestClose(); }
  });

  /* ---------- Bestaetigungsdialog (innerhalb des Modals) ---------- */
  function ask(cfg, onYes) {
    els.confirmTitle.textContent = cfg.title;
    els.confirmText.textContent = cfg.text;
    els.confirmYes.textContent = cfg.yes;
    els.confirmNo.textContent = cfg.no;
    confirmAction = onYes;
    els.confirm.hidden = false;
    els.confirmNo.focus();
  }
  function hideConfirm() { els.confirm.hidden = true; confirmAction = null; }
  els.confirmNo.addEventListener("click", hideConfirm);
  els.confirmYes.addEventListener("click", function () {
    var fn = confirmAction;
    hideConfirm();
    if (fn) { fn(); }
  });

  /* ---------- Liste ---------- */
  function render() {
    renderList();
    renderForm();
    var n = items.filter(function (i) { return !isBlankDraft(i); }).length;
    var a = activeCount();
    els.count.textContent = "(" + a + " von " + n + " aktiv auf Startseite)";
    els.slots.textContent = a + "/" + limit + " Startplätze belegt";
    els.slots.className = "pm-slots" + (a >= limit ? " pm-slots--full" : "");
  }
  function renderList() {
    var keepFocus = document.activeElement && document.activeElement.getAttribute && document.activeElement.getAttribute("data-pm-focus");
    els.list.textContent = "";
    items.forEach(function (it, idx) {
      var li = el("li", "pm-item pm-item--" + tint(it.icon) + (it.show_on_home ? " pm-item--home" : "") + (it === selected ? " pm-item--selected" : ""));
      li.setAttribute("data-pm-key", String(it._k));
      li.draggable = false;

      var grip = el("button", "pm-grip");
      grip.type = "button";
      grip.innerHTML = extra.grip;
      grip.setAttribute("aria-label", "Reihenfolge ändern: " + (it.name || "Neuer Prompt") + " (Pfeiltasten hoch/runter oder ziehen)");
      grip.setAttribute("data-pm-focus", "grip-" + it._k);
      grip.addEventListener("mousedown", function () { li.draggable = true; });
      grip.addEventListener("keydown", function (e) {
        if (e.key === "ArrowUp" || e.key === "ArrowDown") {
          e.preventDefault();
          move(idx, e.key === "ArrowUp" ? idx - 1 : idx + 1);
          var again = els.list.querySelector('[data-pm-focus="grip-' + it._k + '"]');
          if (again) { again.focus(); }
        }
      });
      li.appendChild(grip);

      var main = el("button", "pm-item__main");
      main.type = "button";
      main.setAttribute("aria-current", it === selected ? "true" : "false");
      var ic = el("span", "pm-item__icon pm-item__icon--" + tint(it.icon));
      ic.innerHTML = (icons[it.icon] || icons.document || { svg: "" }).svg;
      var tx = el("span", "pm-item__text");
      tx.appendChild(el("span", "pm-item__title", it.name.trim() || "Neuer Prompt"));
      tx.appendChild(el("span", "pm-item__desc", it.description.trim() || (it.id ? "" : "Noch nicht gespeichert")));
      main.appendChild(ic); main.appendChild(tx);
      main.addEventListener("click", function () { select(it); });
      li.appendChild(main);

      var sw = el("div", "pm-item__home");
      var toggle = el("button", "pm-switch" + (it.show_on_home ? " pm-switch--on" : ""));
      toggle.type = "button";
      toggle.setAttribute("role", "switch");
      toggle.setAttribute("aria-checked", it.show_on_home ? "true" : "false");
      toggle.setAttribute("aria-label", "Auf der Chat-Startseite anzeigen: " + (it.name || "Neuer Prompt"));
      toggle.appendChild(el("span", "pm-switch__knob"));
      toggle.addEventListener("click", function () { setHome(it, !it.show_on_home); });
      sw.appendChild(toggle);
      sw.appendChild(el("span", "pm-item__home-label", it.show_on_home ? "Startseite" : "Nicht auf Startseite"));
      li.appendChild(sw);

      var more = el("div", "pm-more");
      var mb = el("button", "pm-more__btn");
      mb.type = "button";
      mb.innerHTML = extra.more;
      mb.setAttribute("aria-label", "Aktionen für " + (it.name || "Neuer Prompt"));
      mb.setAttribute("aria-haspopup", "menu");
      mb.setAttribute("aria-expanded", "false");
      var menu = el("div", "pm-more__menu");
      menu.setAttribute("role", "menu");
      menu.hidden = true;
      var mEdit = el("button", "pm-more__item", "Bearbeiten");
      var mDel = el("button", "pm-more__item pm-more__item--danger", "Löschen");
      [mEdit, mDel].forEach(function (b) { b.type = "button"; b.setAttribute("role", "menuitem"); menu.appendChild(b); });
      mb.addEventListener("click", function (e) {
        e.stopPropagation();
        var willOpen = menu.hidden;
        closeMenus();
        menu.hidden = !willOpen;
        mb.setAttribute("aria-expanded", willOpen ? "true" : "false");
      });
      mEdit.addEventListener("click", function () { closeMenus(); select(it); focusFirstField(); });
      mDel.addEventListener("click", function () { closeMenus(); requestDelete(it); });
      more.appendChild(mb); more.appendChild(menu);
      li.appendChild(more);

      // Drag-and-drop (Griff): native HTML5-Events
      li.addEventListener("dragstart", function (e) {
        dragFrom = idx;
        li.classList.add("pm-item--dragging");
        if (e.dataTransfer) { e.dataTransfer.effectAllowed = "move"; e.dataTransfer.setData("text/plain", String(it._k)); }
      });
      li.addEventListener("dragend", function () { li.draggable = false; li.classList.remove("pm-item--dragging"); dragFrom = null; clearDropMarks(); });
      li.addEventListener("dragover", function (e) {
        if (dragFrom === null) { return; }
        e.preventDefault();
        if (e.dataTransfer) { e.dataTransfer.dropEffect = "move"; }
        clearDropMarks();
        var r = li.getBoundingClientRect();
        li.classList.add(e.clientY < r.top + r.height / 2 ? "pm-item--drop-before" : "pm-item--drop-after");
      });
      li.addEventListener("drop", function (e) {
        if (dragFrom === null) { return; }
        e.preventDefault();
        var r = li.getBoundingClientRect();
        var to = e.clientY < r.top + r.height / 2 ? idx : idx + 1;
        if (dragFrom < to) { to -= 1; }
        var from = dragFrom;
        dragFrom = null;
        clearDropMarks();
        move(from, to);
      });
      els.list.appendChild(li);
    });
    if (keepFocus) {
      var f = els.list.querySelector('[data-pm-focus="' + keepFocus + '"]');
      if (f) { f.focus(); }
    }
  }
  // Automatisches Scrollen der Liste, wenn beim Ziehen der Rand erreicht wird (lange Listen)
  els.list.addEventListener("dragover", function (e) {
    if (dragFrom === null) { return; }
    var r = els.list.getBoundingClientRect();
    if (e.clientY < r.top + 36) { els.list.scrollTop -= 14; }
    else if (e.clientY > r.bottom - 36) { els.list.scrollTop += 14; }
  });
  function clearDropMarks() {
    els.list.querySelectorAll(".pm-item--drop-before,.pm-item--drop-after").forEach(function (n) {
      n.classList.remove("pm-item--drop-before", "pm-item--drop-after");
    });
  }
  function closeMenus() {
    dlg.querySelectorAll(".pm-more__menu").forEach(function (m) { m.hidden = true; });
    dlg.querySelectorAll(".pm-more__btn").forEach(function (b) { b.setAttribute("aria-expanded", "false"); });
  }
  dlg.addEventListener("click", function (e) {
    if (!e.target.closest(".pm-more")) { closeMenus(); }
    if (!e.target.closest(".pm-icon-picker")) { closeIconMenu(); }
  });

  function move(from, to) {
    if (to < 0 || to >= items.length || from === to) { return; }
    var it = items.splice(from, 1)[0];
    items.splice(to, 0, it);
    setStatus("");
    render();
  }

  /* ---------- Auswahl / Formular ---------- */
  function select(it) {
    if (selected && selected !== it && isBlankDraft(selected)) {
      items.splice(items.indexOf(selected), 1);   // leerer Entwurf: nichts geht verloren
    }
    selected = it;
    render();
  }
  function focusFirstField() { var f = $('[data-pm-input="name"]'); if (f) { f.focus(); } }
  function addNew() {
    if (selected && isBlankDraft(selected)) { focusFirstField(); return; }
    var it = { id: null, name: "", description: "", content: "", icon: "document", show_on_home: false, attach_on_use: false, _k: ++uid };
    items.push(it);
    selected = it;
    render();
    focusFirstField();
    var ul = els.list;
    ul.scrollTop = ul.scrollHeight;
  }
  function renderForm() {
    var it = selected;
    els.empty.hidden = !!it;
    els.form.hidden = !it;
    if (!it) { return; }
    var isNew = !it.id;
    els.formTitle.textContent = isNew ? "Neuen Prompt erstellen" : "Prompt bearbeiten";
    els.formSub.textContent = isNew ? "Geben Sie hier die Details für Ihren neuen Prompt ein." : "Änderungen werden erst mit „Speichern“ übernommen.";
    FIELDS.forEach(function (f) {
      var input = $('[data-pm-input="' + f + '"]');
      if (input.value !== it[f]) { input.value = it[f]; }
      input.setAttribute("maxlength", String(max[f]));
      updateCounter(f);
      showError(f);
    });
    els.iconCurrent.innerHTML = (icons[it.icon] || icons.document).svg;
    els.iconBtn.setAttribute("aria-label", "Icon: " + ((icons[it.icon] || {}).label || ""));
    els.attach.checked = !!it.attach_on_use;
    syncHome();
    els.delWrap.hidden = isNew;
  }
  function updateCounter(f) {
    var c = $('[data-pm-counter="' + f + '"]');
    c.textContent = (selected ? selected[f].length : 0) + "/" + max[f];
  }
  function showError(f) {
    var msg = (selected && errors[selected._k] && errors[selected._k][f]) || "";
    var e = $('[data-pm-err="' + f + '"]');
    e.textContent = msg;
    var input = $('[data-pm-input="' + f + '"]');
    input.classList.toggle("pm-input--error", !!msg);
    input.setAttribute("aria-invalid", msg ? "true" : "false");
  }
  FIELDS.forEach(function (f) {
    $('[data-pm-input="' + f + '"]').addEventListener("input", function (e) {
      if (!selected) { return; }
      selected[f] = e.target.value;
      if (errors[selected._k]) { delete errors[selected._k][f]; showError(f); }
      updateCounter(f);
      setStatus("");
      renderList();
      refreshHeader();
    });
  });
  function refreshHeader() {
    var n = items.filter(function (i) { return !isBlankDraft(i); }).length;
    els.count.textContent = "(" + activeCount() + " von " + n + " aktiv auf Startseite)";
  }
  els.attach.addEventListener("change", function () { if (selected) { selected.attach_on_use = els.attach.checked; setStatus(""); } });

  /* ---------- Startseite aktivieren (Limit) ---------- */
  function limitMessage() {
    return "Aktuell " + activeCount() + " von " + limit + " Prompts ausgewählt. Bitte einen bestehenden Prompt deaktivieren, um diesen anzuzeigen.";
  }
  function setHome(it, on) {
    if (on && !it.show_on_home && activeCount() >= limit) {
      var msg = "Maximal " + limit + " Prompts können auf der Startseite angezeigt werden. Bitte deaktivieren Sie zuerst einen anderen Prompt.";
      setStatus(msg, "warn");
      if (it === selected) { els.homeNote.textContent = limitMessage(); els.homeNote.classList.add("pm-home-note--warn"); }
      return;
    }
    it.show_on_home = on;
    setStatus("");
    render();
  }
  function syncHome() {
    var it = selected;
    var on = !!(it && it.show_on_home);
    els.homeToggle.setAttribute("aria-checked", on ? "true" : "false");
    els.homeToggle.classList.toggle("pm-switch--on", on);
    var full = !on && activeCount() >= limit;
    els.homeToggle.classList.toggle("pm-switch--blocked", full);
    els.homeNote.classList.toggle("pm-home-note--warn", full);
    els.homeNote.textContent = full ? limitMessage()
      : (on ? "Dieser Prompt erscheint auf der Chat-Startseite. Belegt: " + activeCount() + " von " + limit + "." : "Belegt: " + activeCount() + " von " + limit + " Startplätzen.");
  }
  els.homeToggle.addEventListener("click", function () { if (selected) { setHome(selected, !selected.show_on_home); } });

  /* ---------- Icon-Auswahl ---------- */
  function closeIconMenu() { els.iconMenu.hidden = true; els.iconBtn.setAttribute("aria-expanded", "false"); }
  function buildIconMenu() {
    els.iconMenu.textContent = "";
    Object.keys(icons).forEach(function (key) {
      var li = el("li", "pm-icon-opt" + (selected && selected.icon === key ? " pm-icon-opt--on" : ""));
      li.setAttribute("role", "option");
      li.setAttribute("aria-selected", selected && selected.icon === key ? "true" : "false");
      li.tabIndex = 0;
      li.title = icons[key].label;
      li.innerHTML = icons[key].svg;
      li.setAttribute("aria-label", icons[key].label);
      function pick() { if (selected) { selected.icon = key; setStatus(""); render(); } closeIconMenu(); els.iconBtn.focus(); }
      li.addEventListener("click", pick);
      li.addEventListener("keydown", function (e) { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(); } });
      els.iconMenu.appendChild(li);
    });
  }
  els.iconBtn.addEventListener("click", function () {
    if (!els.iconMenu.hidden) { closeIconMenu(); return; }
    buildIconMenu();
    els.iconMenu.hidden = false;
    els.iconBtn.setAttribute("aria-expanded", "true");
    var first = els.iconMenu.querySelector(".pm-icon-opt--on") || els.iconMenu.firstChild;
    if (first) { first.focus(); }
  });

  /* ---------- Loeschen ---------- */
  function requestDelete(it) {
    ask({
      title: "Prompt löschen?",
      text: "„" + (it.name.trim() || "Neuer Prompt") + "“ wird mit „Speichern“ endgültig gelöscht. Zum Ausblenden auf der Startseite genügt es, den Prompt zu deaktivieren.",
      yes: "Löschen", no: "Abbrechen"
    }, function () {
      var i = items.indexOf(it);
      if (i < 0) { return; }
      items.splice(i, 1);
      if (it.id) { deleted.push(it.id); }
      if (selected === it) { selected = null; }
      setStatus("");
      render();
    });
  }
  els.del.addEventListener("click", function () { if (selected) { requestDelete(selected); } });

  /* ---------- Speichern ---------- */
  function validateLocal() {
    errors = {};
    var first = null;
    items.forEach(function (it) {
      if (isBlankDraft(it)) { return; }
      var e = {};
      if (!it.name.trim()) { e.name = "Bitte geben Sie einen Titel ein."; }
      else if (it.name.length > max.name) { e.name = "Der Titel darf höchstens " + max.name + " Zeichen lang sein."; }
      if (!it.description.trim()) { e.description = "Bitte geben Sie eine Beschreibung ein."; }
      else if (it.description.length > max.description) { e.description = "Die Beschreibung darf höchstens " + max.description + " Zeichen lang sein."; }
      if (!it.content.trim()) { e.content = "Bitte geben Sie eine Prompt-Anweisung ein."; }
      else if (it.content.length > max.content) { e.content = "Die Anweisung darf höchstens " + max.content + " Zeichen lang sein."; }
      if (Object.keys(e).length) { errors[it._k] = e; if (!first) { first = it; } }
    });
    return first;
  }
  function save() {
    if (busy) { return; }
    items = items.filter(function (i) { return !isBlankDraft(i) || i === selected; });
    var bad = validateLocal();
    if (bad) {
      selected = bad;
      render();
      var f = FIELDS.filter(function (x) { return errors[bad._k][x]; })[0];
      var inp = $('[data-pm-input="' + f + '"]');
      if (inp) { inp.focus(); }
      setStatus("Bitte füllen Sie alle Pflichtfelder aus.", "error");
      return;
    }
    if (!isDirty()) { setStatus("Keine Änderungen zu speichern."); return; }
    busy = true;
    els.save.disabled = true;
    setStatus("Wird gespeichert …");
    var payload = JSON.stringify({
      prompts: items.filter(function (i) { return !isBlankDraft(i); }).map(plain),
      deleted: deleted
    });
    var body = new URLSearchParams();
    body.set("csrf_token", csrf);
    body.set("payload", payload);
    fetch(stateUrl, {
      method: "POST", credentials: "same-origin",
      headers: { "Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded" },
      body: body.toString()
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (data) { return { ok: r.ok, status: r.status, data: data }; });
    }).then(function (res) {
      busy = false;
      els.save.disabled = false;
      if (!res.ok) { return fail(res); }
      var keepName = selected && selected.id ? selected.id : null;
      items = res.data.prompts.map(function (p) { var q = plain(p); q._k = ++uid; return q; });
      deleted = [];
      errors = {};
      selected = keepName ? (items.filter(function (i) { return i.id === keepName; })[0] || null) : null;
      original = snapshot();
      renderHome(items);
      render();
      setStatus("Gespeichert.", "ok");
    }).catch(function () {
      busy = false;
      els.save.disabled = false;
      setStatus("Speichern fehlgeschlagen. Bitte prüfen Sie die Verbindung und versuchen Sie es erneut.", "error");
    });
  }
  function fail(res) {
    var list = (res.data && res.data.errors) || [];
    if (res.status === 403 || res.status === 401) {
      setStatus("Sie haben keine Berechtigung, Standard-Prompts zu ändern, oder Ihre Sitzung ist abgelaufen.", "error");
      return;
    }
    errors = {};
    var shown = items.filter(function (i) { return !isBlankDraft(i); });
    var firstItem = null, global = "";
    list.forEach(function (e) {
      if (e.index == null) { global = e.message; return; }
      var it = shown[e.index];
      if (!it) { return; }
      (errors[it._k] = errors[it._k] || {})[e.field] = e.message;
      if (!firstItem) { firstItem = it; }
    });
    if (firstItem) { selected = firstItem; }
    render();
    setStatus(global || "Bitte korrigieren Sie die markierten Felder.", "error");
  }
  els.save.addEventListener("click", save);
  els.cancel.addEventListener("click", requestClose);
  dlg.querySelector("[data-pm-close]").addEventListener("click", requestClose);
  dlg.querySelector("[data-pm-add]").addEventListener("click", addNew);
  els.form.addEventListener("submit", function (e) { e.preventDefault(); });

  /* ---------- Startseite aktualisieren ---------- */
  function renderHome(all) {
    var host = document.getElementById("chat-home-prompts");
    if (!host) { return; }
    host.textContent = "";
    var shown = all.filter(function (p) { return p.show_on_home; }).slice(0, limit);
    if (!shown.length) {
      host.appendChild(el("p", "chat-empty-state__no-prompts",
        "Es sind keine Standard-Prompts auf der Startseite aktiviert. Über „Standard-Prompts verwalten“ können Sie bis zu " + limit + " auswählen."));
      return;
    }
    shown.forEach(function (p) {
      var t = tint(p.icon);
      var b = el("button", "chat-quick-action chat-quick-action--card chat-quick-action--tint-" + t);
      b.type = "button";
      b.setAttribute("data-prefill", p.content);
      if (p.attach_on_use) { b.setAttribute("data-attach", "true"); }
      var ic = el("span", "chat-quick-action__icon chat-quick-action__icon--" + t);
      ic.innerHTML = (icons[p.icon] || icons.document).svg;
      b.appendChild(ic);
      b.appendChild(el("span", "chat-quick-action__title", p.name));
      b.appendChild(el("span", "chat-quick-action__subtitle", p.description));
      host.appendChild(b);
    });
  }

  document.querySelectorAll("[data-prompt-manager-open]").forEach(function (b) {
    b.addEventListener("click", function () { open(b); });
  });
})();
