' Start.vbs - startet Lexono ohne sichtbares Konsolenfenster (Schritt 3,
' "produktiver Piloteinsatz"; Local-First-Architektur 20.08., siehe
' ARCHITECTURE.md §60).
'
' WICHTIGE AUSNAHME (bewusst, kein Versehen): Solange die Ersteinrichtung
' noch nicht ERFOLGREICH abgeschlossen ist, braucht der interaktive
' Setup-Assistent (E-Mail-/Passwort-Abfrage in der Konsole, siehe
' app/setup/wizard.py, run.py: cmd_setup) eine SICHTBARE Konsole. Eine von
' Anfang an versteckte Konsole würde dort unsichtbar auf eine
' Tastatureingabe warten, die nie ankommen kann - die App würde scheinbar
' "hängen", ohne dass der Anwalt/die Kanzleimitarbeiterin einen Hinweis
' bekäme. Deshalb: solange kein abgeschlossenes Setup vorliegt, SICHTBAR;
' danach jeder weitere Start STUMM (Server-Logs stdout/stderr -> app.log
' im DATENVERZEICHNIS, siehe strLogPath weiter unten).
'
' WICHTIG (real beim Endanwender aufgetreten, 12.09.): NICHT anhand von
' `.env` allein entscheiden, ob die Ersteinrichtung abgeschlossen ist -
' `.env` wird als ALLERERSTER Schritt von `run_setup_wizard()` geschrieben,
' VOR Migration und Admin-Anlage. Schlug einer dieser spaeteren, tatsaechlich
' ladungstragenden Schritte fehl, existierte `.env` trotzdem bereits - jeder
' folgende Start waere dann STUMM gelaufen, WAEHREND `run.py::main()`
' (das zusaetzlich pruefen kann, ob tatsaechlich ein Benutzer existiert)
' versucht haette, den Setup-Assistenten erneut zu starten: unsichtbar
' wartende Konsole, fuer den Benutzer nicht von einem Haenger zu
' unterscheiden. Deshalb prueft dieses Skript stattdessen `.setup_complete`
' - eine Markerdatei, die `run_setup_wizard()` ERST nach tatsaechlich
' erfolgreicher Admin-Anlage schreibt (VBScript besitzt keinen
' SQLite-Treiber, kann also nicht direkt wie `run.py::main()` in der
' Datenbank nachsehen, ob ein Benutzer existiert - dieser Marker ist die
' naechstbeste, aber bewusst konservative Annäherung: er wird nur bei
' echtem Erfolg gesetzt, nie vorzeitig).
'
' Ersetzt keinen bestehenden Mechanismus - ruft lediglich denselben Befehl
' auf, den auch die Startmenü-/Desktop-Verknüpfung (windows/installer.iss)
' verwendet, nur mit unterdrücktem Konsolenfenster.

Option Explicit

Dim objShell, objFSO, strScriptDir, strDataDir, strProgramData
Dim strSetupCompletePath, strExePath, strPythonExe, strRunPy, strLogPath, strCommand
Dim strRedirectedCommand

Set objShell = CreateObject("WScript.Shell")
Set objFSO = CreateObject("Scripting.FileSystemObject")

strScriptDir = objFSO.GetParentFolderName(WScript.ScriptFullName)

' Persistentes Datenverzeichnis - identische Ableitung wie
' app/setup/paths.py (resolve_data_dir): LEXONO_DATA_DIR-Override (oder der
' aeltere Name KANZLEI_AI_DATA_DIR, weiterhin unterstuetzt), sonst
' %PROGRAMDATA%\Lexono.
strDataDir = objShell.ExpandEnvironmentStrings("%LEXONO_DATA_DIR%")
If strDataDir = "%LEXONO_DATA_DIR%" Then
    strDataDir = objShell.ExpandEnvironmentStrings("%KANZLEI_AI_DATA_DIR%")
End If
If strDataDir = "%KANZLEI_AI_DATA_DIR%" Then
    strProgramData = objShell.ExpandEnvironmentStrings("%PROGRAMDATA%")
    strDataDir = strProgramData & "\Lexono"
End If
strSetupCompletePath = strDataDir & "\.setup_complete"

' Server-Log ins DATENVERZEICHNIS, NICHT neben dieses Skript (14.09.,
' Desktop-Blocker-Diagnose).
'
' ECHTER, reproduzierter Befund: auf dem Desktop der Referenzmaschine lag
' neben der Lexono-Verknuepfung eine zweite, scheinbar leere Kachel. Es war
' kein zweiter Shortcut und kein Installer-Fehler (windows/installer.iss
' legt nachweislich genau EINEN {autodesktop}-Eintrag an), sondern eine
' verwaiste `app.log` vom 12.09.: Windows blendet bekannte Endungen aus
' (HideFileExt) und fuer `.log` ist keine Anwendung registriert - Explorer
' zeichnet dann ein generisches, praktisch leeres Symbol namens "app"
' direkt neben "Lexono".
'
' Ursache dafuer, dass eine Lexono-Logdatei ueberhaupt dort landen konnte,
' war diese Zeile: der Logpfad folgte dem SKRIPTVERZEICHNIS. Lief (wie am
' 12.09.) irgendwann eine Kopie dieses Skripts von einem beliebigen Ort,
' entstand dort eine app.log - inklusive Desktop, wo sie als Fremdkoerper
' im Symbolgitter stehen bleibt.
'
' Das Datenverzeichnis ist der bereits etablierte, dafuer vorgesehene Ort
' (app/setup/paths.py, ARCHITECTURE.md §2022: ausdruecklich NICHT
' Desktop/Dokumente). Zweiter, unabhaengiger Vorteil: bei einer Installation
' in ein schreibgeschuetztes Programmverzeichnis (maschinenweites Setup
' unter %PROGRAMFILES%) wuerde `>> app.log` im Programmordner fehlschlagen
' und den stummen Start kommentarlos abbrechen - im Datenverzeichnis nicht.
' Der stumme Zweig unten laeuft ohnehin nur, wenn `.setup_complete` in
' genau diesem Verzeichnis existiert, es ist also garantiert vorhanden und
' beschreibbar.
strLogPath = strDataDir & "\app.log"

' HINWEIS (KanzleiAI->Lexono-Produktidentitaets-Bereinigung): die echte
' Migration eines bestehenden `%PROGRAMDATA%\KanzleiAI`-Verzeichnisses
' passiert innerhalb von Lexono.exe (app/setup/paths.py::resolve_data_dir),
' NICHT hier in Start.vbs (VBScript hat keine eigene Migrationslogik).
' Nebeneffekt: bei GENAU EINEM Upgrade-Start eines bestehenden
' KanzleiAI-Nutzers sieht dieses Skript `.setup_complete` unter dem NEUEN
' Pfad noch nicht (die Migration lief ja noch nicht) und zeigt einmalig
' die Konsole sichtbar an, obwohl die Ersteinrichtung tatsaechlich bereits
' abgeschlossen war - rein kosmetisch, kein Datenverlust, kein
' Funktionsausfall, und ab dem naechsten Start (nach der Migration durch
' den ersten echten Lexono.exe-Aufruf) wieder korrekt stumm.

' Gebündelte .exe (neben diesem Skript, z. B. nach der Installation)
' bevorzugt, sonst Entwicklungsbetrieb über die venv-Python-Installation
' im Projekt-Root (dieses Skript liegt dort - "Hauptverzeichnis").
strExePath = strScriptDir & "\Lexono.exe"
If Not objFSO.FileExists(strExePath) Then
    strExePath = strScriptDir & "\dist\Lexono\Lexono.exe"
End If

If objFSO.FileExists(strExePath) Then
    strCommand = """" & strExePath & """ serve"
Else
    strPythonExe = strScriptDir & "\.venv\Scripts\python.exe"
    strRunPy = strScriptDir & "\run.py"
    strCommand = """" & strPythonExe & """ """ & strRunPy & """ serve"
End If

If objFSO.FileExists(strSetupCompletePath) Then
    ' Bereits eingerichtet: stummer Start, komplett verstecktes Fenster
    ' (0 = SW_HIDE), alle Server-Logs (stdout/stderr) landen in app.log.
    '
    ' strRedirectedCommand ist z. B.:
    '   "C:\...\python.exe" "C:\...\run.py" serve >> "C:\...\app.log" 2>&1
    strRedirectedCommand = strCommand & " >> """ & strLogPath & """ 2>&1"

    ' WICHTIG zur zusaetzlichen Aussen-Anfuehrung um strRedirectedCommand:
    ' bekannter cmd.exe-/c-Bug - beginnt die Befehlszeile nach /c mit einem
    ' Anfuehrungszeichen (hier: der zitierte .exe-Pfad), entfernt cmd.exe
    ' bei bestimmten Konstellationen faelschlich BEIDE aeusseren Anfuehrungs-
    ' zeichen (das oeffnende vor dem Programmpfad UND das schliessende nach
    ' dem Log-Pfad) und zerstoert damit die Befehlszeile - beobachtet und
    ' verifiziert waehrend der Umsetzung dieses Schritts (echter Testlauf:
    ' ohne das zusaetzliche aeussere Anfuehrungszeichenpaar bricht der Start
    ' kommentarlos ab, keine app.log entsteht). Der Standard-Workaround ist
    ' ein zusaetzliches, rein umschliessendes Anfuehrungszeichenpaar um die
    ' GESAMTE Befehlszeile - cmd.exe entfernt dann nur dieses aeusserste
    ' Paar, die eigentliche (bereits korrekt zitierte) Befehlszeile bleibt
    ' unangetastet.
    objShell.Run "cmd /c """ & strRedirectedCommand & """", 0, False
Else
    ' Ersteinrichtung noch nicht abgeschlossen: Setup-Assistent braucht eine sichtbare,
    ' interaktive Konsole - NICHT verstecken (1 = normales Fenster).
    objShell.Run strCommand, 1, False
End If
