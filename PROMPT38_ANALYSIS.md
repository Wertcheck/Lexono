# Analyse: Prompt 38 – Multi-Kanzlei-Profile + Cross-Tenant-Tests

Status: **Analyse abgeschlossen, Implementierung noch NICHT begonnen.** Kein Code geändert.
Grundlage für die nächste Umsetzungssitzung (Chat, nicht Claude Code – reine Python-/
Konfigurationslogik, kein Windows-natives Tooling nötig).

> Weiterhin unverändert offen (zuletzt geprüft 01.09.2026, siehe
> `.agentic/OPEN_ISSUES.md`, Kategorie "Produktentscheidung
> erforderlich") - ob Lexono mehrere Kanzleien pro Instanz unterstützen
> soll, ist eine Produktentscheidung, kein rein technisches Detail.

## Prämisse

Basiert auf der am 16.08. getroffenen Entscheidung "getrennte Installation je Kanzlei"
(siehe TODO.md, Abschnitt "Entscheidung zur Mehr-Kanzlei-Fähigkeit"). Keine Laufzeit-
Mandantentrennung, kein `tenant_id`, keine gemeinsame Datenbank zwischen Kanzleien.

## Bestandsaufnahme

- Es gibt bislang **keine** Repräsentation der Kanzlei selbst im System – nur `Client`
  (= Mandant der Kanzlei). Die Kanzlei, die das System betreibt, ist implizit "die einzige
  Installation", nirgends als Datensatz oder Konfigurationsobjekt modelliert.
- Klassifikations-Schlüsselwörter (`app/classification/classifier.py:
  _DOCUMENT_TYPE_KEYWORDS`) sind eine fest codierte Python-Modul-Konstante – nicht
  konfigurierbar, ohne Codeänderung nicht anpassbar.
- Branding/Briefkopf-Daten existieren nirgends – relevant spätestens für Prompt 39
  (Dokumentvorlagen).

## Übersetzung der beiden Prompt-Begriffe unter der getroffenen Prämisse

### "Multi-Kanzlei-Profile" → Konfigurierbarkeit pro Installation (nicht pro Laufzeit-Mandant)

Vorschlag: neues `KanzleiProfile`-Konfigurationsobjekt, geladen aus einer separaten
strukturierten Datei (nicht `.env` – zu verschachtelt für Umgebungsvariablen). Inhalt:

- Kanzleiname, Anschrift, Kontaktdaten, Briefkopf-Daten (Vorarbeit für Prompt 39)
- Klassifikations-Keyword-**Erweiterungen** je Dokumenttyp (Vorschlag: nur additiv, nicht
  ersetzend – die eingebauten Standard-Keywords bleiben als Grundlage bestehen, eine Kanzlei
  kann ergänzen, aber nicht versehentlich die gesamte Erkennung deaktivieren)
- Standard-Rechtsgebiete (`practice_area`-Vorschlagsliste)
- Optional: Logo-Pfad

Diese Datei würde vom Setup-Assistenten (Prompt 37) beim Ersteinrichten erzeugt/befüllt.

### "Cross-Tenant-Tests" → Beweis echter Konfigurierbarkeit statt Datenisolation

Da zwei Kanzleien nie denselben Prozess/dieselbe Datenbank teilen (physische Trennung durch
getrennte Installation), ist ein klassischer "Mandant A sieht nie Mandant-B-Daten"-Test hier
strukturell gegenstandslos. Sinnvoll und geplant stattdessen:

1. Zwei unterschiedliche Profildateien führen nachweislich zu unterschiedlichem Verhalten
   (Konfiguration wird tatsächlich gelesen und wirkt sich aus, nicht nur behauptet).
2. Keine versteckte prozessweite Annahme (z. B. `@lru_cache` auf `get_settings()`) darf bei
   zwei Instanzen im selben Prozess (z. B. während eines Testlaufs) Konfiguration vermischen.
3. Regressionsschutz: die eingebauten Standard-Keywords bleiben ohne Profildatei unverändert
   funktionsfähig (Rückwärtskompatibilität für die bereits laufende erste Kanzlei).

## Offene Fragen (vor Implementierung zu klären)

1. Config-Datei-Format: JSON oder YAML? (Tendenz: JSON, da bereits an mehreren Stellen im
   Projekt verwendet - z. B. Aktenexport-Manifest, Prompt 35 - kein neues Format einführen.)
2. Klassifikations-Keywords: nur ergänzbar oder auch pro Kategorie überschreibbar?
3. Soll das Kanzleiprofil in dieser Phase schon als DB-Zeile (statt Datei) angelegt werden,
   falls perspektivisch eine Bearbeitung über das Dashboard (Admin-Rolle) gewünscht ist? Wäre
   konsistent mit dem Muster "Nutzerverwaltung"/"Systemstatus" (Prompts 26/32), aber ein
   größerer Eingriff als eine reine Konfigurationsdatei.

## Nicht Teil dieser Analyse / bewusst zurückgestellt

- Das Lizenz-/Auslieferungsmodell für weitere Kanzleien bleibt weiterhin offen (siehe
  TODO.md) - unabhängig von diesem Prompt.
- Prompt 37 (Setup-Assistent) selbst wird in der Claude-Code-Sitzung auf der Windows-
  Zielmaschine umgesetzt (siehe HANDOFF_PROMPT36_37_WINDOWS.md) - dieses Dokument bereitet
  nur die KONZEPTIONELLE Schnittstelle vor (welche Felder eine Profildatei braucht), nicht
  die Implementierung des Assistenten selbst.
