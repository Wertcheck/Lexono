// Echter Dokumentviewer - Seitennavigation/Thumbnails/Zoom (20.09., Owner-
// Direktive "PRIORITAETSERGAENZUNG: ECHTER DOKUMENTVIEWER").
//
// Bewusst reines Client-JS ohne Framework (identisches Muster wie
// app_sidebar.js) - alle Seiten sind bereits einzeln ueber
// /dashboard/matters/{matter_id}/document/{document_id}/page/{n}.png
// adressierbar (app/web/matters_router.py::matter_document_page_image),
// Seitenwechsel ist daher nur ein <img>-Quellenwechsel, kein HTMX-
// Roundtrip fuer die Viewer-Shell noetig. Zoom skaliert das bereits
// serverseitig bei dpi=150 gerenderte Bild rein per CSS-Breite - echte
// Vergroesserung des tatsaechlich gerenderten Bilds, kein Fake-Button.
(function () {
  "use strict";

  const viewer = document.querySelector(".document-viewer[data-page-count]");
  if (!viewer) {
    return;
  }

  const pageCount = parseInt(viewer.getAttribute("data-page-count"), 10) || 1;
  const urlBase = viewer.getAttribute("data-page-url-base");
  const mainDpi = parseInt(viewer.getAttribute("data-page-dpi"), 10) || 150;
  const pageImage = document.getElementById("doc-viewer-page-image");
  const currentPageLabel = document.getElementById("doc-viewer-current-page");
  const prevBtn = document.getElementById("doc-viewer-prev");
  const nextBtn = document.getElementById("doc-viewer-next");
  const zoomInBtn = document.getElementById("doc-viewer-zoom-in");
  const zoomOutBtn = document.getElementById("doc-viewer-zoom-out");
  const zoomLabel = document.getElementById("doc-viewer-zoom-level");
  const thumbnails = document.getElementById("doc-viewer-thumbnails");

  let currentPage = 1;
  let zoomPercent = 100;
  const MIN_ZOOM = 50;
  const MAX_ZOOM = 250;
  const ZOOM_STEP = 25;

  function goToPage(pageNumber) {
    if (pageNumber < 1 || pageNumber > pageCount || !pageImage) {
      return;
    }
    currentPage = pageNumber;
    pageImage.src = urlBase + "/" + currentPage + ".png?dpi=" + mainDpi;
    if (currentPageLabel) {
      currentPageLabel.textContent = String(currentPage);
    }
    if (thumbnails) {
      thumbnails.querySelectorAll(".document-viewer__thumbnail").forEach(function (btn) {
        const isActive = parseInt(btn.getAttribute("data-page"), 10) === currentPage;
        btn.classList.toggle("document-viewer__thumbnail--active", isActive);
        if (isActive) {
          btn.scrollIntoView({ block: "nearest" });
        }
      });
    }
    if (prevBtn) {
      prevBtn.disabled = currentPage <= 1;
    }
    if (nextBtn) {
      nextBtn.disabled = currentPage >= pageCount;
    }
  }

  function applyZoom() {
    if (!pageImage) {
      return;
    }
    pageImage.style.width = zoomPercent + "%";
    if (zoomLabel) {
      zoomLabel.textContent = zoomPercent + "%";
    }
  }

  if (prevBtn) {
    prevBtn.addEventListener("click", function () {
      goToPage(currentPage - 1);
    });
  }
  if (nextBtn) {
    nextBtn.addEventListener("click", function () {
      goToPage(currentPage + 1);
    });
  }
  if (thumbnails) {
    thumbnails.querySelectorAll(".document-viewer__thumbnail").forEach(function (btn) {
      btn.addEventListener("click", function () {
        goToPage(parseInt(btn.getAttribute("data-page"), 10));
      });
    });
  }
  if (zoomInBtn) {
    zoomInBtn.addEventListener("click", function () {
      zoomPercent = Math.min(MAX_ZOOM, zoomPercent + ZOOM_STEP);
      applyZoom();
    });
  }
  if (zoomOutBtn) {
    zoomOutBtn.addEventListener("click", function () {
      zoomPercent = Math.max(MIN_ZOOM, zoomPercent - ZOOM_STEP);
      applyZoom();
    });
  }

  applyZoom();
})();
