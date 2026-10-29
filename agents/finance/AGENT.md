# Agent M: Finance (Lexono Finance / Finanzbuchhaltung)

## Verantwortung

Optionales, lizenzpflichtiges Modul "Lexono Finance" (Modulschluessel `finanzen`): Buchungen, Belege, Konten,
Auswertungen, DATEV-Entwurf, Mitarbeiter-Erfassung. Nicht Teil des ersten Kanzlei-Pilotpakets (Build-Profil
`kanzlei-pilot`, siehe `MODULARCHITEKTUR.md`).

## Arbeitsbereich (ab Freigabe 28.10.2026)

- Worktree `C:\Users\Bonit\Lexono-wt-finance`, Branch `feature/finance-pilot`.
- Eigene Python-Umgebung im Worktree (`.venv` dort, NICHT die des Hauptordners: deren Editable-Installation zeigt auf
  `C:\Users\Bonit\Lexono` und wuerde unbemerkt den Kanzlei-Code testen).
- Immer eigenes `LEXONO_DATA_DIR` (nie `C:\ProgramData\Lexono`), QA-Ports 8100-8149.

## Relevante Architektur

- `app/fibu/service.py` (Fachlogik), `app/web/fibu_router.py` (Routen, Router-Dependency `require_module("finanzen")`),
  `app/web/templates/fibu.html`, `app/models/fibu.py`, Migration `schritt3_040`.
- Gemeinsam genutzt (nur koordiniert aendern, siehe Lead-Regeln): `app/modules/*` (Katalog, Lizenz, Mandantenzugriff,
  Build-Profil), `app/auth/permissions.py` (`PERM_FIBU_*`), `app/main.py`, `base.html`, `app/models/__init__.py`.

## Regeln

- Bestehende Implementierung erweitern, nicht neu schreiben.
- Keine Migration mit endgueltiger `down_revision` im Modul-Branch festlegen - die Revision wird erst bei der
  kontrollierten Integration hinter den aktuellen Kopf gesetzt (keine zwei Alembic-Koepfe).
- Keine Aussage zu GoBD-, DATEV- oder steuerlicher Korrektheit ohne fachliche Pruefung.
- Kein Installer installieren (nur der Kanzlei-Strang installiert); schwere Laeufe (volle Regression, Build, native
  Pruefung) nur nach Absprache seriell.
- Ausschliesslich synthetische Daten.

## Relevante Dateien

- `MODULARCHITEKTUR.md`, `benchmarks/legal_answers_v1/qa-evidence/finanzbuchhaltung-mvp-20261028/README.md`
- Tests: `tests/test_fibu.py`, `tests/test_staff_rights.py`, `tests/test_modules.py`
