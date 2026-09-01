; Inno-Setup-Skript für die Windows-Installation (Prompt 36; App-Icon
; Prompt 47; Installation unter %LocalAppData% ohne Admin-Rechte Schritt 3).
;
; Voraussetzung: der PyInstaller-Build liegt bereits unter dist\kanzlei_ai\
; (siehe windows\kanzlei_ai.spec bzw. windows\build.ps1, das beide Schritte
; nacheinander ausführt). Übersetzen mit dem Inno-Setup-Compiler (ISCC.exe,
; Teil der kostenlosen Inno-Setup-Installation von jrsoftware.org):
;
;     iscc windows\installer.iss
;
; WICHTIG zu relativen Pfaden in dieser Datei: Inno Setup löst sie relativ
; zum Speicherort DIESES Skripts auf (windows\), nicht relativ zum
; Projekt-Root - daher unten "app_icon.ico" (liegt direkt daneben in
; windows\), NICHT "windows\app_icon.ico" (das würde windows\windows\...
; suchen und fehlschlagen). Aus demselben Grund referenziert [Files] oben
; bereits "..\dist\kanzlei_ai\*".
; Installiert AUSSCHLIESSLICH den Programmordner (Code, Templates,
; statische Assets, Migrationsskripte) unter %LocalAppData%\Lexono (bis
; Prompt 36-Schritt 3 unter {autopf}\"Program Files") - KEINE Mandantendaten.
; Konfiguration/Datenbank/Dokumente entstehen erst beim ersten Start des
; Setup-Assistenten (app/setup/, Prompt 37) weiterhin unter
; %PROGRAMDATA%\KanzleiAI - bewusst getrennt vom Installationsverzeichnis,
; siehe ARCHITECTURE.md (Programminstallation vs. Anwendungsdaten).

#define MyAppName "Lexono"
#define MyAppVersion "0.1.0"
#define MyAppExeName "kanzlei_ai.exe"
#define MyAppPublisher "Lexono Projekt"

[Setup]
AppId={{9F4B9E7A-2B1E-4C77-9C7C-3D9B5E5B0B21}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
; Reliability-Fund (01.09., unabhaengige Installer-Review): dieselbe
; Windows-Mutex, die run.py selbst zur Single-Instance-Absicherung nutzt
; (siehe run.py::_SINGLE_INSTANCE_MUTEX_NAME/"Lexono_SingleInstance_Mutex",
; CreateMutexW), war Inno Setup bisher NICHT bekannt - ein Reinstall/Update
; waehrend Lexono noch im Hintergrund laeuft (real moeglich: Start.vbs
; startet die App unsichtbar, kein Tray-Icon als Erinnerung) konnte
; laufende .exe-/DLL-Dateien mitten im Kopiervorgang sperren, im
; ungünstigsten Fall ein teilweise ueberschriebenes {app}-Verzeichnis
; hinterlassen. AppMutex laesst Setup denselben Mutex-Namen pruefen und
; den Nutzer VOR dem Kopieren zum Schliessen der laufenden Instanz
; auffordern, statt mitten im Kopiervorgang auf eine gesperrte Datei zu
; stossen.
AppMutex=Lexono_SingleInstance_Mutex
; Geaendert (Schritt 3, 20.08.): Installation je Windows-Benutzerkonto unter
; %LocalAppData%\Lexono statt {autopf} ("Program Files") - Ziel laut
; Vorgabe: Updates ohne Windows-Administratorrechte moeglich, da
; %LocalAppData% vom jeweiligen Benutzerkonto selbst beschreibbar ist. Bricht
; bewusst mit der Prompt-36-Entscheidung (Program Files) - eine bereits
; unter Program Files installierte Pilot-Instanz muss vor der Installation
; dieser Version manuell deinstalliert werden (siehe RELEASE_NOTES.md),
; sonst entstehen zwei parallele Installationen.
;
; Markenumbenennung "Kanzlei-AI" -> "Lexono" (siehe ARCHITECTURE.md): der
; Ordnername wechselt hier von \KanzleiAI zu \Lexono - fuer NEUE Installationen.
; Bereits installierte Pilot-Instanzen aktualisieren dank stabiler AppId (siehe
; oben) weiterhin am zuvor gewaehlten Pfad (Inno Setup verwendet bei
; erkannter AppId den zuletzt genutzten Installationsort, nicht DefaultDirName) -
; keine manuelle Nacharbeit fuer bestehende Installationen noetig. Das
; getrennte, persistente Datenverzeichnis (%PROGRAMDATA%\KanzleiAI, siehe
; app/setup/paths.py) bleibt BEWUSST unveraendert - eine Umbenennung dort
; wuerde bestehenden Mandantendaten-Bestaenden den Pfad entziehen.
DefaultDirName={localappdata}\Lexono
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=..\dist\installer
; Fester Dateiname "Lexono_Setup.exe" (bewusst OHNE Versionsnummer im
; Namen, auf ausdrücklichen Wunsch) - Kehrseite: ein neuer Build
; überschreibt in dist\installer\ den vorherigen, es liegen also nie
; mehrere Versionsstände parallel. Falls künftig mehrere Versionen
; nebeneinander aufbewahrt werden sollen (z. B. für ein Rollback), einfach
; wieder "Lexono_Setup-{#MyAppVersion}" verwenden.
OutputBaseFilename=Lexono_Setup
Compression=lzma
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible
; Geaendert (Schritt 3): "lowest" statt "admin" - keine UAC-Erhoehung mehr
; noetig, konsistent mit der Installation unter %LocalAppData% oben. Das
; persistente Datenverzeichnis (%PROGRAMDATA%\KanzleiAI, siehe
; app/setup/paths.py) bleibt unveraendert - dort war ohnehin nie eine
; Admin-Erhoehung fuer den Setup-Assistenten noetig (laeuft unter dem Konto
; des Anwalts/der Kanzleimitarbeiter).
PrivilegesRequired=lowest
; Anwendungssymbol fuer den Installer selbst (Setup.exe-Datei-Icon,
; Titelleiste, Add/Remove-Programme-Eintrag) - Prompt 47. Aus dem echten,
; offiziellen Lexono-Markenzeichen generiert (siehe windows/
; generate_placeholder_icon.py, Dateiname historisch, Inhalt zuletzt am
; 20.08. aktualisiert: neues Dokument+Schild+Kette-Icon, CI-Farbcode
; #101828 als Icon-Hintergrund) - dasselbe Icon erscheint unveraendert auf
; der Desktop-/Startmenue-Verknuepfung (siehe [Icons] unten, IconFilename
; zeigt auf {#MyAppExeName}, dessen eingebettetes Icon wiederum aus
; derselben app_icon.ico stammt, siehe windows/kanzlei_ai.spec).
SetupIconFile=app_icon.ico
; Weiterhin KEIN WizardImageFile (grossflaechiges Bild auf den
; Assistenten-Seiten) - kanzleispezifisches Vollbild-Branding ist Teil der
; zurückgestellten "Multi-Kanzlei-Profile"-Frage (Prompt 38), siehe
; PROMPT38_ANALYSIS.md, hier weiterhin bewusst NICHT vorweggenommen. Die am
; 20.08. angefragte CI-Farbkonsistenz ("Verknüpfungs-Panel/Hintergrundfarben
; im Setup") ist über das oben gesetzte SetupIconFile (#101828) bereits
; erfüllt - das ist die einzige tatsächlich sichtbare "Hintergrundfarbe"
; einer Desktop-Verknüpfung/eines Installer-Icons unter Windows.
; Icon fuer den Eintrag unter "Apps & Features"/"Programme und Funktionen" -
; ohne diese Zeile wuerde Windows dort ein generisches Deinstaller-Icon
; zeigen statt des tatsaechlichen App-Icons.
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}

[Languages]
Name: "german"; MessagesFile: "compiler:Languages\German.isl"

[Files]
Source: "..\dist\kanzlei_ai\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; Stummer Starter (Schritt 3) - siehe [Icons] unten: Start Menü/Desktop
; starten die App darüber statt direkt über die .exe, damit im normalen
; Betrieb kein Konsolenfenster erscheint (nur beim allerersten Start
; bleibt die Konsole sichtbar, siehe Start.vbs-Kommentar).
Source: "..\Start.vbs"; DestDir: "{app}"; Flags: ignoreversion
; WebView2-Evergreen-Bootstrapper (ARCHITECTURE.md §70/Pilot-Readiness-
; Review) - siehe windows\fetch_webview2.ps1 fuer Herkunft/Lizenz. "Flags:
; dontcopy" - wird NICHT automatisch nach {tmp} entpackt (Fund aus einem
; echten Installationslauf, 31.08.: "dontcopy" bedeutet nur "nicht nach
; {app} kopieren" - ohne einen expliziten `ExtractTemporaryFile`-Aufruf im
; [Code]-Abschnitt unten bleibt die Datei komplett unentpackt, der
; [Run]-Schritt scheitert dann mit "Datei kann nicht ausgefuehrt werden" /
; CreateProcess-Fehlercode 2. Bleibt NICHT im Installationsverzeichnis
; liegen (kein dauerhaft benoetigtes Programmbestandteil, nur ein
; einmaliger Setup-Schritt) - siehe [Code] fuer die tatsaechliche
; Entpackung vor dem [Run]-Aufruf.
Source: "vendor\webview2\MicrosoftEdgeWebview2Setup.exe"; DestDir: "{tmp}"; Flags: dontcopy

[Tasks]
; Startmenü-Verknüpfung ist immer da (siehe [Icons] unten, kein Task
; nötig). Desktop-Verknüpfung bleibt abwählbar (Task statt fester
; [Icons]-Zeile), ist aber seit dieser Anfrage ("Desktop- und
; Startmenü-Verknüpfungen anlegen") standardmäßig ANGEHAKT - vorher
; bewusst unchecked (nicht jeder wollte einen weiteren Desktop-Eintrag),
; jetzt Standardverhalten mit weiterhin vorhandener Abwahlmöglichkeit im
; Installationsassistenten.
Name: "desktopicon"; Description: "Desktop-Verknüpfung anlegen"; GroupDescription: "Zusätzliche Symbole:"

[Icons]
; IconFilename explizit gesetzt (Prompt 47) statt sich auf Inno Setups
; Standardverhalten zu verlassen (das ohne diese Angabe automatisch das in
; kanzlei_ai.exe eingebettete Icon - siehe windows/kanzlei_ai.spec,
; EXE(icon=...) - übernommen hätte, im Ergebnis identisch, hier aber
; ausdrücklich dokumentiert statt implizit).
;
; Filename zeigt seit Schritt 3 auf Start.vbs (über wscript.exe) statt
; direkt auf die .exe - dadurch startet die App im Normalbetrieb ohne
; sichtbares Konsolenfenster (Start.vbs zeigt die Konsole bewusst NUR beim
; allerersten Start, wenn der interaktive Setup-Assistent sie braucht,
; siehe Start.vbs-Kommentar). IconFilename bleibt unverändert die .exe,
; damit die Verknüpfung weiterhin das echte App-Icon zeigt (wscript.exe
; selbst hat kein passendes Icon).
Name: "{group}\{#MyAppName}"; Filename: "wscript.exe"; Parameters: """{app}\Start.vbs"""; WorkingDir: "{app}"; IconFilename: "{app}\{#MyAppExeName}"
Name: "{group}\{#MyAppName} deinstallieren"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "wscript.exe"; Parameters: """{app}\Start.vbs"""; WorkingDir: "{app}"; IconFilename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
; WebView2-Runtime automatisch bereitstellen (ARCHITECTURE.md §70) - laeuft
; VOR dem eigentlichen App-Start (siehe Reihenfolge der [Run]-Eintraege,
; Inno Setup fuehrt sie sequenziell aus), damit "kanzlei_ai.exe serve"
; beim allerersten Start die Runtime bereits vorfindet, statt mit der
; bisherigen Fehlermeldung (run.py::_is_webview2_runtime_available) manuell
; auf einen Download zu verweisen. "/silent /install" - keine
; Bootstrapper-eigene UI; Windows kann dennoch EINMALIG UAC-Zustimmung
; einfordern, falls die Runtime noch fehlt und maschinenweit installiert
; werden muss (identisches Verhalten wie bei jeder anderen Anwendung, die
; WebView2 einbindet). "Flags: waituntilterminated" (App braucht die
; Runtime sofort), aber bewusst OHNE "Flags: abortonerror" - schlaegt
; dieser Schritt fehl (z. B. kein Internetzugang waehrend der
; Installation), bricht die Lexono-Installation selbst NICHT ab; die
; bestehende, bereits getestete Laufzeit-Fehlerbehandlung in run.py
; greift dann beim ersten Programmstart mit einer klaren Meldung + Link
; zum manuellen Download (siehe run.py::cmd_serve).
Filename: "{tmp}\MicrosoftEdgeWebview2Setup.exe"; Parameters: "/silent /install"; StatusMsg: "WebView2-Laufzeitumgebung wird bereitgestellt..."; Flags: waituntilterminated

Filename: "wscript.exe"; Parameters: """{app}\Start.vbs"""; Description: "{#MyAppName} jetzt starten (öffnet beim allerersten Start den Setup-Assistenten in einem Konsolenfenster, danach unsichtbar im Hintergrund - siehe app.log)"; Flags: postinstall nowait skipifsilent

; BEWUSST KEIN [UninstallDelete]-Abschnitt für %PROGRAMDATA%\KanzleiAI:
; dieses Verzeichnis enthält vollständige, unpseudonymisierte
; Mandanteninhalte (Datenbank, Dokumente) - eine Deinstallation darf das
; NIEMALS automatisch löschen. Eine bewusste Datenlöschung bleibt dem
; Betreiber vorbehalten (siehe ARCHITECTURE.md, Abschnitt zu Backup/Export,
; Prompt 35, wo dieselbe Sensibilität bereits dokumentiert ist).

[Code]
// Entpackt den WebView2-Bootstrapper VOR dem eigentlichen Dateikopiervorgang
// nach {tmp} - "Flags: dontcopy" im [Files]-Abschnitt allein entpackt NICHTS
// automatisch (echter Fund aus einem realen Installationslauf, 31.08.: ohne
// diesen expliziten ExtractTemporaryFile-Aufruf schlägt der [Run]-Schritt mit
// "CreateProcess schlug fehl; Code 2 - Datei nicht gefunden" fehl, obwohl der
// Installer selbst fehlerfrei durchläuft). ssInstall ist frühzeitig genug -
// die Extraktion nach {tmp} ist unabhängig vom eigentlichen {app}-Kopiervorgang.
procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssInstall then
  begin
    ExtractTemporaryFile('MicrosoftEdgeWebview2Setup.exe');
  end;
end;
