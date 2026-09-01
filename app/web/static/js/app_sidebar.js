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
