/* Zentraler Pseudonymisierungsdialog (25.10.2026) - siehe partials/pseudonymization_center.html.
 *
 * Regeln:
 * - Dokumentinhalte (Begriffe, Kontexte, Texte) sind untrusted input: ausschliesslich textContent, nie innerHTML.
 * - Entscheidungen bleiben bis "Bestaetigen" nur im Dialog (Abbrechen speichert und sendet NICHTS).
 * - Bestaetigen ist erst moeglich, wenn jede offene Rueckfrage beantwortet ist; der Server prueft das erneut
 *   (require_resolved) zusammen mit dem Textstand und verarbeitet erst dann - fail-closed wie bisher.
 * - Weitere Rueckfragen, die erst beim Fortsetzen entstehen (z. B. aus dem Auftragstext), werden ebenfalls hier
 *   beantwortet (POST .../privacy-review, unveraenderter Server-Pfad).
 */
(function () {
  "use strict";

  var dialog = document.getElementById("pseudonymization-center");
  if (!dialog) { return; }

  var conversationId = dialog.getAttribute("data-conversation-id");
  var documentId = dialog.getAttribute("data-document-id");
  var canManage = dialog.getAttribute("data-can-manage") === "1";
  var docPane = dialog.querySelector("[data-pseudo-document]");
  var statusBox = dialog.querySelector("[data-pseudo-status]");
  var questionsBox = dialog.querySelector("[data-pseudo-questions]");
  var followupBox = dialog.querySelector("[data-pseudo-followup]");
  var mappingsList = dialog.querySelector("[data-pseudo-mappings]");
  var mappingCount = dialog.querySelector("[data-pseudo-mapping-count]");
  var limitsBox = dialog.querySelector("[data-pseudo-limits]");
  var openCount = dialog.querySelector("[data-pseudo-open-count]");
  // Ohne offenen Auftrag gibt es keinen Bestaetigen-Button (reine Pruefansicht) - Platzhalter ohne Wirkung
  var confirmBtn = dialog.querySelector("[data-pseudo-center-confirm]") || document.createElement("button");

  var OPTIONS = [
    { decision: "personal", title: "Pseudonymisieren", hint: "als Person/sensibel behandeln" },
    { decision: "not_personal", title: "Nicht pseudonymisieren", hint: "kein personenbezogenes Datum" },
    { decision: "unsure", title: "Unsicher", hint: "vorsichtshalber pseudonymisieren" }
  ];

  var state = { data: null, decisions: {}, view: "marked", busy: false };

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

  function decisionKind(term) {
    var d = state.decisions[term];
    if (!d) { return "open"; }
    return d === "not_personal" ? "kept" : "masked";
  }

  function openTerms() {
    if (!state.data) { return []; }
    return state.data.candidates.map(function (c) { return c.term; }).filter(function (t) { return !state.decisions[t]; });
  }

  // --- Dokumentansicht ----------------------------------------------------------------------------------------------

  function markClass(mark) {
    if (mark.kind === "open") { return "pseudo-mark pseudo-mark--" + decisionKind(mark.term); }
    return "pseudo-mark pseudo-mark--masked";
  }

  function renderPages() {
    docPane.textContent = "";
    var data = state.data;
    if (state.view === "result") { renderResult(); return; }
    if (data.viewer_kind !== "pages" || !data.pages.length) { renderTextView(); return; }
    data.pages.forEach(function (page) {
      var wrap = el("figure", "pseudo-page");
      wrap.setAttribute("data-page", String(page.number));
      var box = el("div", "pseudo-page__box");
      box.style.aspectRatio = page.width + " / " + page.height;
      var img = el("img", "pseudo-page__img");
      img.alt = "Seite " + page.number + " (Original)";
      img.loading = "lazy";
      img.src = data.page_url_base + page.number + ".png?dpi=110";
      box.appendChild(img);
      if (state.view === "marked") {
        page.marks.forEach(function (mark) {
          var m = el("span", markClass(mark));
          m.style.left = (mark.x * 100) + "%";
          m.style.top = (mark.y * 100) + "%";
          m.style.width = (mark.w * 100) + "%";
          m.style.height = (mark.h * 100) + "%";
          m.title = mark.kind === "open"
            ? (decisionKind(mark.term) === "open" ? "Rückfrage offen: " : "Entschieden: ") + mark.term
            : mark.placeholder + " (" + mark.category + ")";
          if (mark.kind === "open") {
            m.setAttribute("data-term", mark.term);
            m.addEventListener("click", function () { focusQuestion(mark.term); });
          }
          box.appendChild(m);
        });
      }
      wrap.appendChild(box);
      wrap.appendChild(el("figcaption", "pseudo-page__caption", "Seite " + page.number + " von " + data.pages.length));
      docPane.appendChild(wrap);
    });
  }

  function renderTextView() {
    var data = state.data;
    var box = el("div", "pseudo-text");
    var text = data.original_text || "";
    if (state.view !== "marked") { box.textContent = text; docPane.appendChild(box); return; }
    // Begriffe im lokalen Text markieren (laengste zuerst, nur Textknoten - keine HTML-Interpretation)
    var terms = {};
    data.mappings.forEach(function (m) { terms[m.original] = { kind: "masked", term: m.original }; });
    data.candidates.forEach(function (c) { terms[c.term] = { kind: "open", term: c.term }; });
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
      var mark = el("mark", info.kind === "open" ? "pseudo-mark-inline pseudo-mark--" + decisionKind(info.term)
                                                  : "pseudo-mark-inline pseudo-mark--masked", bestKey);
      box.appendChild(mark);
      pos = best + bestKey.length;
    }
    docPane.appendChild(box);
  }

  function renderResult() {
    var data = state.data;
    var head = el("p", "pseudo-center__result-note",
      "Pseudonymisiertes Ergebnis (Text, der nach der Bestätigung geprüft und übermittelt wird). Stand ohne Ihre " +
      "noch nicht gespeicherten Entscheidungen; „Nicht pseudonymisieren“ macht einen Begriff erst nach der " +
      "Bestätigung lesbar, und der Server prüft dann erneut.");
    docPane.appendChild(head);
    var box = el("div", "pseudo-text pseudo-text--result",
      data.result_text || "Kein sendefähiges Ergebnis – siehe Prüfstatus rechts.");
    docPane.appendChild(box);
  }

  // --- Pruefung (rechts) --------------------------------------------------------------------------------------------

  function renderStatus() {
    var data = state.data;
    statusBox.textContent = "";
    var text, cls;
    if (data.status === "blocked") {
      cls = "pseudo-center__state pseudo-center__state--blocked";
      text = "Prüfung fehlgeschlagen – das Dokument kann nicht verarbeitet werden.";
    } else if (data.status === "no_text") {
      cls = "pseudo-center__state pseudo-center__state--blocked";
      text = "Kein verwertbarer Text – keine Verarbeitung möglich.";
    } else if (data.candidates.length) {
      cls = "pseudo-center__state pseudo-center__state--open";
      text = data.candidates.length + " Rückfrage(n) zu unsicheren Begriffen";
    } else {
      cls = "pseudo-center__state pseudo-center__state--ok";
      text = "Keine offenen Rückfragen";
    }
    statusBox.appendChild(el("p", cls, text));
    (data.blocked_reasons || []).forEach(function (r) { statusBox.appendChild(el("p", "form-hint", r)); });
  }

  function renderQuestions() {
    questionsBox.textContent = "";
    var data = state.data;
    if (!data.candidates.length) { return; }
    questionsBox.appendChild(el("h3", "pseudo-center__section-title", "Rückfragen"));
    data.candidates.forEach(function (candidate) {
      var item = el("div", "pseudo-question pseudo-question--" + decisionKind(candidate.term));
      item.setAttribute("data-question", candidate.term);
      var quote = el("p", "pseudo-question__quote");
      quote.appendChild(document.createTextNode("„… " + (candidate.context_before || "")));
      quote.appendChild(el("mark", "pseudo-mark-inline pseudo-mark--" + decisionKind(candidate.term), candidate.term));
      quote.appendChild(document.createTextNode((candidate.context_after || "") + " …“"));
      item.appendChild(quote);
      var row = el("div", "pseudo-question__options");
      OPTIONS.forEach(function (option) {
        var b = el("button", "pseudo-question__option");
        b.type = "button";
        b.setAttribute("data-decision", option.decision);
        b.setAttribute("aria-pressed", state.decisions[candidate.term] === option.decision ? "true" : "false");
        b.appendChild(el("strong", "", option.title));
        b.appendChild(el("small", "", option.hint));
        b.addEventListener("click", function () {
          state.decisions[candidate.term] = option.decision;
          refresh();
          var next = openTerms()[0];
          if (next) { focusQuestion(next); }
        });
        row.appendChild(b);
      });
      item.appendChild(row);
      questionsBox.appendChild(item);
    });
  }

  function focusQuestion(term) {
    var node = questionsBox.querySelector("[data-question=\"" + (window.CSS && CSS.escape ? CSS.escape(term) : term) + "\"]");
    if (node) { node.scrollIntoView({ block: "nearest", behavior: "smooth" }); node.classList.add("is-focused");
      setTimeout(function () { node.classList.remove("is-focused"); }, 1200); }
    var mark = docPane.querySelector(".pseudo-mark[data-term=\"" + (window.CSS && CSS.escape ? CSS.escape(term) : term) + "\"]");
    if (mark) { mark.scrollIntoView({ block: "center", behavior: "smooth" }); }
  }

  function renderMappings() {
    mappingsList.textContent = "";
    var data = state.data;
    mappingCount.textContent = "(" + data.mappings.length + ")";
    data.mappings.forEach(function (m) {
      var li = el("li", "pseudo-center__mapping");
      li.appendChild(el("code", "", m.placeholder));
      li.appendChild(el("span", "pseudo-center__mapping-cat", m.category));
      li.appendChild(el("span", "pseudo-center__mapping-val", m.original));
      mappingsList.appendChild(li);
    });
  }

  function renderLimits() {
    limitsBox.textContent = "";
    var data = state.data;
    if (!data.limitations.length && !data.not_located.length) { return; }
    limitsBox.appendChild(el("h3", "pseudo-center__section-title", "Hinweise zur Darstellung"));
    data.limitations.forEach(function (text) { limitsBox.appendChild(el("p", "form-hint", text)); });
    if (data.not_located.length) {
      var ul = el("ul", "pseudo-center__not-located");
      data.not_located.forEach(function (v) { ul.appendChild(el("li", "", v)); });
      limitsBox.appendChild(ul);
    }
  }

  function renderFooter() {
    var data = state.data;
    var remaining = openTerms().length;
    var blocked = data.status === "blocked" || data.status === "no_text";
    openCount.textContent = blocked ? "" : remaining
      ? remaining + " Rückfrage(n) offen – bitte zuerst entscheiden."
      : "Alle Rückfragen beantwortet.";
    confirmBtn.disabled = state.busy || blocked || remaining > 0 || !canManage;
    if (!canManage) { openCount.textContent = "Bestätigen dürfen Administration und Anwälte."; }
  }

  function refresh() {
    renderPages();
    renderStatus();
    renderQuestions();
    renderMappings();
    renderLimits();
    renderFooter();
  }

  // --- Laden / Oeffnen / Schliessen ---------------------------------------------------------------------------------

  function load() {
    docPane.textContent = "";
    docPane.appendChild(el("p", "pseudo-center__loading", "Dokument wird lokal geprüft …"));
    return fetch("/dashboard/chat/" + encodeURIComponent(conversationId) + "/document/" +
                 encodeURIComponent(documentId) + "/pseudonymization-review", { credentials: "same-origin" })
      .then(function (r) { if (!r.ok) { throw new Error("load_failed"); } return r.json(); })
      .then(function (data) {
        if (data.text_version !== dialog.getAttribute("data-text-version")) {
          // Dokument hat sich seit dem Laden der Seite geaendert - aktuellen Stand verwenden (Server prueft erneut)
          dialog.setAttribute("data-text-version", data.text_version);
        }
        state.data = data;
        state.decisions = {};
        refresh();
      })
      .catch(function () {
        docPane.textContent = "";
        docPane.appendChild(el("p", "banner banner--error", "Die Prüfansicht konnte nicht geladen werden. Es wurde nichts gesendet."));
        confirmBtn.disabled = true;
      });
  }

  var returnFocus = null;
  function openDialog() {
    returnFocus = document.activeElement;
    if (typeof dialog.showModal === "function") { if (!dialog.open) { dialog.showModal(); } }
    else { dialog.setAttribute("open", "open"); }
    state.view = "marked";
    setTab("marked");
    load();
  }

  function closeDialog() {
    if (state.busy) { return; }
    state.decisions = {}; // Abbruch: nichts gespeichert, nichts gesendet
    if (dialog.open && typeof dialog.close === "function") { dialog.close(); } else { dialog.removeAttribute("open"); }
    if (returnFocus && returnFocus.focus) { returnFocus.focus({ preventScroll: true }); }
  }

  function setTab(view) {
    state.view = view;
    dialog.querySelectorAll("[data-pseudo-view]").forEach(function (tab) {
      var active = tab.getAttribute("data-pseudo-view") === view;
      tab.classList.toggle("is-active", active);
      tab.setAttribute("aria-selected", active ? "true" : "false");
    });
    if (state.data) { renderPages(); }
  }

  dialog.querySelectorAll("[data-pseudo-view]").forEach(function (tab) {
    tab.addEventListener("click", function () { setTab(tab.getAttribute("data-pseudo-view")); });
  });
  dialog.querySelectorAll("[data-pseudo-center-cancel]").forEach(function (b) { b.addEventListener("click", closeDialog); });
  dialog.addEventListener("cancel", function (evt) { if (state.busy) { evt.preventDefault(); } else { state.decisions = {}; } });

  document.querySelectorAll("[data-pseudonymization-center-open], [data-confirm-release-generate]").forEach(function (b) {
    b.addEventListener("click", function (evt) { evt.preventDefault(); evt.stopImmediatePropagation(); openDialog(); }, true);
  });

  // --- Bestaetigen und fortsetzen -----------------------------------------------------------------------------------

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
    window.location.href = "/dashboard/chat/" + encodeURIComponent(conversationId);
  }

  function setBusy(busy, text) {
    state.busy = busy;
    confirmBtn.disabled = busy || confirmBtn.disabled;
    dialog.querySelectorAll("[data-pseudo-center-cancel]").forEach(function (b) { b.disabled = busy; });
    if (text) { openCount.textContent = text; }
  }

  function handleStream(response) {
    var finished = false;
    return readSse(response, function (next) {
      if (next.kind === "review") {
        finished = true;
        showFollowup(next);
      } else if (next.kind === "done") {
        finished = true;
        if (next.message_id === null && next.content) {
          // Abweisung (z. B. veralteter Stand, offene Rueckfrage) - nichts wurde gesendet; im Dialog anzeigen
          setBusy(false);
          followupBox.textContent = "";
          followupBox.appendChild(el("p", "banner banner--error", next.content));
          load();
          return;
        }
        backToChat();
      }
    }).then(function () { if (!finished) { backToChat(); } });
  }

  function showFollowup(payload) {
    // Rueckfragen, die erst beim Fortsetzen entstehen (z. B. Begriffe im Auftragstext) - hier beantworten.
    setBusy(false, "Weitere Rückfrage beim Fortsetzen – bitte entscheiden.");
    confirmBtn.disabled = true;
    followupBox.textContent = "";
    followupBox.appendChild(el("h3", "pseudo-center__section-title", "Weitere Rückfrage zum Auftrag"));
    var answers = {};
    payload.candidates.forEach(function (candidate) {
      var item = el("div", "pseudo-question pseudo-question--open");
      var quote = el("p", "pseudo-question__quote");
      quote.appendChild(document.createTextNode("„… " + (candidate.context_before || "")));
      quote.appendChild(el("mark", "pseudo-mark-inline pseudo-mark--open", candidate.term));
      quote.appendChild(document.createTextNode((candidate.context_after || "") + " …“"));
      item.appendChild(quote);
      var row = el("div", "pseudo-question__options");
      OPTIONS.forEach(function (option) {
        var b = el("button", "pseudo-question__option");
        b.type = "button";
        b.appendChild(el("strong", "", option.title));
        b.appendChild(el("small", "", option.hint));
        b.addEventListener("click", function () {
          answers[candidate.term] = option.decision;
          row.querySelectorAll("button").forEach(function (x) { x.setAttribute("aria-pressed", x === b ? "true" : "false"); });
          if (Object.keys(answers).length === payload.candidates.length) {
            var decisions = payload.candidates.map(function (c) { return { term: c.term, decision: answers[c.term] }; });
            var form = new FormData();
            form.append("csrf_token", csrfToken());
            form.append("message_id", payload.message_id);
            form.append("decisions", JSON.stringify(decisions));
            setBusy(true, "Ihre Auswahl wird übernommen – der Inhalt wird erneut geprüft …");
            fetch("/dashboard/chat/" + encodeURIComponent(conversationId) + "/privacy-review",
                  { method: "POST", body: form, credentials: "same-origin" })
              .then(handleStream).catch(backToChat);
          }
        });
        row.appendChild(b);
      });
      item.appendChild(row);
      followupBox.appendChild(item);
    });
  }

  confirmBtn.addEventListener("click", function () {
    if (confirmBtn.disabled || !state.data) { return; }
    var decisions = state.data.candidates.map(function (c) { return { term: c.term, decision: state.decisions[c.term] }; });
    var form = new FormData();
    form.append("csrf_token", csrfToken());
    form.append("document_id", documentId);
    form.append("text_version", state.data.text_version);
    form.append("decisions", JSON.stringify(decisions));
    form.append("require_resolved", "1");
    setBusy(true, "Pseudonymisierung wird bestätigt – der gesamte Inhalt wird erneut geprüft …");
    confirmBtn.disabled = true;
    fetch("/dashboard/chat/" + encodeURIComponent(conversationId) + "/confirm-release-and-generate",
          { method: "POST", body: form, credentials: "same-origin" })
      .then(handleStream)
      .catch(function () {
        setBusy(false);
        renderFooter();
        followupBox.textContent = "";
        followupBox.appendChild(el("p", "banner banner--error", "Die Verbindung wurde unterbrochen. Bitte den Chat neu laden."));
      });
  });

  window.LexonoPseudonymizationCenter = { open: openDialog };
})();
