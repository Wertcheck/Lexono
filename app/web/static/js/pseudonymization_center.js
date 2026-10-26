/* Zentraler Pseudonymisierungsdialog (25./26.10.2026) - siehe partials/pseudonymization_center.html.
 *
 * EIN Pop-up ueber dem Chat fuer:
 *  - "release": Dokumentanalyse / Schriftsatz am Dokument-Gate - Dokumentauswahl, Rueckfragen ueber alle Dokumente,
 *    Bestaetigen ueber POST .../confirm-release-and-generate (serverseitige Pruefung: Textstaende, nur aktuell offene
 *    Rueckfragen, alle beantwortet; danach fail-closed Wiederaufnahme).
 *  - "review": Datenschutz-Rueckfragen beim Senden (vorher eigener HITL-Dialog) - Fragen + Dokumentvorschau mit den
 *    Fundstellen; Antworten gehen an den unveraenderten Server-Pfad (privacy-review).
 *  - "view": reine Pruefansicht ohne offenen Auftrag (mit Schnellaktionen, die NUR das Chat-Eingabefeld vorbelegen).
 *
 * Regeln: Dokumentinhalte sind untrusted input (nur textContent). Entscheidungen bleiben bis "Bestaetigen" im Dialog
 * (auch ueber Dokument- und Ansichtswechsel); Abbrechen speichert und sendet nichts. Bestaetigen ist erst moeglich,
 * wenn jede Rueckfrage beantwortet ist - der Server prueft das beim Bestaetigen erneut.
 *
 * 26.10.2026 UX-Ueberarbeitung: Dokument-Tabs mit Typ und offenen Rueckfragen (+ Auswahlliste ab 5 Dokumenten),
 * Seitenuebersicht, "Breite", Maximieren, Fortschritt mit Fragenavigation, kompakte Fragekarten mit Kategorie und
 * Seitenbezug, Statusleiste in der Fusszeile.
 */
(function () {
  "use strict";

  var dialog = document.getElementById("pseudonymization-center");
  if (!dialog) { return; }

  var $ = function (sel) { return dialog.querySelector(sel); };
  var docPane = $("[data-pseudo-document]");
  var docTabs = $("[data-pseudo-doc-tabs]");
  var docSelectWrap = $("[data-pseudo-doc-select-wrap]");
  var docSelect = $("[data-pseudo-doc-select]");
  var thumbs = $("[data-pseudo-thumbs]");
  var thumbsToggle = $("[data-pseudo-thumbs-toggle]");
  var maximizeBtn = $("[data-pseudo-maximize]");
  var statusBox = $("[data-pseudo-status]");
  var progressBox = $("[data-pseudo-progress]");
  var progressText = $("[data-pseudo-progress-text]");
  var progressPercent = $("[data-pseudo-progress-percent]");
  var progressBar = $("[data-pseudo-progress-bar]");
  var progressFill = $("[data-pseudo-progress-fill]");
  var qPrev = $("[data-pseudo-q-prev]");
  var qNext = $("[data-pseudo-q-next]");
  var panelScroll = $("[data-pseudo-panel-scroll]");
  var questionsBox = $("[data-pseudo-questions]");
  var followupBox = $("[data-pseudo-followup]");
  var mappingsWrap = $("[data-pseudo-mappings-wrap]");
  var mappingsList = $("[data-pseudo-mappings]");
  var mappingCount = $("[data-pseudo-mapping-count]");
  var quickBox = $("[data-pseudo-quick]");
  var quickActions = $("[data-pseudo-quick-actions]");
  var limitsBox = $("[data-pseudo-limits]");
  var noticeBox = $("[data-pseudo-notice]");
  var footerStatus = $("[data-pseudo-footer-status]");
  var openCount = $("[data-pseudo-open-count]");
  var confirmBtn = $("[data-pseudo-center-confirm]");
  var subtitle = $("[data-pseudo-subtitle]");
  var pageInfo = $("[data-pseudo-page-info]");
  var zoomLabel = $("[data-pseudo-zoom]");
  var protectForm = $("[data-pseudo-protect-form]");
  var protectInput = $("[data-pseudo-protect-input]");

  var OPTIONS = [
    { decision: "personal", title: "Pseudonymisieren", hint: "als Person/sensibel behandeln" },
    { decision: "not_personal", title: "Nicht pseudonymisieren", hint: "kein personenbezogenes Datum" },
    { decision: "unsure", title: "Unsicher", hint: "vorsichtshalber pseudonymisieren" }
  ];

  var CATEGORY_LABELS = {
    person: "Person", organisation: "Organisation", organization: "Organisation", ort: "Ort", adresse: "Anschrift",
    anschrift: "Anschrift", kennung: "Kennnummer", aktenzeichen: "Aktenzeichen", email: "E-Mail", telefon: "Telefon",
    iban: "IBAN", datum: "Datum", geburtsdatum: "Geburtsdatum"
  };

  var MANY_DOCS = 5; // ab dieser Anzahl zusaetzlich die Auswahlliste

  var state = {
    conversationId: dialog.getAttribute("data-conversation-id") || "",
    matterId: dialog.getAttribute("data-matter-id") || "",
    canManage: dialog.getAttribute("data-can-manage") === "1",
    mode: "view", docs: [], order: { label: "", has_open_order: false }, currentId: null, reviews: {},
    decisions: {}, view: "marked", zoom: 1, busy: false, payload: null, callback: null, generation: 0,
    activeTerm: null, thumbs: false, maximized: false
  };

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) { node.className = className; }
    if (text !== undefined && text !== null) { node.textContent = text; }
    return node;
  }

  function csrfToken() {
    // erstes NICHT leeres Feld (das Sperr-Formular im Seitenkopf traegt ein leeres csrf_token)
    var fields = document.querySelectorAll("input[name=\"csrf_token\"]");
    for (var i = 0; i < fields.length; i += 1) { if (fields[i].value) { return fields[i].value; } }
    return "";
  }

  function cssEscape(value) { return window.CSS && CSS.escape ? CSS.escape(value) : value.replace(/"/g, "\\\""); }

  function fileType(name) {
    var m = /\.([A-Za-z0-9]{1,5})$/.exec(name || "");
    return m ? m[1].toUpperCase() : "TEXT";
  }

  function categoryLabel(category) {
    return CATEGORY_LABELS[(category || "").toLowerCase()] || "Unsicherer Begriff";
  }

  // --- Fragen ---------------------------------------------------------------------------------------------------

  function questions() {
    if (state.mode === "review" && state.payload) {
      return state.payload.candidates.map(function (c) {
        return { term: c.term, context_before: c.context_before, context_after: c.context_after,
                 docs: docsWithMark(c.term), fromChat: true };
      });
    }
    var byTerm = {}, list = [];
    state.docs.forEach(function (doc) {
      var data = state.reviews[doc.id];
      if (!data || !data.candidates) { return; }
      data.candidates.forEach(function (c) {
        if (!byTerm[c.term]) {
          byTerm[c.term] = { term: c.term, context_before: c.context_before, context_after: c.context_after, docs: [] };
          list.push(byTerm[c.term]);
        }
        if (byTerm[c.term].docs.indexOf(doc.id) === -1) { byTerm[c.term].docs.push(doc.id); }
      });
    });
    return list;
  }

  function docsWithMark(term) {
    return state.docs.filter(function (doc) {
      var data = state.reviews[doc.id];
      return data && data.pages && data.pages.some(function (p) {
        return p.marks.some(function (m) { return m.term === term && m.kind === "open"; });
      });
    }).map(function (doc) { return doc.id; });
  }

  /** Seitennummern, auf denen `term` in Dokument `id` als Rueckfrage markiert ist. */
  function pagesWithTerm(id, term) {
    var data = state.reviews[id];
    if (!data || !data.pages) { return []; }
    return data.pages.filter(function (p) {
      return p.marks.some(function (m) { return m.term === term && m.kind === "open"; });
    }).map(function (p) { return p.number; });
  }

  /** Kategorie aus den erkannten Funden, falls der Begriff dort (vorsichtig) bereits zugeordnet ist. */
  function questionCategory(term) {
    var found = null;
    state.docs.forEach(function (doc) {
      var data = state.reviews[doc.id];
      (data && data.mappings || []).forEach(function (m) {
        if (!found && (m.original === term || (m.variants || []).indexOf(term) !== -1)) { found = m.category; }
      });
    });
    return categoryLabel(found);
  }

  function openTerms() {
    return questions().map(function (q) { return q.term; }).filter(function (t) { return !state.decisions[t]; });
  }

  function decisionKind(term) {
    var d = state.decisions[term];
    if (!d) { return "open"; }
    return d === "not_personal" ? "kept" : "masked";
  }

  function docName(id) {
    var doc = state.docs.filter(function (d) { return d.id === id; })[0];
    return doc ? doc.filename : "Dokument";
  }

  function openForDoc(id) {
    return questions().filter(function (q) { return q.docs.indexOf(id) !== -1 && !state.decisions[q.term]; }).length;
  }

  // --- Dokumentauswahl -------------------------------------------------------------------------------------------

  function renderDocTabs() {
    docTabs.textContent = "";
    state.docs.forEach(function (doc) {
      var active = doc.id === state.currentId;
      var tab = el("button", "pseudo-center__doc-tab" + (active ? " is-active" : ""));
      tab.type = "button";
      tab.setAttribute("role", "tab");
      tab.setAttribute("aria-selected", active ? "true" : "false");
      tab.setAttribute("data-doc-id", doc.id);
      tab.title = doc.filename;
      tab.appendChild(el("span", "pseudo-center__doc-type pseudo-center__doc-type--" + fileType(doc.filename).toLowerCase(),
                         fileType(doc.filename)));
      tab.appendChild(el("span", "pseudo-center__doc-tab-name", doc.filename));
      var open = openForDoc(doc.id);
      if (open) {
        var badge = el("span", "pseudo-center__doc-badge", String(open));
        badge.title = open + " offene Rückfrage(n)";
        tab.appendChild(badge);
      } else if (state.reviews[doc.id]) {
        var ok = el("span", "pseudo-center__doc-ok", "✓");
        ok.title = "keine offene Rückfrage";
        tab.appendChild(ok);
      } else {
        tab.appendChild(el("span", "pseudo-center__doc-wait", "…"));
      }
      tab.addEventListener("click", function () { selectDoc(doc.id); });
      docTabs.appendChild(tab);
    });
    var active = docTabs.querySelector(".is-active");
    if (active && active.scrollIntoView) { active.scrollIntoView({ block: "nearest", inline: "nearest" }); }
    // Viele Dokumente: zusaetzlich Auswahlliste (Tabs bleiben horizontal scrollbar)
    docSelectWrap.hidden = state.docs.length < MANY_DOCS;
    if (!docSelectWrap.hidden) {
      docSelect.textContent = "";
      state.docs.forEach(function (doc, index) {
        var open = openForDoc(doc.id);
        var option = el("option", "", (index + 1) + ". " + doc.filename + (open ? " – " + open + " offen" : ""));
        option.value = doc.id;
        option.selected = doc.id === state.currentId;
        docSelect.appendChild(option);
      });
    }
  }

  docSelect.addEventListener("change", function () { selectDoc(docSelect.value); });

  function selectDoc(id, thenTerm) {
    state.currentId = id;
    renderDocTabs();
    ensureReview(id).then(function () {
      if (state.currentId !== id) { return; }
      renderDocument();
      renderPanel();
      if (thenTerm) { scrollToMark(thenTerm); }
    });
  }

  // --- Dokumentansicht -------------------------------------------------------------------------------------------

  function markClass(mark) {
    var active = state.activeTerm && mark.term === state.activeTerm ? " is-active" : "";
    if (mark.kind === "open") { return "pseudo-mark pseudo-mark--" + decisionKind(mark.term) + active; }
    return "pseudo-mark pseudo-mark--masked";
  }

  function renderDocument() {
    docPane.textContent = "";
    var data = state.reviews[state.currentId];
    var doc = state.docs.filter(function (d) { return d.id === state.currentId; })[0];
    subtitle.textContent = data
      ? fileType(data.filename) + (data.pages && data.pages.length ? " · " + data.pages.length + " Seite(n)" : "") +
        " · " + data.mappings.length + " Fund(e)"
      : (state.docs.length ? "" : "Keine Dokumente in dieser Unterhaltung");
    if (doc) { subtitle.title = doc.filename; }
    if (!data) {
      docPane.appendChild(el("p", "pseudo-center__loading",
        state.docs.length ? "Dokument wird lokal geprüft …" : "Dieser Chat enthält keine Dokumente."));
      renderThumbs(null);
      updatePageInfo();
      return;
    }
    if (state.view === "result") { renderResult(data); renderThumbs(null); updatePageInfo(); return; }
    if (data.viewer_kind !== "pages" || !data.pages.length) { renderTextView(data); renderThumbs(null); updatePageInfo(); return; }
    var holder = el("div", "pseudo-center__page-stack");
    holder.style.width = Math.round(state.zoom * 100) + "%";
    data.pages.forEach(function (page) {
      var wrap = el("figure", "pseudo-page");
      wrap.setAttribute("data-page", String(page.number));
      var box = el("div", "pseudo-page__box");
      box.style.aspectRatio = page.width + " / " + page.height;
      var img = el("img", "pseudo-page__img");
      img.alt = "Seite " + page.number + " (Original)";
      img.loading = "lazy";
      img.src = data.page_url_base + page.number + ".png?dpi=" + (state.zoom > 1.2 ? 160 : 110);
      box.appendChild(img);
      if (state.view === "marked") {
        page.marks.forEach(function (mark) {
          var m = el("span", markClass(mark));
          m.style.left = (mark.x * 100) + "%";
          m.style.top = (mark.y * 100) + "%";
          m.style.width = (mark.w * 100) + "%";
          m.style.height = (mark.h * 100) + "%";
          m.setAttribute("data-term", mark.term);
          m.title = mark.kind === "open"
            ? (decisionKind(mark.term) === "open" ? "Rückfrage offen: " : "Entschieden: ") + mark.term
            : mark.placeholder + " (" + categoryLabel(mark.category) + ")";
          if (mark.kind === "open") { m.addEventListener("click", function () { activateQuestion(mark.term, true); }); }
          box.appendChild(m);
        });
      }
      wrap.appendChild(box);
      wrap.appendChild(el("figcaption", "pseudo-page__caption", "Seite " + page.number + " von " + data.pages.length));
      holder.appendChild(wrap);
    });
    docPane.appendChild(holder);
    renderThumbs(data);
    updatePageInfo();
  }

  function renderThumbs(data) {
    thumbs.textContent = "";
    var usable = data && data.viewer_kind === "pages" && data.pages.length > 0 && state.view !== "result";
    thumbsToggle.disabled = !usable;
    thumbs.hidden = !(usable && state.thumbs);
    thumbsToggle.setAttribute("aria-pressed", state.thumbs && usable ? "true" : "false");
    if (thumbs.hidden) { return; }
    data.pages.forEach(function (page) {
      var b = el("button", "pseudo-thumb");
      b.type = "button";
      b.setAttribute("data-thumb-page", String(page.number));
      b.setAttribute("aria-label", "Seite " + page.number);
      var frame = el("span", "pseudo-thumb__frame");
      frame.style.aspectRatio = page.width + " / " + page.height;
      var img = el("img", "pseudo-thumb__img");
      img.alt = "";
      img.loading = "lazy";
      img.src = data.page_url_base + page.number + ".png?dpi=40";
      frame.appendChild(img);
      var open = page.marks.filter(function (m) { return m.kind === "open" && !state.decisions[m.term]; }).length;
      if (open) { frame.appendChild(el("span", "pseudo-thumb__badge", String(open))); }
      b.appendChild(frame);
      b.appendChild(el("span", "pseudo-thumb__num", String(page.number)));
      b.addEventListener("click", function () { scrollToPage(page.number - 1); });
      thumbs.appendChild(b);
    });
  }

  function renderTextView(data) {
    var box = el("div", "pseudo-text");
    var text = data.original_text || "";
    if (state.view !== "marked") { box.textContent = text; docPane.appendChild(box); return; }
    var terms = {};
    data.mappings.forEach(function (m) { terms[m.original] = { kind: "masked", term: m.original }; });
    questions().forEach(function (q) { terms[q.term] = { kind: "open", term: q.term }; });
    var keys = Object.keys(terms).filter(Boolean).sort(function (a, b) { return b.length - a.length; });
    var pos = 0;
    while (pos < text.length) {
      var best = -1, bestKey = null;
      keys.forEach(function (k) {
        var i = text.indexOf(k, pos);
        if (i !== -1 && (best === -1 || i < best)) { best = i; bestKey = k; }
      });
      if (best === -1) { box.appendChild(document.createTextNode(text.slice(pos))); break; }
      box.appendChild(document.createTextNode(text.slice(pos, best)));
      var info = terms[bestKey];
      var mark = el("mark", "pseudo-mark-inline pseudo-mark--" + (info.kind === "open" ? decisionKind(info.term) : "masked"), bestKey);
      mark.setAttribute("data-term", info.term);
      box.appendChild(mark);
      pos = best + bestKey.length;
    }
    docPane.appendChild(box);
  }

  function renderResult(data) {
    docPane.appendChild(el("p", "pseudo-center__result-note",
      "Pseudonymisiertes Ergebnis dieses Dokuments – so würde der Text nach der Bestätigung geprüft und übermittelt. " +
      "Stand ohne Ihre noch nicht gespeicherten Entscheidungen; „Nicht pseudonymisieren“ macht einen Begriff erst " +
      "nach der Bestätigung lesbar, der Server prüft dann erneut."));
    docPane.appendChild(el("div", "pseudo-text pseudo-text--result",
      data.result_text || "Kein sendefähiges Ergebnis – siehe Prüfstatus rechts."));
  }

  function pagesList() { return Array.prototype.slice.call(docPane.querySelectorAll(".pseudo-page")); }

  /** Seite unter der Mitte des sichtbaren Bereichs (nicht die oberste angeschnittene). */
  function currentPageIndex() {
    var pages = pagesList();
    var rect = docPane.getBoundingClientRect();
    var middle = rect.top + Math.min(rect.height / 2, 240);
    for (var i = 0; i < pages.length; i += 1) {
      if (pages[i].getBoundingClientRect().bottom > middle) { return i; }
    }
    return Math.max(0, pages.length - 1);
  }

  /** Neu darstellen (andere Breite/Zoom/Seitenuebersicht), ohne die aktuelle Seite zu verlieren. */
  function rerenderKeepingPage(change) {
    var index = currentPageIndex();
    change();
    renderDocument();
    var pages = pagesList();
    if (pages[index]) { docPane.scrollTop = pages[index].offsetTop - 8; }
    updatePageInfo();
  }

  function updatePageInfo() {
    var pages = pagesList();
    var index = currentPageIndex();
    pageInfo.textContent = pages.length ? "Seite " + (index + 1) + " / " + pages.length : "–";
    zoomLabel.textContent = Math.round(state.zoom * 100) + " %";
    $("[data-pseudo-page-prev]").disabled = !pages.length || index === 0;
    $("[data-pseudo-page-next]").disabled = !pages.length || index >= pages.length - 1;
    if (!thumbs.hidden) {
      var current = null;
      thumbs.querySelectorAll(".pseudo-thumb").forEach(function (t) {
        var on = t.getAttribute("data-thumb-page") === String(index + 1);
        t.classList.toggle("is-current", on);
        if (on) { current = t; }
      });
      if (current) {
        var top = current.offsetTop, bottom = top + current.offsetHeight;
        if (top < thumbs.scrollTop || bottom > thumbs.scrollTop + thumbs.clientHeight) {
          thumbs.scrollTop = top - thumbs.clientHeight / 2 + current.offsetHeight / 2;
        }
      }
    }
  }

  function scrollToPage(target) {
    var pages = pagesList();
    if (!pages.length) { return; }
    target = Math.min(pages.length - 1, Math.max(0, target));
    docPane.scrollTo({ top: pages[target].offsetTop - 8, behavior: "smooth" });
    setTimeout(updatePageInfo, 400);
  }

  function goToPage(delta) { scrollToPage(currentPageIndex() + delta); }

  function setZoom(next) {
    rerenderKeepingPage(function () { state.zoom = Math.min(2, Math.max(0.6, Math.round(next * 10) / 10)); });
  }

  function scrollToMark(term) {
    var mark = docPane.querySelector("[data-term=\"" + cssEscape(term) + "\"]");
    if (mark) {
      mark.scrollIntoView({ block: "center", behavior: "smooth" });
      mark.classList.add("is-focused");
      setTimeout(function () { mark.classList.remove("is-focused"); }, 1600);
      setTimeout(updatePageInfo, 450);
    }
  }

  // --- Pruefung (rechts) -----------------------------------------------------------------------------------------

  function renderStatus() {
    statusBox.textContent = "";
    var data = state.reviews[state.currentId];
    var qs = questions();
    var text, cls;
    var anyBlocked = state.docs.some(function (d) { var r = state.reviews[d.id]; return r && (r.status === "blocked"); });
    if (anyBlocked) {
      cls = "pseudo-center__state pseudo-center__state--blocked";
      text = "Prüfung fehlgeschlagen – mindestens ein Dokument kann nicht verarbeitet werden.";
    } else if (data && data.status === "no_text") {
      cls = "pseudo-center__state pseudo-center__state--blocked";
      text = "Kein verwertbarer Text – keine Verarbeitung möglich.";
    } else if (qs.length) {
      cls = "pseudo-center__state pseudo-center__state--open";
      text = state.mode === "review" ? "Datenschutz-Rückfrage zu Ihrer Nachricht" : "Rückfragen zu unsicheren Begriffen";
    } else {
      cls = "pseudo-center__state pseudo-center__state--ok";
      text = state.docs.length && state.docs.every(function (d) { return state.reviews[d.id]; })
        ? "Keine offenen Rückfragen" : "Dokumente werden lokal geprüft …";
    }
    statusBox.appendChild(el("p", cls, text));
    if (state.mode === "view") {
      statusBox.appendChild(el("p", "form-hint", "Kein offener Auftrag – reine Prüfansicht. Stellen Sie Ihre Frage im " +
        "Chat; die Prüfung erfolgt beim Senden erneut."));
    }
    state.docs.forEach(function (d) {
      var r = state.reviews[d.id];
      (r && r.blocked_reasons || []).forEach(function (reason) { statusBox.appendChild(el("p", "form-hint", reason)); });
    });
  }

  function renderProgress() {
    var qs = questions();
    progressBox.hidden = !qs.length;
    if (!qs.length) { return; }
    var done = qs.length - openTerms().length;
    var percent = Math.round(done * 100 / qs.length);
    progressText.textContent = done + " von " + qs.length + " Rückfragen beantwortet";
    progressPercent.textContent = percent + " %";
    progressBar.setAttribute("aria-valuenow", String(percent));
    progressBar.setAttribute("aria-label", progressText.textContent);
    progressFill.style.width = percent + "%";
    var index = activeIndex(qs);
    qPrev.disabled = qs.length < 2 || index <= 0;
    qNext.disabled = !openTerms().some(function (t) { return t !== state.activeTerm; });
  }

  function activeIndex(qs) {
    for (var i = 0; i < qs.length; i += 1) { if (qs[i].term === state.activeTerm) { return i; } }
    return -1;
  }

  function renderQuestions() {
    questionsBox.textContent = "";
    var qs = questions();
    if (!qs.length) { return; }
    qs.forEach(function (q, index) {
      var kind = decisionKind(q.term);
      var item = el("article", "pseudo-question pseudo-question--" + kind + (q.term === state.activeTerm ? " is-active" : ""));
      item.setAttribute("data-question", q.term);
      item.addEventListener("click", function (evt) {
        if (evt.target.closest("button, summary, a")) { return; }
        activateQuestion(q.term, false, true);
      });
      var head = el("div", "pseudo-question__head");
      head.appendChild(el("span", "pseudo-question__index", (index + 1) + " / " + qs.length));
      head.appendChild(el("span", "pseudo-question__term", q.term));
      head.appendChild(el("span", "pseudo-question__state pseudo-question__state--" + kind,
        kind === "open" ? "offen" : (state.decisions[q.term] === "not_personal" ? "bleibt lesbar"
                                     : (state.decisions[q.term] === "unsure" ? "unsicher" : "pseudonymisiert"))));
      item.appendChild(head);
      var meta = el("div", "pseudo-question__meta");
      meta.appendChild(el("span", "pseudo-question__cat", questionCategory(q.term)));
      if (q.docs.length) {
        // aktuelles Dokument zuerst; bei vielen Dokumenten kompakt ("+ n weitere")
        var docs = q.docs.slice().sort(function (a, b) { return (b === state.currentId) - (a === state.currentId); });
        if (docs.length > 3) {
          meta.appendChild(el("span", "pseudo-question__more", "in " + docs.length + " Dokumenten"));
        }
        docs.slice(0, 2).concat(docs.length === 3 ? docs.slice(2) : []).forEach(function (id) {
          var pages = pagesWithTerm(id, q.term);
          var where = el("button", "pseudo-question__where",
            docName(id) + (pages.length ? " · S. " + pages.slice(0, 3).join(", ") + (pages.length > 3 ? " …" : "") : ""));
          where.type = "button";
          where.title = "Zur Fundstelle springen";
          where.addEventListener("click", function () {
            state.activeTerm = q.term;
            if (id !== state.currentId) { selectDoc(id, q.term); }
            else { if (state.view !== "marked") { setView("marked"); } renderDocument(); renderPanel(); scrollToMark(q.term); }
          });
          meta.appendChild(where);
        });
      } else if (q.fromChat) {
        meta.appendChild(el("span", "pseudo-question__source", "aus Ihrer Nachricht im Chat"));
      }
      item.appendChild(meta);
      item.appendChild(el("p", "pseudo-question__ask", "Soll „" + q.term + "“ vor der Übergabe pseudonymisiert werden?"));
      var row = el("div", "pseudo-question__options");
      OPTIONS.forEach(function (option) {
        var b = el("button", "pseudo-question__option");
        b.type = "button";
        b.setAttribute("data-decision", option.decision);
        b.setAttribute("aria-pressed", state.decisions[q.term] === option.decision ? "true" : "false");
        b.title = option.hint;
        b.disabled = state.busy || (state.mode === "view");
        b.appendChild(el("strong", "", option.title));
        b.appendChild(el("small", "", option.hint));
        b.addEventListener("click", function () {
          state.decisions[q.term] = option.decision;
          var next = questions().filter(function (x) { return !state.decisions[x.term]; })[0];
          state.activeTerm = next ? next.term : q.term;
          renderDocTabs();
          renderDocument();
          renderPanel();
          if (next) { goToQuestion(next); } else { confirmBtn.focus({ preventScroll: true }); }
        });
        row.appendChild(b);
      });
      item.appendChild(row);
      var ctx = el("details", "pseudo-question__context");
      ctx.appendChild(el("summary", "", "Kontext anzeigen"));
      var quote = el("p", "pseudo-question__quote");
      quote.appendChild(document.createTextNode("„… " + (q.context_before || "")));
      quote.appendChild(el("mark", "pseudo-mark-inline pseudo-mark--" + kind, q.term));
      quote.appendChild(document.createTextNode((q.context_after || "") + " …“"));
      ctx.appendChild(quote);
      ctx.open = q.term === state.activeTerm && !q.docs.length;
      item.appendChild(ctx);
      questionsBox.appendChild(item);
    });
  }

  function goToQuestion(q) {
    activateQuestion(q.term, true, false);
    if (q.docs.length && q.docs.indexOf(state.currentId) === -1) { selectDoc(q.docs[0], q.term); }
    else { if (state.view !== "marked") { setView("marked"); } scrollToMark(q.term); }
  }

  /** Frage hervorheben (Karte + Markierung). `scrollCard`: Karte ins Bild holen; `jump`: zur Fundstelle springen. */
  function activateQuestion(term, scrollCard, jump) {
    state.activeTerm = term;
    questionsBox.querySelectorAll(".pseudo-question").forEach(function (node) {
      node.classList.toggle("is-active", node.getAttribute("data-question") === term);
    });
    docPane.querySelectorAll(".pseudo-mark").forEach(function (node) {
      node.classList.toggle("is-active", node.getAttribute("data-term") === term);
    });
    renderProgress();
    var node = questionsBox.querySelector("[data-question=\"" + cssEscape(term) + "\"]");
    if (node && scrollCard) {
      var top = node.offsetTop - panelScroll.offsetTop;
      if (top < panelScroll.scrollTop || top + node.offsetHeight > panelScroll.scrollTop + panelScroll.clientHeight) {
        panelScroll.scrollTo({ top: Math.max(0, top - 8), behavior: "smooth" });
      }
    }
    if (jump) {
      var q = questions().filter(function (x) { return x.term === term; })[0];
      if (q) { goToQuestion(q); }
    }
  }

  function stepQuestion(delta) {
    var qs = questions();
    if (!qs.length) { return; }
    var index = activeIndex(qs);
    if (delta > 0) {
      // naechste OFFENE Frage nach der aktuellen (zyklisch)
      for (var step = 1; step <= qs.length; step += 1) {
        var candidate = qs[(Math.max(index, -1) + step) % qs.length];
        if (!state.decisions[candidate.term] && candidate.term !== state.activeTerm) { goToQuestion(candidate); return; }
      }
      return;
    }
    if (index > 0) { goToQuestion(qs[index - 1]); }
  }

  qPrev.addEventListener("click", function () { stepQuestion(-1); });
  qNext.addEventListener("click", function () { stepQuestion(1); });

  function renderMappings() {
    mappingsList.textContent = "";
    var data = state.reviews[state.currentId];
    var mappings = data ? data.mappings : [];
    mappingCount.textContent = String(mappings.length);
    mappingsWrap.hidden = !data;
    mappings.forEach(function (m) {
      var li = el("li", "pseudo-center__mapping");
      li.appendChild(el("span", "pseudo-center__mapping-val", m.original));
      li.appendChild(el("span", "pseudo-center__mapping-cat", categoryLabel(m.category)));
      li.appendChild(el("code", "", m.placeholder));
      li.title = "Zur Fundstelle";
      li.tabIndex = 0;
      li.addEventListener("click", function () { if (state.view !== "marked") { setView("marked"); } scrollToMark(m.original); });
      mappingsList.appendChild(li);
    });
    if (!mappings.length && data) { mappingsList.appendChild(el("li", "form-hint", "Keine Funde in diesem Dokument.")); }
  }

  function renderLimits() {
    limitsBox.textContent = "";
    var data = state.reviews[state.currentId];
    if (!data || (!data.limitations.length && !data.not_located.length)) { return; }
    var details = el("details", "pseudo-center__section");
    details.appendChild(el("summary", "", "Hinweise zur Darstellung"));
    data.limitations.forEach(function (text) { details.appendChild(el("p", "form-hint", text)); });
    if (data.not_located.length) {
      var ul = el("ul", "pseudo-center__not-located");
      data.not_located.forEach(function (v) { ul.appendChild(el("li", "", v)); });
      details.appendChild(ul);
      details.open = true; // nicht markierte Werte nicht verstecken
    }
    limitsBox.appendChild(details);
  }

  function renderProtect() {
    var data = state.reviews[state.currentId];
    var wrap = $("[data-pseudo-protect-wrap]");
    wrap.hidden = !(data && state.matterId && data.viewer_kind && data.status !== "no_text");
    if (wrap.hidden) { return; }
    protectForm.setAttribute("action", "/dashboard/matters/" + encodeURIComponent(state.matterId) + "/document/" +
                             encodeURIComponent(state.currentId) + "/protect-names");
    protectForm.querySelector("input[name=\"text_version\"]").value = data.text_version;
    protectForm.querySelector("input[name=\"return_to\"]").value = "chat:" + state.conversationId;
  }

  var QUICK = [
    { label: "Antwort entwerfen", text: "Bitte entwirf auf Basis von „{f}“ eine Antwort auf dieses Dokument." },
    { label: "Fristen & Risiken prüfen", text: "Bitte pruefe „{f}“ auf Fristen und rechtliche Risiken und fasse sie zusammen." },
    { label: "Kurz zusammenfassen", text: "Bitte fasse „{f}“ in wenigen Saetzen zusammen." }
  ];

  function renderQuick() {
    quickActions.textContent = "";
    var data = state.reviews[state.currentId];
    var input = document.querySelector("textarea[name=\"content\"]");
    quickBox.hidden = !(state.mode === "view" && data && data.status !== "no_text" && input);
    if (quickBox.hidden) { return; }
    QUICK.forEach(function (action) {
      var b = el("button", "btn btn--ghost pseudo-center__quick-btn", action.label);
      b.type = "button";
      b.setAttribute("data-quick-action", action.label);
      b.addEventListener("click", function () {
        // nur vorbelegen - gesendet wird erst durch den Nutzer, der Server prueft dann erneut
        input.value = action.text.replace("{f}", data.filename);
        input.dispatchEvent(new Event("input", { bubbles: true }));
        close("quick");
        input.focus();
      });
      quickActions.appendChild(b);
    });
  }

  /** Ein einheitlicher Button-Text; der naechste Schritt (z. B. "Schriftsatz erstellen") steht im Hinweis links. */
  function confirmLabel() { return "Pseudonymisierung bestätigen und fortfahren"; }

  function nextStep() {
    if (state.mode === "review") { return "Danach wird Ihre Nachricht erneut geprüft und fortgesetzt."; }
    return state.order.label ? "Danach: " + state.order.label + "." : "";
  }

  function renderFooterStatus() {
    footerStatus.textContent = "";
    var funde = 0;
    state.docs.forEach(function (d) { var r = state.reviews[d.id]; if (r) { funde += r.mappings.length; } });
    var qs = questions().length, open = openTerms().length;
    function chip(cls, label, value) {
      var c = el("span", "pseudo-center__chip " + cls);
      c.appendChild(el("strong", "", String(value)));
      c.appendChild(document.createTextNode(" " + label));
      footerStatus.appendChild(c);
    }
    chip("pseudo-center__chip--masked", (funde === 1 ? "Fund" : "Funde") + (state.docs.length > 1 ? " gesamt" : ""), funde);
    if (qs) { chip(open ? "pseudo-center__chip--open" : "pseudo-center__chip--ok", "offen", open + " / " + qs); }
    if (state.docs.length > 1) { chip("", "Dokumente", state.docs.length); }
  }

  function renderFooter() {
    var remaining = openTerms().length;
    var blocked = state.docs.some(function (d) { var r = state.reviews[d.id]; return r && (r.status === "blocked" || r.status === "no_text") && d.needs_confirmation; });
    confirmBtn.hidden = state.mode === "view";
    confirmBtn.textContent = confirmLabel();
    var lock = "";
    if (state.mode === "view") {
      openCount.textContent = "";
    } else if (!state.canManage && state.mode === "release") {
      openCount.textContent = lock = "Bestätigen dürfen Administration und Anwälte.";
    } else if (blocked && state.mode === "release") {
      openCount.textContent = lock = "Mindestens ein Dokument ist blockiert – Fortsetzen nicht möglich.";
    } else {
      openCount.textContent = remaining ? remaining + " Rückfrage(n) offen – bitte zuerst entscheiden."
                                        : ((questions().length ? "Alle Rückfragen beantwortet – bitte prüfen und bestätigen."
                                                               : "Keine offenen Rückfragen.") + " " + nextStep()).trim();
      if (remaining) { lock = "Erst alle Rückfragen beantworten."; }
    }
    var loadingNeeded = state.mode === "release" && state.docs.some(function (d) { return d.needs_confirmation && !state.reviews[d.id]; });
    if (loadingNeeded && !lock) { lock = "Dokumente werden noch geprüft …"; }
    confirmBtn.disabled = state.mode === "view" || state.busy || remaining > 0 || loadingNeeded ||
      (state.mode === "release" && (!state.canManage || blocked));
    confirmBtn.title = confirmBtn.disabled ? lock : "";
    renderFooterStatus();
  }

  function renderPanel() {
    renderStatus();
    renderProgress();
    renderQuestions();
    renderMappings();
    renderProtect();
    renderQuick();
    renderLimits();
    renderFooter();
  }

  // --- Laden -----------------------------------------------------------------------------------------------------

  function ensureReview(id) {
    if (state.reviews[id]) { return Promise.resolve(state.reviews[id]); }
    var generation = state.generation;
    var url = "/dashboard/chat/" + encodeURIComponent(state.conversationId) + "/document/" + encodeURIComponent(id) +
              "/pseudonymization-review";
    if (state.mode === "review" && state.payload) {
      url += "?" + state.payload.candidates.map(function (c) { return "terms=" + encodeURIComponent(c.term); }).join("&");
    }
    return fetch(url, { credentials: "same-origin" })
      .then(function (r) { if (!r.ok) { throw new Error("load_failed"); } return r.json(); })
      .then(function (data) {
        if (generation !== state.generation) { return null; }
        state.reviews[id] = data;
        renderDocTabs();
        return data;
      });
  }

  function loadDocs() {
    if (!state.conversationId) { return Promise.resolve(); }
    return fetch("/dashboard/chat/" + encodeURIComponent(state.conversationId) + "/pseudonymization-documents",
                 { credentials: "same-origin" })
      .then(function (r) { if (!r.ok) { throw new Error("load_failed"); } return r.json(); })
      .then(function (data) {
        state.docs = data.documents || [];
        state.order = { label: data.order_label || "", has_open_order: !!data.has_open_order };
      });
  }

  function showNotice() {
    var text = dialog.getAttribute("data-notice");
    noticeBox.hidden = !text;
    noticeBox.className = "pseudo-center__notice" + (dialog.getAttribute("data-notice-kind") === "error" ? " pseudo-center__notice--error" : "");
    noticeBox.textContent = text || "";
  }

  function setMaximized(on) {
    state.maximized = on;
    dialog.classList.toggle("is-maximized", on);
    maximizeBtn.setAttribute("aria-pressed", on ? "true" : "false");
    maximizeBtn.title = on ? "Prüfpanel wieder einblenden" : "Vorschau maximieren (Prüfpanel ausblenden)";
  }

  var returnFocus = null;

  function open(options) {
    options = options || {};
    state.generation += 1;
    var generation = state.generation;
    if (options.payload && options.payload.conversation_id) { state.conversationId = options.payload.conversation_id; }
    state.payload = options.payload || null;
    state.callback = options.callback || null;
    state.reviews = {};
    state.decisions = {};
    state.busy = false;
    state.view = "marked";
    state.activeTerm = null;
    setMaximized(false);
    followupBox.textContent = "";
    returnFocus = document.activeElement;
    setView("marked", true);
    docPane.textContent = "";
    docPane.appendChild(el("p", "pseudo-center__loading", "Dokumente werden lokal geprüft …"));
    showNotice();
    if (typeof dialog.showModal === "function") { if (!dialog.open) { dialog.showModal(); } }
    else { dialog.setAttribute("open", "open"); }
    renderPanel();
    return loadDocs().then(function () {
      if (generation !== state.generation) { return; }
      if (state.payload) { state.mode = "review"; }
      else if (state.order.has_open_order && state.docs.some(function (d) { return d.needs_confirmation; })) { state.mode = "release"; }
      else { state.mode = "view"; }
      var wanted = options.documentId && state.docs.some(function (d) { return d.id === options.documentId; })
        ? options.documentId : null;
      var first = state.docs.filter(function (d) { return d.needs_confirmation; })[0] || state.docs[0];
      state.currentId = wanted || (first ? first.id : null);
      renderDocTabs();
      // Fuer Rueckfragen und Fundstellen ALLE Dokumente pruefen (lokal, nacheinander); das gewaehlte zuerst.
      var order = state.docs.map(function (d) { return d.id; });
      if (state.currentId) { order.splice(order.indexOf(state.currentId), 1); order.unshift(state.currentId); }
      var chain = Promise.resolve();
      order.forEach(function (id, index) {
        chain = chain.then(function () { return ensureReview(id); }).then(function () {
          if (generation !== state.generation) { return; }
          if (index === 0) { renderDocument(); }
          renderPanel();
        });
      });
      return chain.then(function () {
        if (generation !== state.generation) { return; }
        renderDocTabs();
        renderPanel();
        if (!state.docs.length) { renderDocument(); }
        var firstOpen = questions().filter(function (q) { return !state.decisions[q.term]; })[0];
        if (firstOpen) { state.activeTerm = firstOpen.term; renderPanel(); if (firstOpen.docs.length) { goToQuestion(firstOpen); } }
      });
    }).catch(function () {
      docPane.textContent = "";
      docPane.appendChild(el("p", "banner banner--error", "Die Prüfansicht konnte nicht geladen werden. Es wurde nichts gesendet."));
      confirmBtn.disabled = true;
    });
  }

  function close(reason) {
    if (state.busy) { return; }
    state.generation += 1;
    state.decisions = {}; // Abbruch: nichts gespeichert, nichts gesendet
    var callback = state.callback;
    state.callback = null;
    if (dialog.open && typeof dialog.close === "function") { dialog.close(); } else { dialog.removeAttribute("open"); }
    if (reason !== "quick" && returnFocus && returnFocus.focus) { returnFocus.focus({ preventScroll: true }); }
    if (callback && (reason === "cancel" || reason === "quick") && typeof callback.onCancel === "function") { callback.onCancel(); }
  }

  function setView(view, silent) {
    state.view = view;
    dialog.querySelectorAll("[data-pseudo-view]").forEach(function (tab) {
      var active = tab.getAttribute("data-pseudo-view") === view;
      tab.classList.toggle("is-active", active);
      tab.setAttribute("aria-selected", active ? "true" : "false");
    });
    if (!silent) { renderDocument(); }
  }

  dialog.querySelectorAll("[data-pseudo-view]").forEach(function (tab) {
    tab.addEventListener("click", function () { setView(tab.getAttribute("data-pseudo-view")); });
  });
  dialog.querySelectorAll("[data-pseudo-center-cancel]").forEach(function (b) {
    b.addEventListener("click", function () { close("cancel"); });
  });
  dialog.addEventListener("cancel", function (evt) {
    evt.preventDefault(); // Escape: wie Abbrechen (gleiche Regeln)
    close("cancel");
  });
  $("[data-pseudo-page-prev]").addEventListener("click", function () { goToPage(-1); });
  $("[data-pseudo-page-next]").addEventListener("click", function () { goToPage(1); });
  $("[data-pseudo-zoom-in]").addEventListener("click", function () { setZoom(state.zoom + 0.2); });
  $("[data-pseudo-zoom-out]").addEventListener("click", function () { setZoom(state.zoom - 0.2); });
  $("[data-pseudo-zoom-fit]").addEventListener("click", function () { setZoom(1); });
  thumbsToggle.addEventListener("click", function () { rerenderKeepingPage(function () { state.thumbs = !state.thumbs; }); });
  maximizeBtn.addEventListener("click", function () {
    rerenderKeepingPage(function () { setMaximized(!state.maximized); });
  });
  var scrollTimer = null;
  docPane.addEventListener("scroll", function () {
    if (scrollTimer) { return; }
    scrollTimer = setTimeout(function () { scrollTimer = null; updatePageInfo(); }, 80);
  });

  protectForm.addEventListener("submit", function (evt) {
    var name = (protectInput.value || "").trim();
    if (!name) { evt.preventDefault(); protectInput.focus(); return; }
    protectForm.querySelector("input[name=\"csrf_token\"]").value = csrfToken();
    protectForm.querySelector("input[name=\"protected_terms\"]").value = JSON.stringify([name]);
  });

  // --- Bestaetigen -----------------------------------------------------------------------------------------------

  function readSse(response, onPayload) {
    if (!response.ok || !response.body) { return Promise.reject(new Error("stream_failed")); }
    var reader = response.body.getReader();
    var decoder = new TextDecoder("utf-8");
    var buffer = "";
    function pump() {
      return reader.read().then(function (result) {
        if (result.done) { return; }
        buffer += decoder.decode(result.value, { stream: true });
        var frames = buffer.split("\n\n");
        buffer = frames.pop();
        frames.forEach(function (frame) {
          var line = frame.trim();
          if (line.indexOf("data: ") !== 0) { return; }
          try { onPayload(JSON.parse(line.slice(6))); } catch (e) { /* ignorieren */ }
        });
        return pump();
      });
    }
    return pump();
  }

  function backToChat() {
    state.busy = false;
    window.location.href = "/dashboard/chat/" + encodeURIComponent(state.conversationId);
  }

  function setBusy(busy, text) {
    state.busy = busy;
    dialog.querySelectorAll("[data-pseudo-center-cancel]").forEach(function (b) { b.disabled = busy; });
    renderFooter();
    if (text) { openCount.textContent = text; }
  }

  function showError(text) {
    setBusy(false);
    followupBox.textContent = "";
    followupBox.appendChild(el("p", "banner banner--error", text));
  }

  function handleStream(response) {
    var finished = false;
    return readSse(response, function (next) {
      if (next.kind === "review") {
        finished = true;
        // Rueckfrage beim Fortsetzen (z. B. aus dem Auftragstext): im SELBEN Dialog weiterarbeiten
        state.busy = false;
        dialog.querySelectorAll("[data-pseudo-center-cancel]").forEach(function (b) { b.disabled = false; });
        state.mode = "review";
        state.payload = next;
        state.callback = { onDecisions: submitFollowup, onCancel: backToChat };
        state.decisions = {};
        state.reviews = {};
        state.activeTerm = next.candidates && next.candidates.length ? next.candidates[0].term : null;
        followupBox.textContent = "";
        followupBox.appendChild(el("p", "form-hint", "Beim Fortsetzen ist eine weitere Rückfrage entstanden."));
        var ids = state.docs.map(function (d) { return d.id; });
        var chain = Promise.resolve();
        ids.forEach(function (id) { chain = chain.then(function () { return ensureReview(id); }); });
        chain.then(function () { renderDocTabs(); renderDocument(); renderPanel(); });
        renderPanel();
      } else if (next.kind === "done") {
        finished = true;
        if (next.message_id === null && next.content) { showError(next.content); return; }
        backToChat();
      }
    }).then(function () { if (!finished) { backToChat(); } });
  }

  function submitFollowup(decisions) {
    var form = new FormData();
    form.append("csrf_token", csrfToken());
    form.append("message_id", state.payload.message_id);
    form.append("decisions", JSON.stringify(decisions));
    setBusy(true, "Ihre Auswahl wird übernommen – der Inhalt wird erneut geprüft …");
    fetch("/dashboard/chat/" + encodeURIComponent(state.conversationId) + "/privacy-review",
          { method: "POST", body: form, credentials: "same-origin" })
      .then(handleStream).catch(backToChat);
  }

  confirmBtn.addEventListener("click", function () {
    if (confirmBtn.disabled) { return; }
    var decisions = questions().map(function (q) { return { term: q.term, decision: state.decisions[q.term] }; });
    if (state.mode === "review") {
      var callback = state.callback;
      if (callback && callback.onDecisions === submitFollowup) { submitFollowup(decisions); return; }
      state.callback = null;
      state.generation += 1;
      if (dialog.open && typeof dialog.close === "function") { dialog.close(); }
      if (callback && typeof callback.onDecisions === "function") { callback.onDecisions(decisions); }
      return;
    }
    var targets = state.docs.filter(function (d) {
      var r = state.reviews[d.id];
      return d.needs_confirmation || (r && r.candidates && r.candidates.length);
    });
    if (!targets.length) { targets = state.docs.filter(function (d) { return d.id === state.currentId; }); }
    var primary = targets[0];
    var form = new FormData();
    form.append("csrf_token", csrfToken());
    form.append("document_id", primary.id);
    form.append("text_version", state.reviews[primary.id].text_version);
    form.append("documents", JSON.stringify(targets.slice(1).map(function (d) {
      return { document_id: d.id, text_version: state.reviews[d.id].text_version };
    })));
    form.append("decisions", JSON.stringify(decisions));
    form.append("require_resolved", "1");
    setBusy(true, "Pseudonymisierung wird bestätigt – der gesamte Inhalt wird erneut geprüft …");
    fetch("/dashboard/chat/" + encodeURIComponent(state.conversationId) + "/confirm-release-and-generate",
          { method: "POST", body: form, credentials: "same-origin" })
      .then(handleStream)
      .catch(function () { showError("Die Verbindung wurde unterbrochen. Bitte den Chat neu laden."); });
  });

  // --- Einstiege --------------------------------------------------------------------------------------------------

  document.addEventListener("click", function (evt) {
    var trigger = evt.target.closest && evt.target.closest("[data-pseudonymization-center-open], [data-confirm-release-generate], a[data-pseudo-document-link]");
    if (!trigger) { return; }
    evt.preventDefault();
    evt.stopImmediatePropagation();
    open({ documentId: trigger.getAttribute("data-document-id") || null });
  }, true);

  window.LexonoPseudonymizationCenter = {
    open: open,
    /** Datenschutz-Rueckfrage beim Senden im zentralen Dialog: `onDecisions(decisions)` setzt den bestehenden
     *  Server-Pfad fort; Abbrechen ruft `onCancel` (nichts wird gesendet). */
    openReview: function (payload, onDecisions, onCancel) {
      return open({ payload: payload, callback: { onDecisions: onDecisions, onCancel: onCancel } });
    },
    isOpen: function () { return dialog.open; }
  };

  // Automatisch oeffnen: Dokumentansicht (Chat bleibt darunter) bzw. nach einer Sperre am Dokument-Gate
  var autoDoc = dialog.getAttribute("data-open-document");
  var flagKey = "lexono-open-pseudo-" + state.conversationId;
  var flagged = false;
  try { flagged = state.conversationId && window.sessionStorage.getItem(flagKey) === "1"; if (flagged) { window.sessionStorage.removeItem(flagKey); } } catch (e) { flagged = false; }
  if (autoDoc) { open({ documentId: autoDoc }); }
  else if (flagged) {
    loadDocs().then(function () {
      if (state.order.has_open_order && state.docs.some(function (d) { return d.needs_confirmation; })) { open({}); }
    }).catch(function () { /* nichts oeffnen */ });
  }
})();
