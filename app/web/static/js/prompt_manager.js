/* "Standard-Prompts verwalten" (28.10.2026) - siehe partials/prompt_manager.html.
 *
 * Alle Aenderungen (Anlegen, Bearbeiten, Loeschen, Aktivieren, Sortieren) werden hier gesammelt und mit "Speichern"
 * in EINEM Aufruf an POST /dashboard/library/prompts/manage uebergeben; der Server prueft alles (Viererlimit,
 * Pflichtfelder, Laengen) und schreibt erst danach. "Abbrechen"/Schliessen verwirft - mit Rueckfrage, wenn etwas
 * geaendert wurde. Inhalte ausschliesslich per textContent.
 */
(function () {
  "use strict";

  var dialog = document.getElementById("prompt-manager");
  if (!dialog) { return; }
  var $ = function (sel) { return dialog.querySelector(sel); };
  var listEl = $("[data-pm-list]");
  var countEl = $("[data-pm-count]");
  var limitMsg = $("[data-pm-limit-msg]");
  var emptyEl = $("[data-pm-empty]");
  var form = $("[data-pm-form]");
  var formTitle = $("[data-pm-form-title]");
  var formSub = $("[data-pm-form-sub]");
  var deleteBtn = $("[data-pm-delete]");
  var formSwitch = $("[data-pm-form-switch]");
  var switchNote = $("[data-pm-switch-note]");
  var actionNote = $("[data-pm-action-note]");
  var iconBtn = $("[data-pm-icon-btn]");
  var iconCurrent = $("[data-pm-icon-current]");
  var iconMenu = $("[data-pm-icon-menu]");
  var addBtn = $("[data-pm-add]");
  var saveBtn = $("[data-pm-save]");
  var statusEl = $("[data-pm-status]");
  var readonlyEl = $("[data-pm-readonly]");
  var confirmEl = $("[data-pm-confirm]");

  var state = { items: [], deleted: [], snapshot: "", selected: null, max: 4, limits: {}, icons: [], canEdit: false,
                savedOnce: false, busy: false };
  var keySeq = 0;

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) { n.className = cls; }
    if (text !== undefined && text !== null) { n.textContent = text; }
    return n;
  }
  function iconNode(key) {
    var t = dialog.querySelector("template[data-pm-icon=\"" + key + "\"]") || dialog.querySelector("template[data-pm-icon]");
    return t.content.cloneNode(true);
  }
  function tintOf(key) {
    var t = dialog.querySelector("template[data-pm-icon=\"" + key + "\"]");
    return t ? t.getAttribute("data-tint") : "green";
  }
  function uiIcon(name) { return dialog.querySelector("template[data-pm-icon-ui=\"" + name + "\"]").content.cloneNode(true); }
  function csrf() {
    var t = dialog.getAttribute("data-csrf");
    if (t) { return t; }
    var f = document.querySelector("input[name=\"csrf_token\"]");
    return f ? f.value : "";
  }

  function stateJson() {
    return JSON.stringify({ items: state.items.map(function (i) {
      return [i.id, i.name, i.description, i.content, i.icon, i.show_on_start];
    }), deleted: state.deleted });
  }
  function isDirty() { return stateJson() !== state.snapshot; }
  function activeCount() { return state.items.filter(function (i) { return i.show_on_start; }).length; }
  function byKey(key) { return state.items.filter(function (i) { return i.key === key; })[0] || null; }

  /** Aktivierte zuerst (Reihenfolge = Startseite), dann die uebrigen. */
  function normalizeOrder() {
    state.items = state.items.filter(function (i) { return i.show_on_start; })
      .concat(state.items.filter(function (i) { return !i.show_on_start; }));
  }

  function load(data) {
    state.items = data.items.map(function (i) {
      keySeq += 1;
      return { key: i.id || ("neu-" + keySeq), id: i.id, name: i.name, description: i.description, content: i.content,
               icon: i.icon, show_on_start: i.show_on_start, action: i.action };
    });
    state.deleted = [];
    if (data.max_start) { state.max = data.max_start; }
    if (data.limits) { state.limits = data.limits; }
    if (data.icons) { state.icons = data.icons; }
    if (data.can_edit !== undefined) { state.canEdit = data.can_edit; }
    normalizeOrder();
    state.snapshot = stateJson();
  }

  function showLimit(text) {
    limitMsg.textContent = text;
    limitMsg.hidden = !text;
  }
  var limitText = function () {
    return "Bereits " + state.max + " von " + state.max + " Startplätzen belegt. Bitte zuerst einen anderen Prompt " +
           "deaktivieren, um diesen auf der Startseite anzuzeigen.";
  };

  function setActive(item, on) {
    if (on === item.show_on_start) { return true; }
    if (on && activeCount() >= state.max) { showLimit(limitText()); return false; }
    showLimit("");
    // aktiviert: ans Ende der aktivierten; deaktiviert: an den Anfang der uebrigen (normalizeOrder)
    var idx = state.items.indexOf(item);
    state.items.splice(idx, 1);
    item.show_on_start = on;
    var lastActive = -1;
    state.items.forEach(function (i, n) { if (i.show_on_start) { lastActive = n; } });
    state.items.splice(lastActive + 1, 0, item);
    return true;
  }

  // --- Liste -------------------------------------------------------------------------------------------------------
  var dragKey = null;

  function renderList() {
    listEl.textContent = "";
    var active = activeCount();
    countEl.textContent = "(" + active + " von " + state.items.length + " aktiv auf Startseite)";
    countEl.title = active + "/" + state.max + " Startplätze belegt";
    var group = null;
    state.items.forEach(function (item, index) {
      // ein gerade neu angelegter Prompt erscheint nicht zusaetzlich als Zeile - die hervorgehobene Kachel
      // "Neuen Prompt hinzufuegen" steht fuer ihn (Referenzbild); sobald ein anderer gewaehlt wird, erscheint er
      if (!item.id && state.selected === item.key) { return; }
      var g = item.show_on_start ? "active" : "inactive";
      if (group !== g) {
        if (group !== null) { listEl.appendChild(el("div", "pm__sep")); }
        group = g;
      }
      var row = el("div", "pm__row-item pm__row-item--" + tintOf(item.icon) + (item.show_on_start ? " is-on" : "") +
                         (state.selected === item.key ? " is-selected" : ""));
      row.setAttribute("data-key", item.key);
      row.setAttribute("data-group", g);
      if (state.selected === item.key) { row.setAttribute("aria-current", "true"); }

      var grip = el("button", "pm__grip");
      grip.type = "button";
      grip.title = "Ziehen zum Sortieren (Pfeiltasten: verschieben)";
      grip.setAttribute("aria-label", "„" + (item.name || "Neuer Prompt") + "“ verschieben");
      grip.disabled = !state.canEdit;
      grip.appendChild(uiIcon("grip"));
      grip.addEventListener("keydown", function (evt) {
        if (evt.key !== "ArrowUp" && evt.key !== "ArrowDown") { return; }
        evt.preventDefault();
        moveWithinGroup(item.key, evt.key === "ArrowUp" ? -1 : 1);
        var again = listEl.querySelector("[data-key=\"" + item.key + "\"] .pm__grip");
        if (again) { again.focus(); }
      });
      row.appendChild(grip);
      if (state.canEdit) {
        grip.addEventListener("mousedown", function () { row.draggable = true; });
        row.addEventListener("dragstart", function (evt) {
          dragKey = item.key;
          row.classList.add("is-dragging");
          evt.dataTransfer.effectAllowed = "move";
          try { evt.dataTransfer.setData("text/plain", item.key); } catch (e) { /* ignorieren */ }
        });
        row.addEventListener("dragend", function () { row.draggable = false; dragKey = null; renderList(); });
        row.addEventListener("dragover", function (evt) {
          var src = byKey(dragKey);
          if (!src || src.show_on_start !== item.show_on_start || src === item) { return; }
          evt.preventDefault();
          var rect = row.getBoundingClientRect();
          var after = evt.clientY > rect.top + rect.height / 2;
          state.items.splice(state.items.indexOf(src), 1);
          state.items.splice(state.items.indexOf(item) + (after ? 1 : 0), 0, src);
          renderListKeepDrag();
        });
      }

      var ic = el("span", "pm__row-icon pm__tint--" + tintOf(item.icon));
      ic.appendChild(iconNode(item.icon));
      row.appendChild(ic);
      var text = el("span", "pm__row-text");
      text.appendChild(el("strong", "pm__row-title", item.name || "Neuer Prompt"));
      text.appendChild(el("span", "pm__row-desc", item.description || ""));
      row.appendChild(text);

      var sw = el("span", "pm__row-switch");
      var btn = el("button", "pm__switch" + (item.show_on_start ? " is-on" : ""));
      btn.type = "button";
      btn.setAttribute("role", "switch");
      btn.setAttribute("aria-checked", item.show_on_start ? "true" : "false");
      btn.setAttribute("aria-label", "„" + (item.name || "Neuer Prompt") + "“ auf der Startseite anzeigen");
      btn.disabled = !state.canEdit;
      btn.appendChild(el("span"));
      btn.addEventListener("click", function (evt) {
        evt.stopPropagation();
        if (setActive(item, !item.show_on_start)) { renderAll(); }
        else { btn.classList.add("is-denied"); setTimeout(function () { btn.classList.remove("is-denied"); }, 600); }
      });
      sw.appendChild(btn);
      sw.appendChild(el("small", "pm__switch-caption", item.show_on_start ? "Startseite" : "Nicht auf Startseite"));
      row.appendChild(sw);

      if (state.canEdit) {
        var menuWrap = el("span", "pm__menu-wrap");
        var dots = el("button", "pm__dots");
        dots.type = "button";
        dots.setAttribute("aria-label", "Aktionen für „" + (item.name || "Neuer Prompt") + "“");
        dots.setAttribute("aria-haspopup", "menu");
        dots.appendChild(uiIcon("dots"));
        var menu = el("div", "pm__menu");
        menu.setAttribute("role", "menu");
        menu.hidden = true;
        var mEdit = el("button", "pm__menu-item", "Bearbeiten");
        mEdit.type = "button"; mEdit.setAttribute("role", "menuitem");
        mEdit.addEventListener("click", function (evt) { evt.stopPropagation(); closeMenus(); select(item.key); });
        var mDel = el("button", "pm__menu-item pm__menu-item--danger", "Löschen");
        mDel.type = "button"; mDel.setAttribute("role", "menuitem");
        mDel.addEventListener("click", function (evt) { evt.stopPropagation(); closeMenus(); askDelete(item); });
        menu.appendChild(mEdit);
        menu.appendChild(mDel);
        dots.addEventListener("click", function (evt) {
          evt.stopPropagation();
          var open = menu.hidden;
          closeMenus();
          menu.hidden = !open;
          dots.setAttribute("aria-expanded", open ? "true" : "false");
        });
        menuWrap.appendChild(dots);
        menuWrap.appendChild(menu);
        row.appendChild(menuWrap);
      }
      row.addEventListener("click", function (evt) {
        if (evt.target.closest("button")) { return; }
        select(item.key);
      });
      listEl.appendChild(row);
    });
    addBtn.classList.toggle("is-selected", !!(state.selected && byKey(state.selected) && !byKey(state.selected).id));
  }

  function renderListKeepDrag() {
    renderList();
    var row = listEl.querySelector("[data-key=\"" + dragKey + "\"]");
    if (row) { row.classList.add("is-dragging"); }
  }

  function moveWithinGroup(key, delta) {
    var item = byKey(key);
    var idx = state.items.indexOf(item);
    var target = idx + delta;
    if (target < 0 || target >= state.items.length) { return; }
    if (state.items[target].show_on_start !== item.show_on_start) { return; }
    state.items.splice(idx, 1);
    state.items.splice(target, 0, item);
    renderAll();
  }

  function closeMenus() {
    dialog.querySelectorAll(".pm__menu").forEach(function (m) { m.hidden = true; });
    dialog.querySelectorAll(".pm__dots").forEach(function (d) { d.setAttribute("aria-expanded", "false"); });
    iconMenu.hidden = true;
    iconBtn.setAttribute("aria-expanded", "false");
  }

  // --- Formular ----------------------------------------------------------------------------------------------------
  var fields = {};
  dialog.querySelectorAll("[data-pm-field]").forEach(function (f) { fields[f.getAttribute("data-pm-field")] = f; });

  function select(key) {
    state.selected = key;
    clearErrors();
    renderAll();
    var f = fields.name;
    if (f && !f.readOnly) { f.focus({ preventScroll: true }); }
  }

  function renderForm() {
    var item = state.selected ? byKey(state.selected) : null;
    emptyEl.hidden = !!item;
    form.hidden = !item;
    if (!item) { return; }
    var isNew = !item.id;
    formTitle.textContent = isNew ? "Neuen Prompt erstellen" : "Prompt bearbeiten";
    formSub.textContent = isNew ? "Geben Sie hier die Details für Ihren neuen Prompt ein."
                                : "Änderungen werden mit „Speichern“ übernommen.";
    deleteBtn.hidden = !state.canEdit || isNew;  // neu & ungespeichert: "Abbrechen" verwirft
    ["name", "description", "content"].forEach(function (name) {
      var f = fields[name];
      if (f.value !== item[name]) { f.value = item[name]; }
      f.readOnly = !state.canEdit;
      if (state.limits[name]) { f.setAttribute("maxlength", String(Math.max(state.limits[name], (item[name] || "").length))); }
      updateCounter(name);
    });
    iconCurrent.textContent = "";
    iconCurrent.appendChild(iconNode(item.icon));
    iconCurrent.className = "pm__icon-current pm__tint--" + tintOf(item.icon);
    iconBtn.disabled = !state.canEdit;
    formSwitch.classList.toggle("is-on", item.show_on_start);
    formSwitch.setAttribute("aria-checked", item.show_on_start ? "true" : "false");
    var full = !item.show_on_start && activeCount() >= state.max;
    formSwitch.disabled = !state.canEdit || full;
    switchNote.hidden = !full;
    switchNote.textContent = full ? "Aktuell " + state.max + " von " + state.max + " Prompts ausgewählt. Bitte einen " +
      "bestehenden Prompt deaktivieren, um diesen anzuzeigen." : "";
    actionNote.hidden = item.action !== "open_matter" && item.action !== "prefill_attach";
    actionNote.textContent = item.action === "open_matter"
      ? "Diese Karte öffnet auf der Startseite die Aktenauswahl; der Chat beginnt mit der gewählten Akte."
      : (item.action === "prefill_attach" ? "Diese Karte übernimmt die Anweisung ins Eingabefeld und öffnet die Dateiauswahl." : "");
  }

  function updateCounter(name) {
    var c = dialog.querySelector("[data-pm-counter=\"" + name + "\"]");
    if (c && state.limits[name]) { c.textContent = fields[name].value.length + "/" + state.limits[name]; }
  }

  Object.keys(fields).forEach(function (name) {
    fields[name].addEventListener("input", function () {
      var item = byKey(state.selected);
      if (!item) { return; }
      item[name] = fields[name].value;
      updateCounter(name);
      clearError(name);
      if (name !== "content") { renderList(); }
      updateFooter();
    });
  });

  formSwitch.addEventListener("click", function () {
    var item = byKey(state.selected);
    if (item && setActive(item, !item.show_on_start)) { renderAll(); }
  });

  iconBtn.addEventListener("click", function (evt) {
    evt.stopPropagation();
    var open = iconMenu.hidden;
    closeMenus();
    if (!open) { return; }
    iconMenu.textContent = "";
    var item = byKey(state.selected);
    state.icons.forEach(function (key) {
      var b = el("button", "pm__icon-option pm__tint--" + tintOf(key) + (item && item.icon === key ? " is-current" : ""));
      b.type = "button";
      b.setAttribute("role", "option");
      b.setAttribute("aria-selected", item && item.icon === key ? "true" : "false");
      b.setAttribute("aria-label", key);
      b.appendChild(iconNode(key));
      b.addEventListener("click", function (e) {
        e.stopPropagation();
        if (item) { item.icon = key; }
        closeMenus();
        renderAll();
      });
      iconMenu.appendChild(b);
    });
    iconMenu.hidden = false;
    iconBtn.setAttribute("aria-expanded", "true");
  });

  addBtn.addEventListener("click", function () {
    keySeq += 1;
    var item = { key: "neu-" + keySeq, id: null, name: "", description: "", content: "", icon: "document",
                 show_on_start: false, action: "prefill" };
    state.items.push(item);
    select(item.key);
  });

  deleteBtn.addEventListener("click", function () {
    var item = byKey(state.selected);
    if (item) { askDelete(item); }
  });

  function askDelete(item) {
    confirmBox("Prompt „" + (item.name || "Neuer Prompt") + "“ löschen? Die Löschung wird mit „Speichern“ übernommen.",
               "Abbrechen", "Löschen", function () {
      state.items.splice(state.items.indexOf(item), 1);
      if (item.id) { state.deleted.push(item.id); }
      if (state.selected === item.key) { state.selected = null; }
      showLimit("");
      renderAll();
    });
  }

  function clearErrors() { ["name", "description", "content"].forEach(clearError); }
  function clearError(name) {
    var e = dialog.querySelector("[data-pm-error=\"" + name + "\"]");
    if (e) { e.textContent = ""; }
    if (fields[name]) { fields[name].classList.remove("is-invalid"); fields[name].removeAttribute("aria-invalid"); }
  }
  function showError(key, field, text) {
    if (key && byKey(key)) { state.selected = key; renderAll(); }
    if (field && fields[field]) {
      var e = dialog.querySelector("[data-pm-error=\"" + field + "\"]");
      if (e) { e.textContent = text; }
      fields[field].classList.add("is-invalid");
      fields[field].setAttribute("aria-invalid", "true");
      fields[field].focus();
    } else if (field === "show_on_start") {
      showLimit(text);
    }
    setStatus(text, "error");
  }

  /** Clientseitige Vorpruefung (der Server prueft identisch erneut). */
  function validate() {
    for (var i = 0; i < state.items.length; i += 1) {
      var it = state.items[i];
      var lim = state.limits;
      if (!it.name.trim()) { return [it.key, "name", "Bitte einen Titel eingeben."]; }
      if (!it.id && it.name.trim().length > lim.name) { return [it.key, "name", "Der Titel ist zu lang."]; }
      if (!it.id && !it.description.trim()) { return [it.key, "description", "Bitte eine Beschreibung eingeben."]; }
      if (!it.content.trim()) { return [it.key, "content", "Bitte eine Prompt-Anweisung eingeben."]; }
    }
    if (activeCount() > state.max) { return [null, "show_on_start", limitText()]; }
    return null;
  }

  // --- Fusszeile, Speichern, Schliessen ----------------------------------------------------------------------------
  function setStatus(text, kind) {
    statusEl.textContent = text || "";
    statusEl.className = "pm__status" + (kind ? " pm__status--" + kind : "");
  }
  function updateFooter() {
    saveBtn.hidden = !state.canEdit;
    saveBtn.disabled = state.busy || !isDirty();
    if (isDirty() && !state.busy && statusEl.className.indexOf("error") === -1) { setStatus("Ungespeicherte Änderungen", "dirty"); }
    else if (!isDirty() && statusEl.className.indexOf("dirty") !== -1) { setStatus(""); }
  }
  function renderAll() {
    renderList();
    renderForm();
    readonlyEl.hidden = state.canEdit;
    addBtn.hidden = !state.canEdit;
    updateFooter();
  }

  saveBtn.addEventListener("click", function () {
    if (saveBtn.disabled) { return; }
    clearErrors();
    var problem = validate();
    if (problem) { showError(problem[0], problem[1], problem[2]); return; }
    var selected = byKey(state.selected);
    var payload = { items: state.items.map(function (i) {
      return { id: i.id, key: i.key, name: i.name, description: i.description, content: i.content, icon: i.icon,
               show_on_start: i.show_on_start };
    }), deleted: state.deleted };
    var body = new FormData();
    body.append("csrf_token", csrf());
    body.append("payload", JSON.stringify(payload));
    state.busy = true;
    updateFooter();
    setStatus("Wird gespeichert …");
    fetch("/dashboard/library/prompts/manage", { method: "POST", body: body, credentials: "same-origin" })
      .then(function (r) { return r.json().then(function (data) { return { ok: r.ok, data: data }; }); })
      .then(function (res) {
        state.busy = false;
        if (!res.ok || !res.data.ok) {
          var d = res.data || {};
          showError(d.key, d.field, d.error || "Speichern fehlgeschlagen. Es wurde nichts geändert.");
          updateFooter();
          return;
        }
        var keepName = selected ? selected.name : null;
        load({ items: res.data.items });
        var again = keepName ? state.items.filter(function (i) { return i.name === keepName; })[0] : null;
        state.selected = again ? again.key : null;
        state.savedOnce = true;
        renderAll();
        setStatus("Gespeichert", "ok");
        setTimeout(function () { if (statusEl.className.indexOf("ok") !== -1) { setStatus(""); } }, 2500);
      })
      .catch(function () {
        state.busy = false;
        setStatus("Verbindung unterbrochen – es wurde nichts gespeichert.", "error");
        updateFooter();
      });
  });

  var confirmCallback = null;
  function confirmBox(text, noLabel, yesLabel, onYes) {
    $("[data-pm-confirm-text]").textContent = text;
    $("[data-pm-confirm-no]").textContent = noLabel;
    $("[data-pm-confirm-yes]").textContent = yesLabel;
    confirmCallback = onYes;
    confirmEl.hidden = false;
    $("[data-pm-confirm-no]").focus();
  }
  $("[data-pm-confirm-no]").addEventListener("click", function () { confirmEl.hidden = true; confirmCallback = null; });
  $("[data-pm-confirm-yes]").addEventListener("click", function () {
    confirmEl.hidden = true;
    var cb = confirmCallback;
    confirmCallback = null;
    if (cb) { cb(); }
  });

  function finishClose() {
    if (dialog.open) { dialog.close(); }
    // Startseite zeigt die gespeicherte Auswahl/Reihenfolge
    if (state.savedOnce) { window.location.reload(); }
  }
  function requestClose() {
    closeMenus();
    if (!confirmEl.hidden) { confirmEl.hidden = true; return; }
    if (isDirty()) {
      confirmBox("Ungespeicherte Änderungen verwerfen?", "Weiter bearbeiten", "Verwerfen", finishClose);
      return;
    }
    finishClose();
  }
  $("[data-pm-close]").addEventListener("click", requestClose);
  $("[data-pm-cancel]").addEventListener("click", requestClose);
  dialog.addEventListener("cancel", function (evt) { evt.preventDefault(); requestClose(); });
  dialog.addEventListener("click", function (evt) {
    if (!evt.target.closest(".pm__menu-wrap") && !evt.target.closest(".pm__icon-select")) { closeMenus(); }
  });
  window.addEventListener("beforeunload", function (evt) {
    if (dialog.open && isDirty()) { evt.preventDefault(); evt.returnValue = ""; }
  });

  function openManager(evt) {
    if (evt) { evt.preventDefault(); }
    setStatus("Wird geladen …");
    fetch("/dashboard/library/prompts/manage.json", { credentials: "same-origin" })
      .then(function (r) { if (!r.ok) { throw new Error("load"); } return r.json(); })
      .then(function (data) {
        load(data);
        state.selected = null;  // beim Oeffnen ist kein Prompt ausgewaehlt (leerer Zustand)
        state.savedOnce = false;
        showLimit("");
        clearErrors();
        setStatus("");
        renderAll();
        if (!dialog.open) { dialog.showModal(); }
        $("#pm-title").focus({ preventScroll: true });
      })
      .catch(function () { window.location.href = "/dashboard/library/prompts"; });
  }
  document.querySelectorAll("[data-prompt-manager-open]").forEach(function (a) { a.addEventListener("click", openManager); });

  window.LexonoPromptManager = { open: openManager, isOpen: function () { return dialog.open; } };
})();
