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

// Profilmenue (UI/UX-Ueberarbeitung, 13.09.; Positionierung neu 04.10.,
// Owner-Direktive "UI-QUALITAETSRUNDE" P2) - "Einstellungen" ist kein
// Hauptmenuepunkt mehr, sondern Teil dieses Dropdowns (Mein Profil/
// Einstellungen/Hilfe & Support/Abmelden). Oeffnet per Klick, schliesst
// per Klick ausserhalb, per Escape oder per erneutem Klick auf den
// Ausloeser - dasselbe einfache, ID-gebundene IIFE-Muster wie oben.
//
// `.sidebar__profile-menu` ist jetzt `position:fixed` (siehe app.css-
// Kommentar dort fuer die volle Root-Cause-Herleitung: die vorherige
// `left:12px;right:12px`-Verankerung relativ zur Sidebar-Breite ergab im
// eingeklappten 68px-Rail eine ~23px schmale, bis zur Unlesbarkeit
// umgebrochene Box). Top/Left werden deshalb hier aus der tatsaechlichen
// Trigger-Position berechnet - bevorzugt nach oben aufklappend (der
// Ausloeser sitzt ganz unten in der Sidebar), faellt aber bei zu wenig
// Platz nach unten um, und wird in jedem Fall an den sichtbaren
// Viewport geklemmt (funktioniert dadurch identisch bei ausgeklappter/
// eingeklappter Sidebar und bei jeder Fensterhoehe/-breite).
(function () {
  var trigger = document.getElementById("sidebar-profile-trigger");
  var menu = document.getElementById("sidebar-profile-menu");
  if (!trigger || !menu) { return; }

  var VIEWPORT_MARGIN = 8;

  function positionMenu() {
    var triggerRect = trigger.getBoundingClientRect();
    var menuRect = menu.getBoundingClientRect();

    var left = triggerRect.left;
    if (left + menuRect.width > window.innerWidth - VIEWPORT_MARGIN) {
      left = window.innerWidth - VIEWPORT_MARGIN - menuRect.width;
    }
    if (left < VIEWPORT_MARGIN) { left = VIEWPORT_MARGIN; }

    var spaceAbove = triggerRect.top - VIEWPORT_MARGIN;
    var spaceBelow = window.innerHeight - triggerRect.bottom - VIEWPORT_MARGIN;
    var top;
    if (spaceAbove >= menuRect.height || spaceAbove >= spaceBelow) {
      // Bevorzugt: nach oben aufklappen (Standardfall, Trigger sitzt
      // unten in der Sidebar).
      top = triggerRect.top - menuRect.height - 6;
      if (top < VIEWPORT_MARGIN) { top = VIEWPORT_MARGIN; }
    } else {
      // Zu wenig Platz oberhalb, aber mehr Platz unterhalb - umklappen.
      top = triggerRect.bottom + 6;
      if (top + menuRect.height > window.innerHeight - VIEWPORT_MARGIN) {
        top = window.innerHeight - VIEWPORT_MARGIN - menuRect.height;
      }
    }

    menu.style.left = left + "px";
    menu.style.top = top + "px";
  }

  function closeMenu() {
    menu.hidden = true;
    trigger.setAttribute("aria-expanded", "false");
  }

  function openMenu() {
    menu.style.visibility = "hidden";
    menu.hidden = false;
    positionMenu();
    menu.style.visibility = "";
    trigger.setAttribute("aria-expanded", "true");
  }

  trigger.addEventListener("click", function (evt) {
    evt.stopPropagation();
    if (menu.hidden) { openMenu(); } else { closeMenu(); }
  });

  document.addEventListener("click", function (evt) {
    if (!menu.hidden && !menu.contains(evt.target) && evt.target !== trigger) {
      closeMenu();
    }
  });

  document.addEventListener("keydown", function (evt) {
    if (evt.key === "Escape" && !menu.hidden) {
      closeMenu();
      trigger.focus();
    }
  });
})();

// Dateiformat-Badge fuer Datei-Upload-Vorschauen (19.09., Owner-Direktive
// "Dateiformat-Icons") - Client-seitiges Gegenstueck zu
// icons.file_type_badge() (app/web/templates/_icons.html): dort fuer
// bereits gespeicherte Dokumente (Jinja, kennt document.original_filename),
// hier fuer die Dropzone-Dateivorschau VOR dem eigentlichen Upload (kennt
// nur das rohe Browser-File-Objekt). Bewusst EINE gemeinsame Funktion
// (hier in app_sidebar.js, auf jeder Seite via base.html geladen) statt
// einer dritten unabhaengigen Kopie in chat.html/clients_list.html/
// schriftsatz_generator.html, die je eigene Dropzone-Vorschau-Chips per JS
// erzeugen. Dieselbe Farb-/Text-Zuordnung wie serverseitig, damit ein
// hochgeladenes PDF vor UND nach dem Upload gleich aussieht.
function lexonoFileTypeBadge(filename) {
  var ext = "";
  if (filename && filename.lastIndexOf(".") > -1) {
    ext = filename.slice(filename.lastIndexOf(".") + 1).toLowerCase();
  }
  var span = document.createElement("span");
  span.className = "file-type-badge";
  if (ext === "pdf") {
    span.className += " file-type-badge--pdf";
    span.textContent = "PDF";
    span.title = "PDF-Dokument";
  } else if (ext === "docx" || ext === "doc") {
    span.className += " file-type-badge--docx";
    span.textContent = ext.toUpperCase();
    span.title = "Word-Dokument";
  } else if (["png", "jpg", "jpeg", "tif", "tiff", "bmp", "gif"].indexOf(ext) > -1) {
    span.className += " file-type-badge--image";
    span.textContent = ext.toUpperCase().slice(0, 4);
    span.title = "Bilddatei";
  } else if (ext === "txt") {
    span.className += " file-type-badge--txt";
    span.textContent = "TXT";
    span.title = "Textdatei";
  } else if (ext) {
    span.className += " file-type-badge--generic";
    span.textContent = ext.toUpperCase().slice(0, 4);
    span.title = ext.toUpperCase() + "-Datei";
  } else {
    span.className += " file-type-badge--generic";
    span.textContent = "?";
    span.title = "Unbekannter Dateityp";
  }
  return span;
}
