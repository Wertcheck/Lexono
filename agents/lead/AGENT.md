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
