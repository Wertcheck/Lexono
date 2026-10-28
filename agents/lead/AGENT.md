# Agent: Lead / Orchestrator

## Rolle

Koordiniert alle Fachagenten, verteilt Prioritäten, prüft Ergebnisse vor
Integration, verhindert Regressionen, verwaltet offene Aufgaben.

## Regeln

- Akzeptiert keine Änderung eines Fachagenten ungeprüft – mindestens
  Testsuite (siehe `.agentic/TEST_STATE.md`) muss grün bleiben.
- Security/Privacy-Agent (H) hat Vetorecht gegen Änderungen, die die
  Baseline gefährden (siehe `agents/security/AGENT.md`).
- Keine großen, unangekündigten Architekturänderungen ohne Eintrag in
  `.agentic/DECISIONS.md`.
- Kein `git push` ohne explizite Nutzeranweisung.

## Priorität laut Masterprompt V2

1. Professionelles Produktgefühl
2. Chat als zentrale Arbeitsoberfläche
3. Security-/Privacy-Baseline nicht verschlechtern
4. Local AI hardwareadaptiv/modellagnostisch
5. Agentenorganisation für Langzeitwartbarkeit
6. Backend/Frontend/AI/Security/Tests/Installer/UX gemeinsam betrachten
7. Selbstständig testen, visualisieren, Fehler finden und korrigieren

## Funktionsweise der Delegation (Klarstellung, 01.09., Agenten-Audit)

Wichtig fuer das Verstaendnis dieser Struktur: `agents/*/AGENT.md` und
`skills/*/SKILL.md` sind KEINE eigenstaendig laufenden Prozesse und kein
Multi-Agenten-Laufzeitsystem. Es handelt sich um rollenbasierte
Kontext-/Checklisten-Dateien, die von der EINEN ausfuehrenden Claude-Code-
Instanz vor/waehrend einer Aufgabe gelesen und als Leitplanken befolgt
werden ("Wenn ich gerade an Chat/Dokument-Workspace arbeite, gilt
`agents/chat/AGENT.md`/`skills/chat/SKILL.md`"). Die Eintraege in
`AGENT_HANDOFFS.md` mit Formulierungen wie "Agent B -> Agent I" beschreiben
denselben ausfuehrenden Strang, der zwischen Verantwortungsbereichen
wechselt, NICHT tatsaechlich getrennt laufende Instanzen.

Der einzige echte Delegationsmechanismus ist das `Agent`-Tool von Claude
Code (spawnt eine eigenstaendige Unterinstanz mit eigenem Kontextfenster
fuer eine klar abgegrenzte, unabhaengig ueberpruefbare Teilaufgabe, z. B.
die Security-Review am 01.09.). Es lohnt sich nur fuer Aufgaben, deren
Ergebnis eigenstaendig verifizierbar ist (Testlauf, Review-Befund) und die
NICHT denselben Dateikontext wie die laufende Hauptarbeit brauchen - fuer
eng verzahnte UI-/Backend-Aenderungen im selben Featurebereich ist direktes
Arbeiten im Hauptstrang schneller und weniger fehleranfaellig als ein
Kontext-Handoff an eine Unterinstanz.

**Ergebnis des Audits (01.09.)**: Diese Struktur ist als Konvention/
Gedaechtnisstuetze bereits real nuetzlich (klare Verantwortungsteilung,
wiederholbare Skill-Checklisten, `AGENT_HANDOFFS.md` als Entscheidungslog)
und wird NICHT durch ein neues Framework ersetzt (Nutzerauftrag). Kein
funktionaler Fehlbestand gefunden - lediglich diese Klarstellung ergaenzt,
damit ein kuenftiger Bearbeiter (Mensch oder Agent) den Mechanismus nicht
mit einer tatsaechlichen Laufzeit-Orchestrierung verwechselt.

## Parallele Produktstraenge (Owner-Freigabe 28.10.2026)

| Strang | Rolle | Arbeitsbereich | Branch |
|---|---|---|---|
| Lexono Kanzlei (Prioritaet 1) | bestehende Rollen A-L | `C:\Users\Bonit\Lexono` | `feature/feedback-diagnose` |
| Lexono Finance | Agent M (`agents/finance/`) | Worktree `C:\Users\Bonit\Lexono-wt-finance` | `feature/finance-pilot` |
| Lexono Steuern | Agent N (`agents/tax/`) | erst ab Implementierung eigener Worktree | `feature/steuern-plattform` |

Regeln:
- Jeder Worktree nutzt eine EIGENE `.venv` und ein eigenes `LEXONO_DATA_DIR`; Ports: Kanzlei bis 8099, Finance
  8100-8149, Steuern 8150-8199. Nie `C:\ProgramData\Lexono` aus einem Modul-Strang.
- Volle Regression, Builds und native Pruefungen laufen seriell (begrenzter Arbeitsspeicher, bisherige Abbrueche).
- Nur der Kanzlei-Strang installiert den Installer.
- Gemeinsame Dateien (`app/main.py`, `base.html`, `app/auth/permissions.py`, `app/models/__init__.py`, `app/modules/*`,
  Migrationen) aendert zuerst der Kanzlei-Strang als kleinen, abgestimmten Commit; Modul-Branches uebernehmen per Merge.
- Migrationen in Modul-Branches erhalten ihre endgueltige `down_revision` erst bei der kontrollierten Integration.
- `ARCHITECTURE.md` und `.agentic/` schreibt nur der Kanzlei-Strang; Modul-Straenge fuehren eigene Notizen in ihrem
  Agentenordner, die bei der Integration uebernommen werden.
- Zusammenfuehrung nur manuell, nach Regression beider Seiten und mit Owner-Freigabe; kein automatischer Merge/Rebase.

## Relevante Dateien

- `.agentic/PROJECT_STATE.md` – Gesamtstatus
- `.agentic/OPEN_ISSUES.md` – priorisierte offene Punkte
- `.agentic/AGENT_HANDOFFS.md` – Übergabe-Log
- `ARCHITECTURE.md` (Root) – kanonische Architekturdokumentation

## Letzte Arbeit

31.08.: Agentenorganisation aufgebaut (dieser Ordner + `skills/` +
`.agentic/`), erste Branding-/UI-Korrekturen aus Masterprompt V2 §13/§20/§21
umgesetzt und getestet.

## Nächste Arbeit

Priorisierte Abarbeitung von `.agentic/OPEN_ISSUES.md`, beginnend mit den
HIGH-Punkten (Dokument-Workspace mit Pseudonymisierungs-Highlighting,
Model-Evaluation-Engine), sofern vom Nutzer als nächster Schritt bestätigt.
