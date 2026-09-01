// Sidebar-Einklappen (Referenzbild, 01.09.) - persistiert per localStorage
// (derselbe Schluessel wie das synchrone Inline-Skript in base.html, das
// das Aufblitzen der ausgeklappten Breite beim Laden vermeidet). Klappt
// NUR die Navigation ein - das Logo-Icon bleibt laut Vorgabe immer
// sichtbar (siehe CSS: .sidebar__brand-logo wird nie ausgeblendet).
(function () {
  var KEY = "lexono_sidebar_collapsed";
  var toggleBtn = document.getElementById("sidebar-collapse-toggle");
  if (!toggleBtn) { return; }

  toggleBtn.addEventListener("click", function () {
    var collapsed = document.documentElement.classList.toggle("sidebar-collapsed");
    try {
      localStorage.setItem(KEY, collapsed ? "1" : "0");
    } catch (e) {}
  });
})();

// Chat-Historie als Flyout (Nutzerkorrektur 01.09.: keine dauerhaft
// sichtbare Spalte mehr, siehe base.html-Kommentar bei "sidebar-chat-
// link"). .chat-shell existiert nur, wenn die Chat-Seite selbst gerade
// gerendert ist - auf allen anderen Seiten bleibt der Link dadurch ein
// stinknormaler <a href>, da hier gar kein Listener angehaengt wird.
(function () {
  var chatLink = document.getElementById("sidebar-chat-link");
  var chatShell = document.querySelector(".chat-shell");
  if (!chatLink || !chatShell) { return; }

  chatLink.addEventListener("click", function (evt) {
    evt.preventDefault();
    var open = chatShell.classList.toggle("chat-shell--history-open");
    chatLink.setAttribute("aria-expanded", open ? "true" : "false");
  });
})();
