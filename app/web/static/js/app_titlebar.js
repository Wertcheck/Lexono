// Eigene Titelleiste (Masterprompt V2, Task #61) - laeuft auf JEDER Seite,
// die partials/app_titlebar.html einbindet (base.html, login.html,
// unlock.html - siehe dortige Kommentare fuer den real gefundenen Bug,
// der zur Auslagerung in eine gemeinsame Datei fuehrte). Nur aktiv, wenn
// die native pywebview-JS-Bruecke tatsaechlich vorhanden ist (dasselbe
// Feature-Detection-Muster wie settings.html fuer pick_folder). Drag/
// Resize sind bewusst schmal geschnitten (nur die Titelleiste zieht das
// Fenster, nur eine Ecke veraendert die Groesse) statt eines
// vollflaechigen "easy_drag" - pywebview 6.2.1 hat auf Windows ohnehin
// keine eingebaute Hit-Test-Logik dafuer (siehe .agentic/DECISIONS.md).
(function () {
  var titlebar = document.getElementById("app-titlebar");
  var dragRegion = document.getElementById("app-titlebar-drag");
  var minimizeBtn = document.getElementById("app-titlebar-minimize");
  var closeBtn = document.getElementById("app-titlebar-close");
  var resizeHandle = document.getElementById("app-resize-handle");
  if (!titlebar || !dragRegion || !resizeHandle) { return; }

  function nativeApiAvailable() {
    return !!(window.pywebview && window.pywebview.api
      && window.pywebview.api.move_window_by && window.pywebview.api.close_window);
  }

  function activateCustomTitlebar() {
    if (!nativeApiAvailable()) { return; }
    titlebar.style.display = "";
    resizeHandle.style.display = "";
    document.body.classList.add("has-app-titlebar");
  }
  activateCustomTitlebar();
  window.addEventListener("pywebviewready", activateCustomTitlebar);

  var lastX = 0;
  var lastY = 0;

  function onDragMove(evt) {
    var dx = evt.screenX - lastX;
    var dy = evt.screenY - lastY;
    lastX = evt.screenX;
    lastY = evt.screenY;
    if (dx !== 0 || dy !== 0) {
      window.pywebview.api.move_window_by(dx, dy);
    }
  }
  function onDragEnd() {
    document.removeEventListener("mousemove", onDragMove);
    document.removeEventListener("mouseup", onDragEnd);
  }
  dragRegion.addEventListener("mousedown", function (evt) {
    if (!nativeApiAvailable()) { return; }
    lastX = evt.screenX;
    lastY = evt.screenY;
    document.addEventListener("mousemove", onDragMove);
    document.addEventListener("mouseup", onDragEnd);
  });

  var lastRX = 0;
  var lastRY = 0;
  function onResizeMove(evt) {
    var dw = evt.screenX - lastRX;
    var dh = evt.screenY - lastRY;
    lastRX = evt.screenX;
    lastRY = evt.screenY;
    if (dw !== 0 || dh !== 0) {
      window.pywebview.api.resize_window_by(dw, dh);
    }
  }
  function onResizeEnd() {
    document.removeEventListener("mousemove", onResizeMove);
    document.removeEventListener("mouseup", onResizeEnd);
  }
  resizeHandle.addEventListener("mousedown", function (evt) {
    if (!nativeApiAvailable()) { return; }
    evt.preventDefault();
    lastRX = evt.screenX;
    lastRY = evt.screenY;
    document.addEventListener("mousemove", onResizeMove);
    document.addEventListener("mouseup", onResizeEnd);
  });

  if (minimizeBtn) {
    minimizeBtn.addEventListener("click", function () {
      if (nativeApiAvailable()) { window.pywebview.api.minimize_window(); }
    });
  }
  if (closeBtn) {
    closeBtn.addEventListener("click", function () {
      if (window.pywebview && window.pywebview.api && window.pywebview.api.close_window) {
        window.pywebview.api.close_window();
      }
    });
  }
})();
