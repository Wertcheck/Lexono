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
  var docRow = $("[data-pseudo-doc-row]");
  var thumbs = $("[data-pseudo-thumbs]");
  var fileName = $("[data-pseudo-file-name]");
  var fileTypeBadge = $("[data-pseudo-file-type]");
  var fileIcon = $("[data-pseudo-file-icon]");
  var applyBtn = $("[data-pseudo-q-apply]");
  var applyLabel = $("[data-pseudo-q-apply-label]");
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

  // Erklaerungen beschreiben das TATSAECHLICHE Verhalten (Server: "unsure" wird wie "personal" pseudonymisiert)
  var OPTIONS = [
    { decision: "personal", title: "Pseudonymisieren", icon: "shield",
      hint: "Wird durch einen Platzhalter ersetzt." },
    { decision: "not_personal", title: "Nicht pseudonymisieren", icon: "eye-off",
      hint: "Der Begriff bleibt im Text lesbar." },
    { decision: "unsure", title: "Unsicher", icon: "question",
      hint: "Wird vorsichtshalber pseudonymisiert." }
  ];

  var ICONS = {
    shield: "M12 3.3l7 2.5v6c0 4.6-3 7.9-7 9-4-1.1-7-4.4-7-9v-6l7-2.5Z|M9 12.3l2.1 2.1 4-4",
    "eye-off": "M3 3l18 18|M10.6 6.1A9.8 9.8 0 0 1 12 6c5 0 8.5 4.2 9.5 6-.5.9-1.5 2.3-2.9 3.6|M6.6 7.6C4.6 8.9 3.2 10.8 2.5 12c1 1.8 4.5 6 9.5 6 1.6 0 3-.4 4.3-1|M9.9 10a3 3 0 0 0 4.1 4.1",
    question: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18Z|M9.6 9.3a2.5 2.5 0 0 1 4.8.9c0 1.7-2.4 2.2-2.4 3.6|M12 17h.01",
    person: "M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8Z|M4.5 20.5c.9-3.6 3.9-5.5 7.5-5.5s6.6 1.9 7.5 5.5",
    organisation: "M4 21V5a1 1 0 0 1 1-1h9a1 1 0 0 1 1 1v16|M15 9h4a1 1 0 0 1 1 1v11|M3 21h18|M8 8h3M8 12h3M8 16h3",
    adresse: "M12 21s-7-6.1-7-11.5a7 7 0 0 1 14 0C19 14.9 12 21 12 21Z|M12 12.5a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5Z",
    datum: "M5 5h14a1 1 0 0 1 1 1v13a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1Z|M4 10h16|M8 3v4M16 3v4",
    sonstiges: "M3.5 12.5 11 5h7.5a1 1 0 0 1 1 1v7.5L12 21a1 1 0 0 1-1.4 0l-7.1-7.1a1 1 0 0 1 0-1.4Z|M15.5 8.5h.01",
    unsicher: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18Z|M9.6 9.3a2.5 2.5 0 0 1 4.8.9c0 1.7-2.4 2.2-2.4 3.6|M12 17h.01"
  };

  /** Inline-SVG aus festen Pfaden per createElementNS (keine Dokumentinhalte, kein HTML-Parsing). */
  function icon(name, className) {
    var ns = "http://www.w3.org/2000/svg";
    var svg = document.createElementNS(ns, "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("fill", "none");
    svg.setAttribute("stroke", "currentColor");
    svg.setAttribute("stroke-width", "1.7");
    svg.setAttribute("stroke-linecap", "round");
    svg.setAttribute("stroke-linejoin", "round");
    svg.setAttribute("aria-hidden", "true");
    if (className) { svg.setAttribute("class", className); }
    (ICONS[name] || ICONS.sonstiges).split("|").forEach(function (d) {
      var path = document.createElementNS(ns, "path");
      path.setAttribute("d", d);
      svg.appendChild(path);
    });
    return svg;
  }

  // Farbgruppen der Legende (Referenz: Person, Organisation, Adresse, Datum, Sonstiges)
  var CATEGORY_GROUPS = {
    person: "person", name: "person", organisation: "organisation", organization: "organisation", firma: "organisation",
    adresse: "adresse", anschrift: "adresse", ort: "adresse", strasse: "adresse", plz: "adresse",
    datum: "datum", geburtsdatum: "datum", todesdatum: "datum"
  };
  var GROUP_TITLES = {
    person: "Erkannte Person", organisation: "Erkannte Organisation", adresse: "Erkannte Adresse",
    datum: "Erkanntes Datum", sonstiges: "Erkannte Angabe", unsicher: "Unsicherer Begriff"
  };

  function categoryGroup(category) { return CATEGORY_GROUPS[(category || "").toLowerCase()] || "sonstiges"; }

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
    activeTerm: null, occurrence: 0, maximized: false
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

  /** Rohkategorie aus den erkannten Funden, falls der Begriff dort (vorsichtig) bereits zugeordnet ist, sonst null. */
  function questionCategoryRaw(term) {
    var found = null;
    state.docs.forEach(function (doc) {
      var data = state.reviews[doc.id];
      (data && data.mappings || []).forEach(function (m) {
        if (!found && (m.original === term || (m.variants || []).indexOf(term) !== -1)) { found = m.category; }
      });
    });
    return found;
  }

  /** Farbgruppe einer Rueckfrage: bekannte Kategorie, sonst "unsicher" (kein erfundener Typ). */
  function questionGroup(term) {
    var raw = questionCategoryRaw(term);
    return raw ? categoryGroup(raw) : "unsicher";
  }

  /** Alle Fundstellen eines Begriffs ueber die Dokumente: [{doc, page}] (Dokumentreihenfolge, Seitenreihenfolge). */
  function occurrences(q) {
    var list = [];
    q.docs.forEach(function (id) {
      var data = state.reviews[id];
      (data && data.pages || []).forEach(function (p) {
        p.marks.forEach(function (m) { if (m.term === q.term && m.kind === "open") { list.push({ doc: id, page: p.number }); } });
      });
    });
    return list;
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
    docRow.hidden = state.docs.length < 2; // ein Dokument: Datei steht in der Werkzeugleiste (wie in der Referenz)
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
        var badge = el("span", "pseudo-center__doc-badge", open + " offen");
        badge.title = open + " offene Rückfrage(n)";
        tab.appendChild(badge);
      } else if (state.reviews[doc.id]) {
        var blockedDoc = state.reviews[doc.id].status === "blocked" || state.reviews[doc.id].status === "no_text";
        var ok = el("span", blockedDoc ? "pseudo-center__doc-blocked" : "pseudo-center__doc-ok",
                    blockedDoc ? "gesperrt" : "✓ geprüft");
        ok.title = blockedDoc ? "Dokument kann nicht verarbeitet werden" : "keine offene Rückfrage";
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

  function selectDoc(id, thenTerm, nth) {
    state.currentId = id;
    renderDocTabs();
    ensureReview(id).then(function () {
      if (state.currentId !== id) { return; }
      renderDocument();
      renderPanel();
      if (thenTerm) { scrollToMark(thenTerm, nth); }
    });
  }

  // --- Dokumentansicht -------------------------------------------------------------------------------------------

  /** Markierung nach Zustand UND Kategorie: Funde flaechig in der Farbe ihrer Kategorie; Rueckfragen gestrichelt
   *  (offen), nach "Pseudonymisieren"/"Unsicher" flaechig, nach "Nicht pseudonymisieren" nur gepunktete Kontur. */
  function kindClass(kind, group) {
    if (kind === "open") { return "pseudo-mark--open"; }
    if (kind === "kept") { return "pseudo-mark--kept"; }
    return "pseudo-mark--masked pseudo-mark--cat-" + (group === "unsicher" ? "sonstiges" : group);
  }

  function markClass(mark) {
    var active = state.activeTerm && mark.term === state.activeTerm ? " is-active" : "";
    if (mark.kind === "open") { return "pseudo-mark " + kindClass(decisionKind(mark.term), questionGroup(mark.term)) + active; }
    return "pseudo-mark " + kindClass("masked", categoryGroup(mark.category));
  }

  /** Nach einer Entscheidung nur Klassen aktualisieren (kein Neuladen der Seitenbilder, Scrollposition bleibt). */
  function refreshMarks() {
    docPane.querySelectorAll(".pseudo-mark[data-kind=\"open\"], .pseudo-mark-inline[data-kind=\"open\"]").forEach(function (node) {
      var term = node.getAttribute("data-term");
      var base = node.classList.contains("pseudo-mark-inline") ? "pseudo-mark-inline " : "pseudo-mark ";
      node.className = base + kindClass(decisionKind(term), questionGroup(term)) +
        (term === state.activeTerm ? " is-active" : "");
      if (!node.classList.contains("pseudo-mark-inline")) { node.title = markTitle({ kind: "open", term: term }); }
    });
    markCurrent();
    thumbs.querySelectorAll(".pseudo-thumb").forEach(function (t) {
      var data = state.reviews[state.currentId];
      var page = data && data.pages ? data.pages[Number(t.getAttribute("data-thumb-page")) - 1] : null;
      var badge = t.querySelector(".pseudo-thumb__badge");
      var open = page ? page.marks.filter(function (m) { return m.kind === "open" && !state.decisions[m.term]; }).length : 0;
      if (badge && !open) { badge.remove(); } else if (badge) { badge.textContent = String(open); }
    });
  }

  // --- Markierungen im Seitenbild (Annotationsdesign 28.10.2026) ----------------------------------------------------

  /** Darstellungsliste einer Seite (nur Anzeige - Serverdaten bleiben unveraendert):
   *  - maskierte Funde, die vollstaendig in einem anderen maskierten Fund liegen (z. B. "Bauer" in "Max Bauer"),
   *    entfallen, damit sich Toenungen nicht doppeln;
   *  - benachbarte maskierte Teile DERSELBEN Kategorie in einer Zeile (z. B. "Thomas" | "Berger" in einer
   *    Tabellenzelle) werden zu EINER Markierung verbunden;
   *  - offene Rueckfragen bleiben einzeln und in Serverreihenfolge (jede Fundstelle ist eigens anwaehlbar) und liegen
   *    oben. Zeilenuebergreifende Funde liefert der Server je Zeile - sie bleiben je Zeile, gleich gestaltet. */
  function drawableMarks(page) {
    var pw = page.width || 595, ph = page.height || 842, eps = 0.0015;
    var masked = page.marks.filter(function (m) { return m.kind !== "open"; });
    var inside = function (a, b) {
      return a !== b && a.x >= b.x - eps && a.x + a.w <= b.x + b.w + eps && a.y >= b.y - eps && a.y + a.h <= b.y + b.h + eps;
    };
    masked = masked.filter(function (a) {
      return !masked.some(function (b) { return inside(a, b) && (b.w * b.h > a.w * a.h || masked.indexOf(b) < masked.indexOf(a)); });
    });
    masked = masked.map(function (m) {
      return { kind: m.kind, term: m.term, category: m.category, x: m.x, y: m.y, w: m.w, h: m.h,
               parts: [{ term: m.term, placeholder: m.placeholder }] };
    }).sort(function (a, b) { return (a.y - b.y) || (a.x - b.x); });
    var merged = [];
    masked.forEach(function (m) {
      var prev = merged[merged.length - 1];
      var gapPt = prev ? (m.x - (prev.x + prev.w)) * pw : 0;
      var sameLine = prev && Math.abs(prev.y - m.y) < 0.35 * m.h && Math.abs(prev.h - m.h) < 0.35 * m.h;
      if (prev && sameLine && categoryGroup(prev.category) === categoryGroup(m.category) &&
          gapPt > -1 && gapPt < 0.6 * m.h * ph) {
        var right = Math.max(prev.x + prev.w, m.x + m.w), bottom = Math.max(prev.y + prev.h, m.y + m.h);
        prev.x = Math.min(prev.x, m.x); prev.y = Math.min(prev.y, m.y);
        prev.w = right - prev.x; prev.h = bottom - prev.y;
        prev.parts = prev.parts.concat(m.parts);
        return;
      }
      merged.push(m);
    });
    return merged.concat(page.marks.filter(function (m) { return m.kind === "open"; }));
  }

  function markTitle(mark) {
    if (mark.kind === "open") {
      var kind = decisionKind(mark.term);
      return kind === "open" ? "Rückfrage offen: „" + mark.term + "“ – zum Prüfen anklicken"
        : "„" + mark.term + "“ – " + (kind === "kept" ? "bleibt lesbar" : "wird pseudonymisiert") + " (Ihre Auswahl)";
    }
    var label = categoryLabel(mark.category);
    return mark.parts.map(function (p) { return p.term; }).join(" ") + " – " + label + ", wird ersetzt durch " +
      mark.parts.map(function (p) { return p.placeholder; }).filter(Boolean).join(" ");
  }

  /** Overlay einer Fundstelle. Die Wortrahmen der PDF-Textebene reichen von Ober- bis Unterlaenge; die Toenung wird
   *  oben/unten leicht eingezogen und seitlich um 1,5 px erweitert, damit sie den Text ruhig umschliesst, ohne
   *  Nachbarzeilen zu beruehren. Prozentwerte: folgt jeder Zoomstufe. */
  function buildMark(mark, occ) {
    var m = el("span", markClass(mark));
    m.style.left = "calc(" + (mark.x * 100) + "% - 1.5px)";
    m.style.width = "calc(" + (mark.w * 100) + "% + 3px)";
    m.style.top = ((mark.y + mark.h * 0.06) * 100) + "%";
    m.style.height = (mark.h * 0.88 * 100) + "%";
    m.setAttribute("data-term", mark.term);
    m.setAttribute("data-kind", mark.kind);
    m.title = markTitle(mark);
    if (mark.kind === "open") {
      m.setAttribute("data-occ", String(occ));
      m.tabIndex = 0;
      m.setAttribute("role", "button");
      m.setAttribute("aria-label", "Rückfrage zu „" + mark.term + "“, Fundstelle " + (occ + 1));
      m.addEventListener("click", function () { selectOccurrence(mark.term, occ); });
      m.addEventListener("keydown", function (evt) {
        if (evt.key === "Enter" || evt.key === " ") { evt.preventDefault(); selectOccurrence(mark.term, occ); }
      });
    }
    return m;
  }

  /** Index der ersten Fundstelle von `term` im aktuellen Dokument innerhalb von `occurrences()` (dokumentuebergreifend). */
  function occOffset(term) {
    var q = questions().filter(function (x) { return x.term === term; })[0];
    if (!q) { return 0; }
    var occ = occurrences(q);
    for (var i = 0; i < occ.length; i += 1) { if (occ[i].doc === state.currentId) { return i; } }
    return 0;
  }

  /** Aktuelle Fundstelle der angezeigten Rueckfrage im Dokument hervorheben (Panel -> Dokument). */
  function markCurrent() {
    docPane.querySelectorAll(".pseudo-mark.is-current").forEach(function (n) { n.classList.remove("is-current"); });
    if (!state.activeTerm) { return; }
    var node = docPane.querySelector(".pseudo-mark[data-kind=\"open\"][data-term=\"" + cssEscape(state.activeTerm) +
                                     "\"][data-occ=\"" + state.occurrence + "\"]");
    if (node) { node.classList.add("is-current"); }
  }

  /** Klick/Enter auf eine Fundstelle: zugehoerige Rueckfrage MIT genau dieser Fundstelle anzeigen (Dokument -> Panel). */
  function selectOccurrence(term, occ) {
    activateQuestion(term, true, false);
    if (state.occurrence !== occ) { state.occurrence = occ; renderQuestions(); }
    markCurrent();
  }

  function renderFileInfo(data, doc) {
    var name = data ? data.filename : (doc ? doc.filename : "");
    var type = name ? fileType(name) : "";
    fileName.textContent = name || (state.docs.length ? "Dokument wird geladen …" : "Keine Dokumente");
    fileName.title = name;
    fileTypeBadge.textContent = type;
    fileIcon.className = "pseudo-center__file-icon" + (type ? " pseudo-center__file-icon--" + type.toLowerCase() : "");
    var funde = data ? data.mappings.length : 0;
    subtitle.textContent = data
      ? type + (data.pages && data.pages.length ? " · " + data.pages.length + (data.pages.length === 1 ? " Seite" : " Seiten") : "") +
        " · " + funde + (funde === 1 ? " Fund" : " Funde")
      : (state.docs.length ? "" : "Keine Dokumente in dieser Unterhaltung");
  }

  function renderDocument() {
    docPane.textContent = "";
    var data = state.reviews[state.currentId];
    var doc = state.docs.filter(function (d) { return d.id === state.currentId; })[0];
    renderFileInfo(data, doc);
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
    // Seitenbreite = verfuegbare Vorschaubreite (bis 1100 px) x Zoom; Aufloesung passend zur angezeigten Groesse und
    // zur Bildschirmskalierung (vorher fest 110/160 dpi bei max. 900 px - klein und bei 125 % Skalierung unscharf)
    holder.style.width = Math.round(state.zoom * 100) + "%";
    holder.style.maxWidth = Math.round(1100 * state.zoom) + "px";
    var shownPx = Math.min(Math.max(docPane.clientWidth - 32, 320), 1100) * state.zoom;
    var firstWidthPt = data.pages[0].width || 595;
    var dpi = Math.ceil(shownPx * (window.devicePixelRatio || 1) / firstWidthPt * 72 / 24) * 24;
    dpi = Math.min(240, Math.max(96, dpi));
    var occCounter = {}; // Rueckfrage-Begriff -> laufende Fundstellennummer (wie `occurrences()`)
    data.pages.forEach(function (page) {
      var wrap = el("figure", "pseudo-page");
      wrap.setAttribute("data-page", String(page.number));
      var box = el("div", "pseudo-page__box");
      box.style.aspectRatio = page.width + " / " + page.height;
      var img = el("img", "pseudo-page__img");
      img.alt = "Seite " + page.number + " (Original)";
      img.loading = "lazy";
      img.src = data.page_url_base + page.number + ".png?dpi=" + dpi;
      box.appendChild(img);
      if (state.view === "marked") {
        drawableMarks(page).forEach(function (mark) {
          var occ = null;
          if (mark.kind === "open") {
            if (!(mark.term in occCounter)) { occCounter[mark.term] = occOffset(mark.term); }
            occ = occCounter[mark.term];
            occCounter[mark.term] += 1;
          }
          box.appendChild(buildMark(mark, occ));
        });
      }
      wrap.appendChild(box);
      wrap.appendChild(el("figcaption", "pseudo-page__caption", "Seite " + page.number + " von " + data.pages.length));
      holder.appendChild(wrap);
    });
    docPane.appendChild(holder);
    markCurrent();
    renderThumbs(data);
    updatePageInfo();
  }

  function renderThumbs(data) {
    thumbs.textContent = "";
    // Seitenleiste dauerhaft (Referenz), sobald es Seitenbilder gibt - nicht im Ergebnis-/Textmodus
    var usable = data && data.viewer_kind === "pages" && data.pages.length > 0 && state.view !== "result";
    thumbs.hidden = !usable;
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
    data.mappings.forEach(function (m) { terms[m.original] = { kind: "masked", term: m.original, group: categoryGroup(m.category) }; });
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
      var mark = el("mark", "pseudo-mark-inline " + (info.kind === "open"
        ? kindClass(decisionKind(info.term), questionGroup(info.term)) : kindClass("masked", info.group)), bestKey);
      mark.setAttribute("data-term", info.term);
      mark.setAttribute("data-kind", info.kind);
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
    $("[data-pseudo-zoom-out]").disabled = state.zoom <= 0.6;
    $("[data-pseudo-zoom-in]").disabled = state.zoom >= 2;
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

  function scrollToMark(term, nth) {
    // Rueckfrage-Fundstellen zuerst (dieselbe Zaehlung wie "Fundstelle i von n"), sonst Funde (Liste "Erkannte Funde")
    var all = docPane.querySelectorAll("[data-kind=\"open\"][data-term=\"" + cssEscape(term) + "\"]");
    if (!all.length) { all = docPane.querySelectorAll("[data-term=\"" + cssEscape(term) + "\"]"); }
    var mark = all[Math.min(nth || 0, Math.max(0, all.length - 1))];
    if (mark) {
      // Fundstelle im oberen Drittel, aber den Seitenanfang nicht unnoetig verlassen: vorher wurde die Markierung
      // zentriert, sodass bei Funden weit oben vor allem der leere untere Teil der VORSEITE zu sehen war (26.10.2026).
      var paneRect = docPane.getBoundingClientRect();
      var markTop = mark.getBoundingClientRect().top - paneRect.top + docPane.scrollTop;
      var pageNode = mark.closest(".pseudo-page");
      var pageTop = pageNode ? pageNode.offsetTop - 8 : markTop;
      var target = markTop - docPane.clientHeight / 3;
      if (markTop - pageTop < docPane.clientHeight * 0.75) { target = pageTop; }
      docPane.scrollTo({ top: Math.max(0, target), behavior: "smooth" });
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
    var text = null, cls;
    var anyBlocked = state.docs.some(function (d) { var r = state.reviews[d.id]; return r && (r.status === "blocked"); });
    if (anyBlocked) {
      cls = "pseudo-center__state pseudo-center__state--blocked";
      text = "Prüfung fehlgeschlagen – mindestens ein Dokument kann nicht verarbeitet werden.";
    } else if (data && data.status === "no_text") {
      cls = "pseudo-center__state pseudo-center__state--blocked";
      text = "Kein verwertbarer Text – keine Verarbeitung möglich.";
    } else if (qs.length) {
      // Bei offenen Rueckfragen spricht der Fortschritt ("Rueckfrage 2 von 5") fuer sich; nur die Chat-Rueckfrage
      // wird als solche benannt.
      if (state.mode === "review") { cls = "pseudo-center__state pseudo-center__state--open"; text = "Datenschutz-Rückfrage zu Ihrer Nachricht"; }
    } else {
      cls = "pseudo-center__state pseudo-center__state--ok";
      text = state.docs.length && state.docs.every(function (d) { return state.reviews[d.id]; })
        ? "Keine offenen Rückfragen" : "Dokumente werden lokal geprüft …";
    }
    if (text) { statusBox.appendChild(el("p", cls, text)); }
    if (state.mode === "view") {
      statusBox.appendChild(el("p", "form-hint", "Kein offener Auftrag – reine Prüfansicht. Stellen Sie Ihre Frage im " +
        "Chat; die Prüfung erfolgt beim Senden erneut."));
    }
    state.docs.forEach(function (d) {
      var r = state.reviews[d.id];
      (r && r.blocked_reasons || []).forEach(function (reason) { statusBox.appendChild(el("p", "form-hint", reason)); });
    });
    statusBox.hidden = !statusBox.childNodes.length;
  }

  /** "Rueckfrage i von n" (Position der angezeigten Frage) + Balken = Anteil beantworteter Rueckfragen. */
  function renderProgress() {
    var qs = questions();
    progressBox.hidden = !qs.length;
    if (!qs.length) { return; }
    var done = qs.length - openTerms().length;
    var percent = Math.round(done * 100 / qs.length);
    var index = activeIndex(qs);
    progressText.textContent = "Rückfrage " + (Math.max(index, 0) + 1) + " von " + qs.length;
    progressPercent.textContent = done + " von " + qs.length + " beantwortet";
    progressBar.setAttribute("aria-valuenow", String(percent));
    progressBar.setAttribute("aria-label", done + " von " + qs.length + " Rückfragen beantwortet");
    progressFill.style.width = percent + "%";
    qPrev.disabled = qs.length < 2 || index <= 0;
    var otherOpen = openTerms().some(function (t) { return t !== state.activeTerm; });
    // Naechste OFFENE Rueckfrage; sind alle beantwortet, blaettert ">" der Reihe nach weiter (Kontrolle vor Bestaetigen)
    qNext.disabled = !otherOpen && index >= qs.length - 1;
    qNext.title = otherOpen ? "Nächste offene Rückfrage" : "Nächste Rückfrage";
    qNext.setAttribute("aria-label", qNext.title);
  }

  function activeIndex(qs) {
    for (var i = 0; i < qs.length; i += 1) { if (qs[i].term === state.activeTerm) { return i; } }
    return -1;
  }

  /** Ende des Vorkontexts mit hoechstens `max` Zeichen, an einer Wortgrenze beginnend. */
  function contextTail(text, max) {
    if (text.length <= max) { return text; }
    var tail = text.slice(text.length - max);
    var space = tail.indexOf(" ");
    return space > -1 && space < tail.length - 1 ? tail.slice(space + 1) : tail;
  }

  /** Genau EINE Rueckfrage (27.10.2026, Referenzbild): Fundkarte (Kategorie, Begriff, Seite, Fundstelle), Kontext im
   *  Dokument, Frage, drei Optionen mit Erklaerung. Keine Liste weiterer Rueckfragen - geblaettert wird ueber den
   *  Fortschritt (Vorherige/Naechste offene) bzw. "Uebernehmen und naechste Rueckfrage". Entscheidungen liegen in
   *  `state.decisions` und bleiben dadurch ueber Fragen-, Dokument- und Ansichtswechsel erhalten. */
  function renderQuestions() {
    questionsBox.textContent = "";
    var qs = questions();
    if (!qs.length) { return; }
    if (!qs.some(function (q) { return q.term === state.activeTerm; })) {
      state.activeTerm = (qs.filter(function (q) { return !state.decisions[q.term]; })[0] || qs[0]).term;
      state.occurrence = 0;
    }
    var q = qs[activeIndex(qs)];
    var kind = decisionKind(q.term);
    var group = questionGroup(q.term);
    var item = el("article", "pseudo-question pseudo-question--" + kind + " is-active");
    item.setAttribute("data-question", q.term);

    // 1) Fund: Kategorie (nur wenn erkannt, sonst "Unsicherer Begriff"), Begriff, Seite, Fundstelle
    var found = el("section", "pseudo-question__found pseudo-question__found--" + group);
    found.appendChild(el("span", "pseudo-question__found-icon")).appendChild(icon(group));
    var main = el("div", "pseudo-question__found-main");
    main.appendChild(el("span", "pseudo-question__label", GROUP_TITLES[group]));
    main.appendChild(el("strong", "pseudo-question__term", q.term));
    found.appendChild(main);
    var where = el("div", "pseudo-question__where-box");
    var occ = occurrences(q);
    if (occ.length) {
      var at = occ[Math.min(state.occurrence, occ.length - 1)];
      where.appendChild(el("span", "pseudo-question__page", "Seite " + at.page));
      var jump = el("button", "pseudo-question__occ", "Fundstelle " + (Math.min(state.occurrence, occ.length - 1) + 1) + " von " + occ.length);
      jump.type = "button";
      jump.title = occ.length > 1 ? "Zur nächsten Fundstelle springen" : "Zur Fundstelle springen";
      jump.addEventListener("click", function () {
        state.occurrence = occ.length > 1 ? (state.occurrence + 1) % occ.length : 0;
        renderQuestions();
        goToQuestion(q);
      });
      where.appendChild(jump);
      if (state.docs.length > 1) { where.appendChild(el("span", "pseudo-question__doc", docName(at.doc))); }
    } else if (q.docs.length) {
      where.appendChild(el("span", "pseudo-question__page", state.docs.length > 1 ? docName(q.docs[0]) : "im Dokument"));
    } else if (q.fromChat) {
      where.appendChild(el("span", "pseudo-question__page", "aus Ihrer Nachricht"));
    }
    found.appendChild(where);
    item.appendChild(found);

    // 2) Kontext im Dokument
    var ctx = el("section", "pseudo-question__context");
    // Rueckfrage beim Senden: der Kontext stammt aus der Chat-Nachricht, auch wenn der Begriff zusaetzlich in einem
    // Dokument markiert ist (native Pruefung 28.10.2026)
    ctx.appendChild(el("h4", "pseudo-question__context-title", q.fromChat ? "Kontext in Ihrer Nachricht" : "Kontext im Dokument"));
    var quote = el("p", "pseudo-question__quote");
    var quoteText = el("span", "pseudo-question__quote-text"); // eigene Zeile fuer die Kuerzung bei niedrigen Fenstern
    // Begriff frueh im Ausschnitt (wie in der Referenz), damit er auch bei gekuerzter Anzeige sichtbar bleibt
    quoteText.appendChild(document.createTextNode("… " + contextTail(q.context_before || "", 40)));
    quoteText.appendChild(el("mark", "pseudo-mark-inline " + kindClass(kind, group), q.term));
    quoteText.appendChild(document.createTextNode((q.context_after || "") + " …"));
    quote.appendChild(quoteText);
    quote.title = (q.context_before || "") + q.term + (q.context_after || "");
    ctx.appendChild(quote);
    item.appendChild(ctx);

    // 3) Frage + Optionen (Radiokarten mit Erklaerung)
    var answers = el("section", "pseudo-question__answers");
    answers.appendChild(el("h3", "pseudo-question__ask", "Was möchten Sie mit diesem Begriff tun?"));
    answers.appendChild(el("p", "pseudo-question__sub", "Soll „" + q.term + "“ vor der Übergabe pseudonymisiert werden?"));
    if (state.mode === "view") {
      // Reine Pruefansicht: die Optionen sind gesperrt - das sagen wir direkt an der Frage, nicht nur im Kopf
      answers.appendChild(el("p", "pseudo-question__view-hint",
        "Nur Ansicht – entscheiden können Sie, sobald Sie Ihren Auftrag im Chat senden."));
    }
    var row = el("div", "pseudo-question__options");
    row.setAttribute("role", "radiogroup");
    row.setAttribute("aria-label", "Antwort zu " + q.term);
    OPTIONS.forEach(function (option) {
      var chosen = state.decisions[q.term] === option.decision;
      var b = el("button", "pseudo-question__option" + (chosen ? " is-selected" : ""));
      b.type = "button";
      b.setAttribute("role", "radio");
      b.setAttribute("data-decision", option.decision);
      b.setAttribute("aria-checked", chosen ? "true" : "false");
      b.setAttribute("aria-pressed", chosen ? "true" : "false");
      b.disabled = state.busy || (state.mode === "view");
      b.title = option.title + " – " + option.hint;
      b.appendChild(el("span", "pseudo-question__option-icon")).appendChild(icon(option.icon));
      var text = el("span", "pseudo-question__option-text");
      text.appendChild(el("strong", "", option.title));
      text.appendChild(el("small", "", option.hint));
      b.appendChild(text);
      b.appendChild(el("span", "pseudo-question__radio"));
      b.addEventListener("click", function () {
        // Nur waehlen - weiter geht es bewusst ueber "Uebernehmen und naechste Rueckfrage" (Auswahl bleibt sichtbar)
        state.decisions[q.term] = option.decision;
        renderDocTabs();
        refreshMarks();
        renderPanel();
        var again = questionsBox.querySelector("[data-decision=\"" + option.decision + "\"]");
        if (again) { again.focus({ preventScroll: true }); }
      });
      row.appendChild(b);
    });
    answers.appendChild(row);
    item.appendChild(answers);
    questionsBox.appendChild(item);
  }

  function goToQuestion(q) {
    activateQuestion(q.term, true, false);
    var occ = occurrences(q);
    var at = occ[Math.min(state.occurrence, Math.max(0, occ.length - 1))];
    if (!at) { if (q.docs.length && q.docs.indexOf(state.currentId) === -1) { selectDoc(q.docs[0], q.term); } return; }
    // n-te Fundstelle INNERHALB des Zieldokuments
    var nth = occ.slice(0, occ.indexOf(at)).filter(function (o) { return o.doc === at.doc; }).length;
    if (at.doc !== state.currentId) { selectDoc(at.doc, q.term, nth); }
    else { if (state.view !== "marked") { setView("marked"); } scrollToMark(q.term, nth); }
  }

  /** Frage anzeigen (Panel + Markierung). `scrollCard`: Panel an den Anfang; `jump`: zur Fundstelle springen. */
  function activateQuestion(term, scrollCard, jump) {
    var changed = state.activeTerm !== term;
    state.activeTerm = term;
    if (changed) { state.occurrence = 0; renderQuestions(); } // es ist immer genau eine Frage dargestellt
    docPane.querySelectorAll(".pseudo-mark[data-kind=\"open\"], .pseudo-mark-inline[data-kind=\"open\"]").forEach(function (node) {
      node.classList.toggle("is-active", node.getAttribute("data-term") === term);
    });
    markCurrent();
    renderProgress();
    renderFooter();
    if (scrollCard && changed) { panelScroll.scrollTop = 0; }
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
      // naechste OFFENE Frage nach der aktuellen (zyklisch); sind alle beantwortet, der Reihe nach
      for (var step = 1; step <= qs.length; step += 1) {
        var candidate = qs[(Math.max(index, -1) + step) % qs.length];
        if (!state.decisions[candidate.term] && candidate.term !== state.activeTerm) { goToQuestion(candidate); return; }
      }
      if (index < qs.length - 1) { goToQuestion(qs[index + 1]); }
      return;
    }
    if (index > 0) { goToQuestion(qs[index - 1]); }
  }

  qPrev.addEventListener("click", function () { stepQuestion(-1); });
  qNext.addEventListener("click", function () { stepQuestion(1); });
  // "Uebernehmen und naechste Rueckfrage": Auswahl der angezeigten Frage steht bereits in state.decisions
  applyBtn.addEventListener("click", function () {
    if (applyBtn.disabled) { return; }
    if (openTerms().length) { stepQuestion(1); }
    renderFooter();
    if (!confirmBtn.hidden && !confirmBtn.disabled) { confirmBtn.focus({ preventScroll: true }); }
  });

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
    if (state.pendingOrder) {
      var shown = state.pendingOrder.length > 60 ? state.pendingOrder.slice(0, 60) + " …" : state.pendingOrder;
      return "Danach wird Ihr Auftrag „" + shown + "“ gesendet.";
    }
    return state.order.label ? "Danach: " + state.order.label + "." : "";
  }

  // Beim Upload mitgetippter, nach §57 NICHT gesendeter Auftrag (liegt nur im Browser, siehe chat.html
  // restoreUnsentUploadText). 29.10.2026: Ist er vorhanden, oeffnet der Dialog nach dem Upload direkt bedienbar und
  // sendet den Auftrag erst mit "Bestaetigen" (bewusste Aktion, serverseitig wie jede Freigabe geprueft) - vorher
  // oeffnete er als reine Pruefansicht mit gesperrten Optionen, und erst ein zweites Senden legte den Auftrag an.
  function unsentOrderKey() { return "lexono-unsent-" + state.conversationId; }

  function readUnsentOrder() {
    var text = "";
    try { text = window.sessionStorage.getItem(unsentOrderKey()) || ""; } catch (e) { text = ""; }
    if (!text && /[?&]text_not_sent=1/.test(window.location.search)) {
      var input = document.getElementById("chat-input");
      text = input ? input.value : "";
    }
    return text.trim();
  }

  function clearUnsentOrder() {
    try { window.sessionStorage.removeItem(unsentOrderKey()); } catch (e) { /* ohne Speicher */ }
    var input = document.getElementById("chat-input");
    if (input && state.pendingOrder && input.value.trim() === state.pendingOrder) { input.value = ""; }
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
    // Tooltip traegt immer den Hinweis (bei niedrigen Fenstern ist die Hinweiszeile der Fussleiste ausgeblendet)
    confirmBtn.title = confirmBtn.disabled ? (lock || openCount.textContent) : openCount.textContent;
    // Solange Rueckfragen offen sind, steht an der Stelle des Bestaetigens "Uebernehmen und naechste Rueckfrage"
    // (erst nach einer Auswahl aktiv); Bestaetigen erscheint, sobald alle beantwortet sind - gesperrt bleibt es nach
    // den Regeln oben, der Server prueft beim Bestaetigen erneut.
    var stepping = state.mode !== "view" && remaining > 0;
    applyBtn.hidden = !stepping;
    if (stepping) {
      confirmBtn.hidden = true;
      var chosen = !!state.decisions[state.activeTerm];
      applyBtn.disabled = state.busy || !chosen;
      // letzte offene Rueckfrage: nach der Auswahl erscheint direkt "Bestaetigen" - kein Verweis auf eine naechste
      applyLabel.textContent = openTerms().some(function (t) { return t !== state.activeTerm; })
        ? "Übernehmen und nächste Rückfrage" : "Option wählen, dann bestätigen";
      applyBtn.title = chosen ? "Auswahl übernehmen und zur nächsten offenen Rückfrage wechseln"
                              : "Bitte zuerst eine Option wählen.";
    }
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
    state.pendingOrder = "";
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
      else if (!state.order.has_open_order && state.docs.length && readUnsentOrder()) {
        state.mode = "release";
        state.pendingOrder = readUnsentOrder();
        // Der Upload-Hinweis ("Text nicht gesendet ... im Chat senden") trifft hier nicht mehr zu
        noticeBox.hidden = false;
        noticeBox.className = "pseudo-center__notice";
        noticeBox.textContent = "Hochgeladen und lokal ausgelesen – bisher wurde nichts an die Cloud gesendet. Ihr " +
          "Auftrag wird erst gesendet, wenn Sie die Pseudonymisierung bestätigen.";
      }
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
  maximizeBtn.addEventListener("click", function () {
    rerenderKeepingPage(function () { setMaximized(!state.maximized); });
  });
  // Fenstergroesse geaendert: Seiten neu bemessen (Breite/Aufloesung), aktuelle Seite bleibt
  var resizeTimer = null;
  window.addEventListener("resize", function () {
    if (!dialog.open) { return; }
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(function () { rerenderKeepingPage(function () {}); }, 250);
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

  // Ohne jedes Server-Ereignis so lange (Standard 5 min) gilt die Verbindung als haengend: die Anzeige endet mit
  // einer verstaendlichen Meldung statt einer endlosen Animation. Der Server verarbeitet die Nachricht hoechstens
  // einmal (Claim) - ein Neuladen zeigt den tatsaechlichen Stand, nichts wird doppelt gesendet.
  function idleLimitMs() { return window.LEXONO_STREAM_IDLE_MS || 300000; }

  function readSse(response, onPayload) {
    if (!response.ok || !response.body) { return Promise.reject(new Error("stream_failed")); }
    var reader = response.body.getReader();
    var decoder = new TextDecoder("utf-8");
    var buffer = "";
    function readWithIdleLimit() {
      return new Promise(function (resolve, reject) {
        var timer = setTimeout(function () {
          try { reader.cancel(); } catch (e) { /* bereits beendet */ }
          reject(new Error("timeout"));
        }, idleLimitMs());
        reader.read().then(function (r) { clearTimeout(timer); resolve(r); },
                           function (e) { clearTimeout(timer); reject(e); });
      });
    }
    function pump() {
      return readWithIdleLimit().then(function (result) {
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

  // Ladezustand (29.10.2026): nach dem Bestaetigen schliesst der Dialog und der Chat zeigt sofort eine Statusblase
  // (window.LexonoChatProgress aus chat.html) mit den TATSAECHLICHEN Serverphasen - keine erfundenen Prozente.
  // Ohne diese Anzeige (anderer Seitenkontext) bleibt der Status wie bisher in der Fussleiste des Dialogs.
  function progressUi() { return window.LexonoChatProgress || null; }

  function startWork(text, orderText) {
    var progress = progressUi();
    if (!progress) { setBusy(true, text); return; }
    state.busy = true;
    if (dialog.open && typeof dialog.close === "function") { dialog.close(); }
    progress.start(text, orderText || "");
  }

  function workFailed(text) {
    var progress = progressUi();
    if (progress) { state.busy = false; progress.error(text); return; }
    if (!dialog.open && typeof dialog.showModal === "function") { dialog.showModal(); }
    showError(text);
  }

  function streamProblem(err) {
    var limit = idleLimitMs();
    var span = limit >= 60000 ? Math.round(limit / 60000) + " Minuten" : Math.round(limit / 1000) + " Sekunden";
    return err && err.message === "timeout"
      ? "Keine Rückmeldung vom Server seit " + span + " – die Anzeige wurde " +
        "beendet. Bitte den Chat neu laden; der Auftrag wird nicht doppelt gesendet."
      : "Die Verbindung wurde unterbrochen. Bitte den Chat neu laden – der tatsächliche Stand wird dann angezeigt.";
  }

  function handleStream(response) {
    var finished = false;
    var progress = progressUi();
    return readSse(response, function (next) {
      if (next.kind === "start") {
        // Auftrag ist serverseitig angelegt/uebernommen: lokale Kopie des Auftrags nicht mehr anbieten
        clearUnsentOrder();
        state.pendingOrder = "";
        state.drafting = !!next.drafting;
        // Adresse von der Upload-Ansicht (?uploaded=1&text_not_sent=1) auf die Unterhaltung setzen: ein Neuladen
        // zeigt dann den tatsaechlichen Stand statt erneut die Pruefansicht mit veraltetem "nicht gesendet"-Hinweis
        var convPath = "/dashboard/chat/" + encodeURIComponent(state.conversationId);
        if (window.history && window.history.replaceState && window.location.pathname !== convPath) {
          window.history.replaceState(null, "", convPath);
        }
        if (progress) { progress.status(state.drafting ? "Schriftsatz wird erstellt …" : "Auftrag wird verarbeitet …"); }
        return;
      }
      if (next.kind === "status") {
        var phase = next.status ? (state.drafting ? "Schriftsatz wird erstellt – " + next.status : next.status) : "";
        if (progress) { progress.status(phase); } else if (phase) { openCount.textContent = phase; }
        return;
      }
      if (next.kind === "review") {
        finished = true;
        if (progress) { progress.pause("Datenschutz-Rückfrage – bitte im Dialog entscheiden."); }
        if (!dialog.open && typeof dialog.showModal === "function") { dialog.showModal(); }
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
        if (next.message_id === null && next.content) { workFailed(next.content); return; }
        if (progress) { progress.status("Ergebnis wird aufbereitet …"); }
        backToChat();
      }
    }).then(function () { if (!finished) { backToChat(); } });
  }

  function submitFollowup(decisions) {
    var form = new FormData();
    form.append("csrf_token", csrfToken());
    form.append("message_id", state.payload.message_id);
    form.append("decisions", JSON.stringify(decisions));
    startWork("Ihre Auswahl wird übernommen – der Inhalt wird erneut geprüft …");
    fetch("/dashboard/chat/" + encodeURIComponent(state.conversationId) + "/privacy-review",
          { method: "POST", body: form, credentials: "same-origin" })
      .then(handleStream).catch(function (err) { workFailed(streamProblem(err)); });
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
    if (state.pendingOrder) { form.append("order", state.pendingOrder); }
    startWork("Pseudonymisierung bestätigt – Dokumente werden freigegeben und erneut geprüft …", state.pendingOrder);
    fetch("/dashboard/chat/" + encodeURIComponent(state.conversationId) + "/confirm-release-and-generate",
          { method: "POST", body: form, credentials: "same-origin" })
      .then(handleStream)
      .catch(function (err) { workFailed(streamProblem(err)); });
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
