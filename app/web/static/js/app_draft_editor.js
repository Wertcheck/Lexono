/* app_draft_editor.js - Rich-Text-Dokumenten-Editor (04.10., Owner-Direktive
 * "LEXONO - Dokumenten-Editor produktionsnah implementieren und vollstaendig
 * in den Chat-Workflow integrieren"). Siehe app/web/draft_editor_router.py
 * fuer die Backend-Endpunkte, auf die hier ausschliesslich zugegriffen wird -
 * KEINE Logik hier dupliziert etwas, das der Server bereits entscheidet
 * (Privacy-Gateway, Versionierung, Berechtigungen laufen serverseitig,
 * dieses Skript ruft nur die bestehenden/neuen Endpunkte auf).
 *
 * `document.execCommand` ist browserseitig als deprecated markiert, aber in
 * der WebView2/Chromium-Runtime, die Lexono nativ einsetzt (siehe run.py),
 * weiterhin vollstaendig unterstuetzt - eine vollstaendige eigene
 * Rich-Text-Engine (Selection-/Range-Manipulation von Grund auf) waere ein
 * um Groessenordnungen groesserer, hier nicht verhaeltnismaessiger Aufwand
 * fuer denselben Nutzen, siehe CLAUDE.md "Architektur vor kurzfristigen
 * Hacks" - dieser Einsatz ist hinter `_exec()` isoliert, damit ein spaeterer
 * Ersatz an einer Stelle moeglich bleibt. */
(function () {
  "use strict";

  const root = document.getElementById("draft-editor-root");
  if (!root) return;

  const draftId = root.dataset.draftId;
  const isDraftStatus = root.dataset.isDraftStatus === "1";
  const canClaudeCall = root.dataset.canClaudeCall === "1";
  const csrfTokenInput = document.getElementById("draft-editor-csrf-token");
  const surface = document.getElementById("draft-editor-surface");
  const subjectInput = document.getElementById("draft-editor-subject");
  const recipientInput = document.getElementById("draft-editor-recipient");
  const wordCountEl = document.getElementById("draft-editor-wordcount");
  const saveStateEl = document.getElementById("draft-editor-savestate");

  function csrfToken() {
    return csrfTokenInput ? csrfTokenInput.value : "";
  }

  function postForm(url, fields) {
    const body = new URLSearchParams();
    Object.keys(fields).forEach((key) => body.append(key, fields[key] == null ? "" : fields[key]));
    body.append("csrf_token", csrfToken());
    return fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: body.toString(),
    });
  }

  function _exec(cmd, value) {
    surface.focus();
    document.execCommand(cmd, false, value || null);
  }

  // --- Wortanzahl -----------------------------------------------------
  function updateWordCount() {
    const text = (surface.textContent || "").trim();
    const count = text ? text.split(/\s+/).length : 0;
    wordCountEl.textContent = "Wortanzahl: " + count;
  }

  // --- Mehrseitige DIN-A4-Darstellung (06.10., Owner-Direktive
  // "Schriftsatz-Workflow, Pseudonymisierung, lokale KI und
  // DIN-A4-Dokumentdarstellung", Phase 11) ------------------------------
  //
  // WICHTIG: `#draft-editor-surface` bleibt das EINZIGE `contenteditable`-
  // Element - `repaginate()` fuegt nur strukturierende `.editor-page`-
  // Huellen als KINDER innerhalb desselben editierbaren Bereichs ein
  // (keine zweite Editor-Engine, keine zweite Selection-/execCommand-
  // Zone). Diese Huellen sind reine Praesentation: `getFlatContentHtml()`
  // entfernt sie wieder, BEVOR irgendetwas an den Server gesendet wird -
  // `Draft.content` enthaelt daher NIE Pagination-Artefakte (Single
  // Source of Truth bleibt der flache Inhalt, siehe Phase 10).
  //
  // Bewusst NICHT zeichenbasiert (Direktive: "NICHT charactersPerPage"):
  // jede Seite wird anhand der TATSAECHLICH gerenderten Hoehe ihrer
  // Block-Kinder befuellt (`getBoundingClientRect`/`scrollHeight`), ein
  // Umbruch findet ausschliesslich ZWISCHEN Bloecken statt (nie mitten im
  // Wort/Absatz). Eine Ueberschrift, die als letztes Element einer Seite
  // landen wuerde, wird auf die naechste Seite verschoben (kein
  // verwaistes Element am Seitenende).
  const PAGE_RATIO = 297 / 210; // DIN A4 Hoehe/Breite
  const PAGE_PADDING_Y = 32 + 44; // muss .editor-page-CSS padding-top/-bottom entsprechen

  function unwrapPages(root) {
    root.querySelectorAll(".editor-page").forEach((page) => {
      while (page.firstChild) {
        page.parentNode.insertBefore(page.firstChild, page);
      }
      page.remove();
    });
  }

  function createPageEl() {
    const page = document.createElement("div");
    page.className = "editor-page";
    return page;
  }

  function getUsablePageHeight() {
    // Breite misst sich an `surface` selbst (vor dem Umbruch identisch
    // mit der spaeteren `.editor-page`-Breite, da beide `max-width:794px`
    // + `width:100%` im selben Elternelement nutzen) - damit passt sich
    // die Pro-Seite-Kapazitaet automatisch an schmalere Viewports an
    // (/ux-responsive), statt eine feste Pixelzahl anzunehmen.
    const width = Math.min(surface.clientWidth || 794, 794);
    return Math.max(200, width * PAGE_RATIO - PAGE_PADDING_Y);
  }

  let isRepaginating = false;

  function repaginate() {
    if (!surface) return;
    // Waehrend `unwrapPages()`/Umhaengen selbst feuert kein zusaetzlicher
    // "input"-Event (reines DOM-Reparenting), aber defensiv trotzdem
    // re-entrant geschuetzt.
    if (isRepaginating) return;
    isRepaginating = true;
    try {
      unwrapPages(surface);
      const blocks = Array.from(surface.children);
      if (blocks.length <= 1) {
        // Nichts zu verteilen bzw. nur ein einzelner Block - lohnt sich
        // nicht als "Seite" zu umhuellen; wie bisher eine einzelne
        // Flaeche (keine Regression fuer den haeufigen kurzen Entwurf).
        surface.classList.remove("editor-paginated");
        return;
      }

      const usableHeight = getUsablePageHeight();
      const pages = [];
      let current = createPageEl();
      surface.insertBefore(current, blocks[0]);
      pages.push(current);

      blocks.forEach((block) => {
        current.appendChild(block);
        if (current.scrollHeight > usableHeight && current.children.length > 1) {
          // Dieser letzte Block passt nicht mehr auf die aktuelle Seite -
          // auf eine neue Seite verschieben (Umbruch IMMER zwischen
          // Bloecken, nie innerhalb eines Blocks).
          current.removeChild(block);
          current = createPageEl();
          surface.appendChild(current);
          pages.push(current);
          current.appendChild(block);
        }
      });

      // Verwaiste Ueberschrift am Seitenende vermeiden: landet eine
      // Ueberschrift (H1-H4) als LETZTES Element einer Nicht-Endseite,
      // wandert sie auf den Anfang der naechsten Seite.
      for (let i = 0; i < pages.length - 1; i++) {
        const page = pages[i];
        const last = page.lastElementChild;
        if (last && /^H[1-4]$/.test(last.tagName) && page.children.length > 1) {
          page.removeChild(last);
          pages[i + 1].insertBefore(last, pages[i + 1].firstChild);
        }
      }

      // Leere gewordene Seiten (z. B. durch die Ueberschrift-Verschiebung
      // oben) wieder entfernen.
      pages.filter((p) => p.children.length === 0).forEach((p) => p.remove());

      // Bei nur einer tatsaechlichen Seite: wieder entpacken, damit die
      // bisherige einfache Darstellung (kein `.editor-page`-Rahmen) fuer
      // den Regelfall (kurzer, einseitiger Entwurf) unveraendert bleibt.
      if (surface.querySelectorAll(".editor-page").length <= 1) {
        unwrapPages(surface);
        surface.classList.remove("editor-paginated");
      } else {
        surface.classList.add("editor-paginated");
      }
    } finally {
      isRepaginating = false;
    }
  }

  let repaginateTimer = null;
  function scheduleRepaginate() {
    if (repaginateTimer) clearTimeout(repaginateTimer);
    // Kuerzeres Delay als Autosave (1500ms) - die visuelle Seitenaufteilung
    // soll sich fluessig anfuehlen, ohne bei jedem einzelnen Tastendruck
    // synchron das DOM umzubauen.
    repaginateTimer = setTimeout(repaginate, 350);
  }

  function getFlatContentHtml() {
    // Arbeitet auf einem KLON, NIE auf dem Live-DOM - `repaginate()` lief
    // bereits fuer die sichtbare Darstellung; das tatsaechlich gesendete
    // `innerHTML` muss trotzdem IMMER die entpackte, flache Form sein,
    // unabhaengig vom aktuell sichtbaren Pagination-Zustand.
    if (!surface) return "";
    const clone = surface.cloneNode(true);
    unwrapPages(clone);
    return clone.innerHTML;
  }

  // --- Autosave (Statuswechsel Pending/Saving/Saved/Failed) ------------
  let autosaveTimer = null;
  let lastSavedSnapshot = getFlatContentHtml();
  let hasUnsavedChanges = false;

  function scheduleAutosave() {
    if (!isDraftStatus) return;
    hasUnsavedChanges = true;
    saveStateEl.textContent = "Änderung ausstehend …";
    saveStateEl.classList.remove("draft-editor-statusbar__state--error");
    if (autosaveTimer) clearTimeout(autosaveTimer);
    autosaveTimer = setTimeout(runAutosave, 1500);
  }

  function runAutosave() {
    if (!isDraftStatus) return;
    saveStateEl.textContent = "Speichert …";
    postForm("/dashboard/drafts/" + draftId + "/autosave", {
      content: getFlatContentHtml(),
      subject: subjectInput.value,
      recipient: recipientInput.value,
      content_format: "html",
    })
      .then((resp) => resp.json())
      .then((data) => {
        if (data.saved) {
          hasUnsavedChanges = false;
          lastSavedSnapshot = getFlatContentHtml();
          const when = data.last_autosaved_at ? new Date(data.last_autosaved_at) : new Date();
          saveStateEl.textContent = "Entwurf automatisch gespeichert (" + when.toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" }) + " Uhr)";
        } else {
          saveStateEl.textContent = "Nicht gespeichert - Entwurf ist eingefroren (" + (data.status || "unbekannt") + ")";
          saveStateEl.classList.add("draft-editor-statusbar__state--error");
        }
      })
      .catch(() => {
        saveStateEl.textContent = "Speichern fehlgeschlagen - wird erneut versucht";
        saveStateEl.classList.add("draft-editor-statusbar__state--error");
      });
  }

  if (surface) {
    updateWordCount();
    surface.addEventListener("input", () => {
      updateWordCount();
      scheduleAutosave();
      scheduleRepaginate();
    });
    // Initiale Seitenaufteilung (lang genug verzoegert, damit der Browser
    // das zunaechst ungeteilte Markup erst einmal real ausgerendert hat -
    // `getUsablePageHeight()` braucht eine tatsaechliche, bereits
    // layoutete `surface.clientWidth`).
    setTimeout(repaginate, 50);
    // Responsive (/ux-responsive): bei Breitenaenderung (z. B. Tablet-
    // Rotation, Fenstergroesse) muss die Pro-Seite-Kapazitaet neu
    // berechnet werden - dieselbe Debounce-Funktion wie bei Texteingaben.
    window.addEventListener("resize", scheduleRepaginate);
  }
  if (subjectInput) subjectInput.addEventListener("input", scheduleAutosave);
  if (recipientInput) recipientInput.addEventListener("input", scheduleAutosave);

  window.addEventListener("beforeunload", (event) => {
    if (hasUnsavedChanges) {
      event.preventDefault();
      event.returnValue = "";
    }
  });

  // --- Toolbar ----------------------------------------------------------
  document.querySelectorAll(".draft-editor-toolbar__btn[data-cmd]").forEach((btn) => {
    btn.addEventListener("click", () => _exec(btn.dataset.cmd));
  });

  const paragraphStyleSelect = document.getElementById("draft-editor-paragraph-style");
  if (paragraphStyleSelect) {
    paragraphStyleSelect.addEventListener("change", () => {
      _exec("formatBlock", paragraphStyleSelect.value === "h2" ? "H2" : "P");
    });
  }

  const linkBtn = document.querySelector('.draft-editor-toolbar__btn[data-action="link"]');
  if (linkBtn) {
    linkBtn.addEventListener("click", () => {
      const url = window.prompt("Link-Ziel (URL) eingeben:", "https://");
      if (url) _exec("createLink", url);
    });
  }

  // --- Letzte Textauswahl im Editor merken (fuer KI-Aktionen) -----------
  let lastSelectionText = "";
  function captureSelection() {
    const selection = window.getSelection();
    if (selection && selection.rangeCount > 0 && surface.contains(selection.anchorNode)) {
      const text = selection.toString();
      if (text && text.trim()) lastSelectionText = text.trim();
    }
  }
  if (surface) {
    surface.addEventListener("mouseup", captureSelection);
    surface.addEventListener("keyup", captureSelection);
  }

  // --- KI-Vorschlagsvorschau (Accept/Discard) ----------------------------
  const previewPanel = document.getElementById("draft-editor-ai-preview");
  const previewContent = document.getElementById("draft-editor-ai-preview-content");
  const acceptBtn = document.getElementById("draft-editor-ai-accept-btn");
  const discardBtn = document.getElementById("draft-editor-ai-discard-btn");
  let pendingSuggestionDraftId = null;

  function runAiEdit(instructionText, purpose, selectedText, triggerBtn) {
    if (!canClaudeCall || !instructionText || !instructionText.trim()) return;
    if (triggerBtn) {
      triggerBtn.disabled = true;
      triggerBtn.dataset.originalText = triggerBtn.dataset.originalText || triggerBtn.textContent;
      triggerBtn.textContent = "Wird bearbeitet …";
    }
    postForm("/dashboard/drafts/" + draftId + "/ai-edit", {
      instruction_text: instructionText,
      purpose: purpose || "improve_draft",
      selected_text: selectedText || "",
    })
      .then((resp) => resp.json())
      .then((data) => {
        if (!data.success) {
          window.alert(data.error || "KI-Bearbeitung fehlgeschlagen.");
          return;
        }
        pendingSuggestionDraftId = data.new_draft_id;
        previewContent.innerHTML = data.content_format === "html" ? data.content : escapeHtml(data.content).replace(/\n/g, "<br>");
        previewPanel.hidden = false;
        previewPanel.scrollIntoView({ behavior: "smooth", block: "nearest" });
      })
      .catch(() => window.alert("KI-Bearbeitung fehlgeschlagen (Netzwerkfehler)."))
      .finally(() => {
        if (triggerBtn) {
          triggerBtn.disabled = false;
          triggerBtn.textContent = triggerBtn.dataset.originalText;
        }
      });
  }

  function escapeHtml(text) {
    const div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
  }

  if (acceptBtn) {
    acceptBtn.addEventListener("click", () => {
      if (pendingSuggestionDraftId) {
        window.location.href = "/dashboard/drafts/" + pendingSuggestionDraftId + "/edit";
      }
    });
  }
  if (discardBtn) {
    discardBtn.addEventListener("click", () => {
      if (!pendingSuggestionDraftId) {
        previewPanel.hidden = true;
        return;
      }
      postForm("/dashboard/drafts/" + pendingSuggestionDraftId + "/ai-edit/discard", {})
        .finally(() => {
          previewPanel.hidden = true;
          pendingSuggestionDraftId = null;
        });
    });
  }

  // Feste Vorschlaege (Sidebar "Vorschläge").
  document.querySelectorAll(".draft-editor-ai-suggestion-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      captureSelection();
      runAiEdit(btn.dataset.instruction, btn.dataset.purpose, lastSelectionText, btn);
    });
  });

  // Freitext-Anweisung (Composer unten in der Seitenleiste).
  const instructionText = document.getElementById("draft-editor-instruction-text");
  const sendInstructionBtn = document.getElementById("draft-editor-send-instruction-btn");
  if (sendInstructionBtn) {
    sendInstructionBtn.addEventListener("click", () => {
      captureSelection();
      runAiEdit(instructionText.value, "improve_draft", lastSelectionText, sendInstructionBtn);
    });
  }

  // Toolbar "Mit KI bearbeiten" - fokussiert den bestehenden Composer statt
  // einen zweiten, unklaren KI-Pfad zu erfinden (derselbe Grundsatz wie die
  // Standard-Prompts: der Anwalt sieht/bestaetigt die Anweisung, bevor ein
  // kostenpflichtiger Aufruf ausgeloest wird).
  const aiEditSelectionBtn = document.getElementById("draft-editor-ai-edit-selection-btn");
  if (aiEditSelectionBtn && instructionText) {
    aiEditSelectionBtn.addEventListener("click", () => {
      captureSelection();
      instructionText.focus();
      if (lastSelectionText) {
        instructionText.placeholder = "Anweisung für den markierten Text (" + lastSelectionText.length + " Zeichen ausgewählt) …";
      }
    });
  }

  // "Anhängen" (📎) - zeigt ehrlich die tatsaechlich mitgesendete
  // Textauswahl an, statt einen nicht existierenden Datei-Upload
  // vorzutaeuschen (CLAUDE.md "Keine Fake-Vollstaendigkeit").
  const attachBtn = document.querySelector(".draft-editor-attach-btn");
  if (attachBtn) {
    attachBtn.addEventListener("click", () => {
      captureSelection();
      if (lastSelectionText) {
        window.alert("Ausgewählter Text wird als Kontext angehängt (" + lastSelectionText.length + " Zeichen).");
      } else {
        window.alert("Keine Textauswahl im Entwurf vorhanden - markieren Sie zuerst einen Textabschnitt.");
      }
    });
  }

  // Standard-Prompts -> Composer vorausfuellen (identisches Prinzip wie
  // draft_detail.html).
  document.querySelectorAll("[data-prefill-instruction]").forEach((btn) => {
    btn.addEventListener("click", () => {
      instructionText.value = btn.getAttribute("data-prefill-instruction") || "";
      instructionText.focus();
    });
  });

  // --- Vorlagen-Tab -------------------------------------------------------
  const tabs = document.querySelectorAll(".draft-editor-tab");
  tabs.forEach((tab) => {
    tab.addEventListener("click", () => {
      tabs.forEach((t) => {
        t.classList.toggle("draft-editor-tab--active", t === tab);
        t.setAttribute("aria-selected", t === tab ? "true" : "false");
      });
      document.querySelectorAll("[data-tab-panel]").forEach((panel) => {
        panel.hidden = panel.dataset.tabPanel !== tab.dataset.tab;
      });
    });
  });

  document.querySelectorAll(".draft-editor-insert-template-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      if (!isDraftStatus) return;
      surface.focus();
      const content = btn.dataset.templateContent || "";
      document.execCommand("insertText", false, content);
      updateWordCount();
      scheduleAutosave();
      scheduleRepaginate();
    });
  });

  // --- "Als Vorlage speichern" -------------------------------------------
  const saveTemplateBtn = document.getElementById("draft-editor-save-template-btn");
  if (saveTemplateBtn) {
    saveTemplateBtn.addEventListener("click", () => {
      const name = window.prompt("Name der neuen Vorlage:", subjectInput.value || "Neue Vorlage");
      if (!name || !name.trim()) return;
      postForm("/dashboard/drafts/" + draftId + "/save-as-template", { name: name.trim() })
        .then((resp) => resp.json())
        .then((data) => {
          if (data.success) {
            window.alert('Vorlage "' + data.template_name + '" gespeichert.');
          } else {
            window.alert(data.error || "Vorlage konnte nicht gespeichert werden.");
          }
        })
        .catch(() => window.alert("Vorlage konnte nicht gespeichert werden (Netzwerkfehler)."));
    });
  }
})();
