/* "Namen schützen" (PRIVACY_REVIEW_ARCHITECTURE.md §51-§54) - EIN Baustein fuer Chat, Schriftsatz, Aenderungsauftrag,
 * Editor und E-Mail-Aktionen. Eine Auswahl ist EIN Name ("Hans-Peter Müller", "Anna von Berg", "O'Brien"); fuehrende
 * Anrede, Titel oder Rolle ("Dr.", "Herr", "Richter") gehoert nicht dazu und bleibt lesbar. Ein Name gilt als
 * vorhanden, wenn er als ganze Wortfolge (auch in Versalien, im Genitiv, ueber einen Zeilenumbruch, mit geradem oder
 * typografischem Apostroph) im gebundenen Text steht - wie app/privacy/review.detect_decision_terms. Kommt ein
 * markierter Name nach einer Textaenderung nicht mehr vor, ist die Markierung veraltet: dann wird nicht gesendet.
 * Der Server prueft dasselbe noch einmal (app/privacy/decisions.parse_manual_terms) - der Browser ist nur Komfort. */
(function () {
  "use strict";

  var MAX_TERMS = 20;
  var config = { prefix: [], particles: [] };
  try {
    var el = document.getElementById("lexono-name-protection-config");
    if (el) { config = JSON.parse(el.textContent); }
  } catch (err) { /* ohne Listen: der Server lehnt Anrede/Titel/Rolle ohnehin ab */ }

  function escapeRegExp(value) { return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"); }

  function termOccursIn(text, term) {
    var gap = "(?:[ \\t]+|[ \\t]*\\r?\\n[ \\t]*)";
    function body(value) { return value.split(" ").map(escapeRegExp).join(gap).replace(/['’]/g, "['’]"); }
    var pattern = body(term);
    if (term.toUpperCase() !== term) { pattern = "(?:" + pattern + "|" + body(term.toUpperCase()) + ")"; }
    return new RegExp("(?:^|[^\\p{L}\\p{N}_])" + pattern + "(?=(?:s|'s|’s)?(?:[^\\p{L}\\p{N}_]|$))", "u").test(text);
  }

  function isPrefixWord(word) {
    return word.toLowerCase().replace(/[.:]+$/, "").split(/[.-]/).every(function (part) {
      return part === "" || config.prefix.indexOf(part) >= 0;
    });
  }

  function isNameShaped(words) {
    if (!words.length || words.length > 8) { return false; }
    var ok = words.every(function (word) {
      if (!/^\p{L}+(?:[-'’]\p{L}+)*$/u.test(word)) { return false; }
      return word[0] !== word[0].toLowerCase() || config.particles.indexOf(word) >= 0;
    });
    var last = words[words.length - 1];
    return ok && last[0] !== last[0].toLowerCase() && words.join(" ").length <= 80;
  }

  /* Auswahl -> {name} oder {error} */
  function nameFromSelection(selected) {
    var words = (selected || "").split(/\s+/).filter(function (word) { return word.length > 0; });
    while (words.length && isPrefixWord(words[0])) { words.shift(); }
    words = words.map(function (word) { return word.replace(/^[^\p{L}]+|[^\p{L}]+$/gu, ""); })
      .filter(function (word) { return word.length > 0; });
    if (!words.length) {
      return { error: (selected || "").trim()
        ? "Bitte den Namen selbst markieren (ohne Anrede, Titel oder Rolle)."
        : "Bitte zuerst den Namen im Text markieren, dann „Namen schützen“ wählen." };
    }
    if (!isNameShaped(words)) {
      return { error: "Nur Namen können geschützt werden (Buchstaben, Bindestrich, Namenszusatz wie „von“): „" +
        words.join(" ") + "“." };
    }
    return { name: words.join(" ") };
  }

  /* opts: button, list, fields (Array versteckter Felder "protected_terms"), getText() -> gebundener Text,
   *       getSelectedText() -> aktuelle Auswahl, onError(msg), onOk(), watch (Array von Eingabeelementen) */
  function create(opts) {
    var terms = [];
    var fields = (opts.fields || []).filter(Boolean);

    function stale() {
      var text = opts.getText();
      return terms.filter(function (term) { return !termOccursIn(text, term); });
    }

    function render() {
      fields.forEach(function (field) { field.value = terms.length ? JSON.stringify(terms) : ""; });
      if (!opts.list) { return; }
      opts.list.innerHTML = "";
      var staleTerms = stale();
      terms.forEach(function (term) {
        var isStale = staleTerms.indexOf(term) >= 0;
        var item = document.createElement("li");
        item.className = "chat-composer__protected-item" + (isStale ? " chat-composer__protected-item--stale" : "");
        item.title = isStale ? "Kommt im Text nicht mehr vor – Markierung entfernen oder Text prüfen"
          : "Wird vor der Cloud-Übergabe pseudonymisiert";
        var label = document.createElement("span");
        label.textContent = term;
        var remove = document.createElement("button");
        remove.type = "button";
        remove.className = "chat-composer__protected-remove";
        remove.setAttribute("aria-label", "Markierung „" + term + "“ entfernen");
        remove.textContent = "×";
        remove.addEventListener("click", function () {
          setTerms(terms.filter(function (other) { return other !== term; }));
        });
        item.appendChild(label);
        item.appendChild(remove);
        opts.list.appendChild(item);
      });
    }

    function setTerms(next) { terms = (next || []).slice(); render(); }

    if (opts.button) {
      // Fokus/Auswahl behalten, waehrend der Knopf gedrueckt wird.
      opts.button.addEventListener("mousedown", function (evt) { evt.preventDefault(); });
      opts.button.addEventListener("click", function () {
        var result = nameFromSelection(opts.getSelectedText());
        if (result.error) { opts.onError(result.error); return; }
        var next = terms.slice();
        if (next.indexOf(result.name) < 0) { next.push(result.name); }
        if (next.length > MAX_TERMS) { opts.onError("Höchstens " + MAX_TERMS + " Namen je Anfrage."); return; }
        if (opts.onOk) { opts.onOk(); }
        setTerms(next);
      });
    }
    (opts.watch || []).filter(Boolean).forEach(function (input) {
      input.addEventListener("input", function () { if (terms.length) { render(); } });
    });

    return {
      terms: function () { return terms.slice(); },
      setTerms: setTerms,
      stale: stale,
      staleMessage: function () {
        var staleTerms = stale();
        return staleTerms.length ? "Der markierte Name „" + staleTerms.join("“, „") +
          "“ kommt im Text nicht mehr vor. Bitte Markierung entfernen oder Text prüfen." : "";
      }
    };
  }

  /* Klassisches Formular: veraltete Markierung -> Absenden verhindern (Capture-Phase, vor der Ladeanzeige);
   * gueltig -> `onValid` (z. B. einen frueheren Hinweis ausblenden). */
  function guardForm(form, widget, onError, onValid) {
    if (!form || !widget) { return; }
    form.addEventListener("submit", function (evt) {
      var message = widget.staleMessage();
      if (message) {
        evt.preventDefault();
        evt.stopImmediatePropagation();
        onError(message);
      } else if (onValid) {
        onValid();
      }
    }, true);
  }

  /* Auswahl im Dokument, aber nur innerhalb der angegebenen Elemente (z. B. E-Mail-Text, Entwurfsansicht). */
  function selectionWithin(elements) {
    var active = document.activeElement;
    if (active && (active.tagName === "TEXTAREA" || (active.tagName === "INPUT" && active.type === "text")) &&
        elements.indexOf(active) >= 0 && active.selectionEnd > active.selectionStart) {
      return active.value.slice(active.selectionStart, active.selectionEnd);
    }
    var sel = window.getSelection ? window.getSelection() : null;
    if (!sel || sel.rangeCount === 0 || sel.isCollapsed) { return ""; }
    var node = sel.getRangeAt(0).commonAncestorContainer;
    var inside = elements.some(function (el) { return el && el.contains(node); });
    return inside ? sel.toString() : "";
  }

  window.LexonoNameProtection = {
    create: create,
    guardForm: guardForm,
    selectionWithin: selectionWithin,
    termOccursIn: termOccursIn,
    nameFromSelection: nameFromSelection
  };
})();
