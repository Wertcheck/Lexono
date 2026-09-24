// KI-Waiting-/Buffering-UX (20.09., Owner-Direktive "KI-WAITING-/
// BUFFERING-UX PROJEKTWEIT PRÜFEN UND VERBESSERN") - siehe .btn--ai-loading
// in app.css fuer die Begruendung, warum hier bewusst KEIN Streaming
// verwendet wird (das Ergebnis ist eine vollstaendig neue Seite, kein
// inkrementeller Text auf der aktuellen).
//
// Progressive Enhancement ueber ganz normale <form method="post">-
// Formulare: KEIN preventDefault(), die eigentliche Submission/der
// Redirect laeuft unveraendert - nur der geklickte Button bekommt
// zwischen Klick und Seitenwechsel einen ehrlichen "wird verarbeitet"-
// Zustand statt gar keines.
//
// Anwendung: Formular bekommt die Klasse "js-ai-form", der/die Submit-
// Button(s), die einen ECHTEN KI-Aufruf ausloesen, bekommen zusaetzlich
// "data-ai-loading-label" mit einem ehrlichen Verarbeitungstext. Ein
// Formular kann mehrere Submit-Buttons mit `formaction` haben (z. B.
// "Anmerkung speichern" vs. "Änderungen übernehmen & neu formulieren") -
// nur der tatsaechlich angeklickte Button (`event.submitter`) bekommt den
// Ladezustand; ALLE Submit-Buttons im selben Formular werden gesperrt, um
// einen Doppel-Submit ueber den jeweils anderen Button zu verhindern.
(function () {
  "use strict";

  function applyLoadingState(button) {
    var label = button.getAttribute("data-ai-loading-label") || "Wird verarbeitet …";
    button.dataset.aiOriginalHtml = button.innerHTML;
    button.classList.add("btn--ai-loading");
    button.disabled = true;
    button.innerHTML =
      '<span class="btn__spinner" aria-hidden="true"><span></span><span></span><span></span></span>' +
      "<span>" + label + "</span>";
  }

  function resetLoadingState(button) {
    if (button.dataset.aiOriginalHtml === undefined) { return; }
    button.innerHTML = button.dataset.aiOriginalHtml;
    button.classList.remove("btn--ai-loading");
    button.disabled = false;
    delete button.dataset.aiOriginalHtml;
  }

  function wireForm(form) {
    form.addEventListener("submit", function (evt) {
      var submitButtons = Array.prototype.slice.call(
        form.querySelectorAll('button[type="submit"]')
      );
      var submitter = evt.submitter || submitButtons[0];
      if (!submitter || submitter.disabled) {
        evt.preventDefault();
        return;
      }

      // Formular-eigene Validierung (z. B. "required"-Felder) darf den
      // Ladezustand nicht ausloesen, wenn der Submit dadurch gar nicht
      // stattfindet - checkValidity() vor dem Sperren pruefen.
      if (typeof form.checkValidity === "function" && !form.checkValidity()) {
        return;
      }

      if (submitter.hasAttribute("data-ai-loading-label")) {
        applyLoadingState(submitter);
      }
      submitButtons.forEach(function (btn) {
        if (btn !== submitter) { btn.disabled = true; }
      });
    });
  }

  document.querySelectorAll("form.js-ai-form").forEach(wireForm);

  // Aus dem Browser-Zwischenspeicher (bfcache) zurueckkehrende Seiten
  // (z. B. per Zurueck-Button nach einem Fehler) duerfen NICHT im
  // gesperrten Ladezustand haengen bleiben - echter, bereits an anderer
  // Stelle dieser Sitzung beobachteter Effekt bei Formular-Redirects.
  window.addEventListener("pageshow", function (evt) {
    if (!evt.persisted) { return; }
    document.querySelectorAll(".btn--ai-loading").forEach(resetLoadingState);
    document
      .querySelectorAll('form.js-ai-form button[type="submit"][disabled]')
      .forEach(function (btn) { btn.disabled = false; });
  });
})();
