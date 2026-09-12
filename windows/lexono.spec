# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller-Spec für die Windows-Installation (Prompt 36; natives
Fenster Prompt 46).

Erzeugt einen "onedir"-Build (bewusst KEIN "onefile"): onefile extrahiert
sich bei JEDEM Start neu in ein temporäres Verzeichnis (spürbar langsamerer
Start, zusätzlicher Schreibzugriff bei jedem Programmstart, schwerer
nachvollziehbare Pfadprobleme). "onedir" passt außerdem direkt zu Inno Setup
(siehe windows/installer.iss), das den erzeugten Ordner 1:1 unter
"Program Files" installiert.

`console=True` bleibt bewusst auch nach Prompt 46 gesetzt: der Setup-
Assistent (Prompt 37, app/setup/, ausgelöst über run.py "setup"/erster
"serve"-Aufruf) fragt weiterhin interaktiv über die Konsole (`input()`/
`getpass`) nach der Admin-E-Mail-Adresse, BEVOR das native Fenster
(pywebview) überhaupt aufgebaut wird - ohne Konsole gäbe es dafür keine
Eingabemöglichkeit. Der PyInstaller-`console`-Modus ist eine feste
Build-Zeit-Einstellung für die gesamte .exe, nicht pro Aufruf umschaltbar -
ein Umschalten (Konsole nur beim allerersten Start, danach rein
fensterbasiert) wäre über einen separaten, versteckten Zweit-Prozess lösbar,
aber ein deutlich größerer Schritt als hier gerechtfertigt (siehe
ARCHITECTURE.md, offene Punkte). Nach dem allerersten Setup bleibt die
Konsole also weiterhin sichtbar neben dem nativen Fenster - eine bewusst in
Kauf genommene, kleinere kosmetische Einschränkung.

Aufruf (aus dem Projekt-Root, mit aktivierter venv,
`pip install -e .[build]` vorher ausgeführt):

    pyinstaller windows/lexono.spec --distpath dist --workpath build

Ergebnis: dist/Lexono/Lexono.exe + alle Abhängigkeiten im selben
Ordner - genau der Ordner, den windows/installer.iss anschließend
verpackt. (Historisch: `windows/kanzlei_ai.spec` / `dist/kanzlei_ai/
kanzlei_ai.exe`, vor der KanzleiAI->Lexono-Produktidentitaets-
Bereinigung - siehe .agentic/DECISIONS.md.)
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, copy_metadata

PROJECT_ROOT = Path(SPECPATH).resolve().parent  # noqa: F821 (SPECPATH von PyInstaller injiziert)

# Deutsches spaCy-Modell (app/privacy/presidio_ner.py, Presidios NER-Grundlage
# fuer Personen/Orte/Organisationen) - real gefundener Packaging-Bug: dieses
# Spec bündelte das Modell bislang NICHT, obwohl es als eigenstaendiges,
# importierbares Paket "de_core_news_lg" installiert ist (per
# "python -m spacy download de_core_news_lg", ~600 MB Modelldaten). Ohne
# diesen Include wirft `NlpEngineProvider(...).create_engine()` im
# installierten Produkt ein OSError ("Can't find model") - Presidios
# NER-Erkennung faellt komplett aus, JEDE Entwurfserstellung schlaegt fehl
# (siehe ARCHITECTURE.md/Security-Review, Packaging-Befund). collect_data_files
# holt die eigentlichen Modelldateien; copy_metadata wird zusaetzlich
# gebraucht, weil spaCy Modellpakete ueber deren installierte
# Paket-Metadaten (importlib.metadata) aufloest, nicht nur ueber die
# Datendateien selbst.
_SPACY_MODEL_PACKAGE = "de_core_news_lg"

# ZWEITER, beim echten Installer-Smoke-Test nach Beheben des ersten Bugs neu
# aufgedeckter Packaging-Fehler (real am gebauten kanzlei_ai.exe verifiziert,
# nicht nur vermutet): presidio-analyzer laedt seine Recognizer-Registry zur
# Laufzeit aus mitgelieferten YAML-Dateien (conf/default_recognizers.yaml
# u. a.) - PyInstallers statische Analyse sieht auch diese Nicht-Python-Daten
# nicht automatisch. Ohne diesen Include: FileNotFoundError beim ersten
# Presidio-Aufruf im installierten Produkt (".../conf/default_recognizers.yaml"
# nicht gefunden), identische Symptomatik (Entwurfserstellung schlaegt fehl)
# wie beim fehlenden spaCy-Modell, nur eine Ebene tiefer.
_PRESIDIO_ANALYZER_PACKAGE = "presidio_analyzer"

# Gebuendeltes Tesseract-OCR (Pilot-Finding, siehe FUTURE_ROADMAP.md/
# RELEASE_NOTES.md "Tesseract als Abhaengigkeit" + app/documents/ocr.py
# Moduldocstring): OHNE dieses Buendel muesste jede Kanzlei Tesseract
# manuell separat installieren, sonst schlaegt jede OCR-Anfrage im
# fertigen Produkt fehl. windows/fetch_tesseract.ps1 erzeugt diesen Ordner
# (kein Teil des versionierten Quellcodes, ~70 MB Binaerdaten - siehe
# .gitignore). `app/documents/ocr.py::configure_tesseract` erwartet ihn
# genau unter "tesseract/bin" bzw. "tesseract/tessdata" relativ zum
# Bundle-Wurzelverzeichnis (= relativ zu Lexono.exe im onedir-Build).
_TESSERACT_VENDOR_DIR = PROJECT_ROOT / "windows" / "vendor" / "tesseract"
if not (_TESSERACT_VENDOR_DIR / "bin" / "tesseract.exe").is_file():
    raise SystemExit(
        "windows/vendor/tesseract/bin/tesseract.exe fehlt - vor dem Build "
        "einmalig 'powershell -ExecutionPolicy Bypass -File "
        "windows\\fetch_tesseract.ps1' ausführen (lädt ein eigenständiges "
        "Tesseract-OCR herunter, damit OCR im installierten Produkt ohne "
        "manuelle Zusatzinstallation funktioniert)."
    )

a = Analysis(  # noqa: F821 (von PyInstaller zur Laufzeit des Specs injiziert)
    [str(PROJECT_ROOT / "run.py")],
    pathex=[str(PROJECT_ROOT)],
    binaries=[],
    datas=[
        # Migrationen + alembic.ini: von "run.py migrate" zur Laufzeit
        # gebraucht (alembic liest Migrationsskripte als Dateien vom
        # Datenträger, nicht per Python-Import - PyInstallers
        # Import-Analyse sieht sie daher nicht automatisch).
        (str(PROJECT_ROOT / "migrations"), "migrations"),
        (str(PROJECT_ROOT / "alembic.ini"), "."),
        # Templates/statische Assets: siehe app/web/template_paths.py -
        # müssen unter demselben relativen Pfad liegen, den die dortigen
        # Path(__file__)-Berechnungen erwarten.
        (str(PROJECT_ROOT / "app" / "web" / "templates"), "app/web/templates"),
        (str(PROJECT_ROOT / "app" / "web" / "static"), "app/web/static"),
        (str(_TESSERACT_VENDOR_DIR / "bin"), "tesseract/bin"),
        (str(_TESSERACT_VENDOR_DIR / "tessdata"), "tesseract/tessdata"),
        (str(_TESSERACT_VENDOR_DIR / "THIRD_PARTY_NOTICES.md"), "tesseract"),
        *collect_data_files(_SPACY_MODEL_PACKAGE),
        *copy_metadata(_SPACY_MODEL_PACKAGE),
        *collect_data_files(_PRESIDIO_ANALYZER_PACKAGE),
    ],
    hiddenimports=[
        # Siehe Kommentar zu _SPACY_MODEL_PACKAGE oben - spacy.load(name)
        # importiert das Modellpaket per Namen, PyInstallers statische
        # Analyse folgt diesem dynamischen Import nicht automatisch.
        _SPACY_MODEL_PACKAGE,
        # run.py importiert dies erst zur Laufzeit (lazy import in
        # cmd_create_admin) - PyInstallers statische Analyse verfolgt
        # verschachtelte/späte Imports nicht immer zuverlässig.
        "scripts.create_admin",
        # Wie "scripts.create_admin" - real gefundener Packaging-Fund:
        # dieses Recovery-Skript existierte bereits im Quellcode, war aber
        # nicht als hiddenimport gelistet und dadurch aus der installierten
        # .exe heraus nicht lauffähig (siehe run.py::cmd_reset_admin_password).
        "scripts.reset_admin_password",
        # Wiederherstellungs-CLI (Schritt 3) - wie "scripts.create_admin"
        # nur zur Laufzeit ueber run.py cmd_restore lazy importiert.
        "scripts.restore_backup",
        "migrations.env",
        # SQLAlchemy laedt Dialekte z. T. dynamisch nach.
        "sqlalchemy.dialects.sqlite",
        # pywebview (Prompt 46): waehlt sein Windows-Backend
        # (webview.platforms.winforms, das intern wiederum EdgeChromium
        # ODER als Fallback das veraltete MSHTML importiert) erst zur
        # Laufzeit innerhalb eines try/except - hier explizit als
        # hiddenimport ergaenzt, auch wenn PyInstallers AST-Analyse
        # bedingte Imports normalerweise bereits findet (Vorsichtsmassnahme,
        # analog zu "migrations.env" oben). Die dafuer noetigen DLLs
        # (WebView2-Loader, clr_loader/.NET-Interop) sammelt bereits
        # "pyinstaller-hooks-contrib" automatisch ein (hook-webview.py,
        # hook-clr_loader.py, seit Version 2026.6 im Projekt via
        # PyInstaller selbst mitinstalliert) - hier daher KEINE eigene
        # collect_dynamic_libs()-Handhabung noetig.
        "webview.platforms.winforms",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Lexono",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    # Anwendungssymbol (Prompt 47) - aus dem echten Lexono-Markenzeichen
    # generiert (siehe windows/generate_placeholder_icon.py, Dateiname
    # historisch). Wird als Datei-Icon von Lexono.exe UND (ohne
    # gesonderte Einbindung, Windows liest es direkt aus der .exe) von den
    # Verknuepfungen aus windows/installer.iss uebernommen, wo keine
    # eigene IconFilename gesetzt ist.
    icon=str(PROJECT_ROOT / "windows" / "app_icon.ico"),
)

coll = COLLECT(  # noqa: F821
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="Lexono",
)
