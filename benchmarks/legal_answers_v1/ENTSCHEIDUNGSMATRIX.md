# Entscheidungsmatrix `legal_answers_v1` (§82)

Stand 19.10.2026, Basis `61dcfa8`. **Vorbereitung, keine Entscheidung.** Der Agent trifft keine dieser Entscheidungen
und erteilt keine Freigabe. **C-14 bleibt GESPERRT.**

- Maschinenlesbare Fassung: `entscheidungsmatrix.json`. `tests/test_s82_decision_matrix.py` prüft die Kennzahlen und
  Pfade gegen die Dateien.
- Status-Werte: **OFFEN** (Frage gestellt, Vorschläge fehlen bewusst), **ENTSCHEIDUNG VORBEREITET** (Material und
  Optionen liegen vor, die Entscheidung fehlt) und **NACHWEIS FEHLT** (Angaben fehlen, die nur Verantwortliche liefern
  können).
- **Personen:** Für keinen Punkt ist eine Person benannt. Belegt ist nur die **Rolle** aus `FREIGABE_CHECKLISTE.md`.

## Überblick

| Punkt | Frage (kurz) | Zuständige Rolle (belegt) | Status |
|---|---|---|---|
| J1 | Referenzlösungen der drei Fälle zutreffend? | qualifizierte juristische Person (C-12) | ENTSCHEIDUNG VORBEREITET |
| J2 | Antwortvarianten und Verdachtsformulierungen vollständig und richtig? | qualifizierte juristische Person (C-12, C-16) | ENTSCHEIDUNG VORBEREITET |
| J3 | Vertretbare Alternativergebnisse je Fall? | qualifizierte juristische Person (C-12) | OFFEN |
| J4 | Gewichte und PASS/FAIL-Schwellen je Dimension? | qualifizierte juristische Person (C-13) | OFFEN |
| J5 | Wer bewertet, Zweitprüfung, Umgang mit Abweichungen? | Owner/juristisch (C-15) | NACHWEIS FEHLT |
| J6 | Regeln zu n/a und Unentscheidbarem bestätigen? | qualifizierte juristische Person (nur AUDIT_S77; **keinem Checklistenpunkt zugeordnet**) | ENTSCHEIDUNG VORBEREITET |
| F-1 | Nachweis, wer freigegeben hat; Vier-Augen-Prinzip? | Owner (F-1) | ENTSCHEIDUNG VORBEREITET |
| C-7 | Modell, Preise, Budget, Abrechnungsabgleich | Owner (C-7) | NACHWEIS FEHLT |
| C-11 | Ablage: Ort, Verschlüsselung, Zugriff, Aufbewahrung, Löschung, Sicherung | Owner/Datenschutz (C-11) | ENTSCHEIDUNG VORBEREITET |

**Reihenfolge und Abhängigkeiten:**
- **Juristische Punkte:** J1 → J2 und J3 → J4 (zusammen mit J6). J5 und F-1 lassen sich unabhängig vorbereiten,
  müssen aber vor jeder Freigabe stehen.
- **Kosten:** C-7 hängt von C-1 (Modell) ab.
- **Ablage:** C-11 hängt vom IT-Betrieb ab.
- **Ausführung:** C-14 hängt von allen ab.

**Technische Folge jeder juristischen Änderung:**
1. Spezifikation bzw. Rubrik bearbeiten und `version` erhöhen; die Historie wird ergänzt.
2. Validator ausführen: `python -m scripts.legal_bench.answer_specs`.
3. Begründet neu sperren: `--update-lock --reason …`.
4. Danach sind ältere Freigaben automatisch **veraltet**: Datum vor der Neusperre bzw. anderer `plan_fingerprint`
   (§81).

## Kennzahlen der Spezifikationen

Aus den Dateien gezählt und per Test geprüft:

| Spezifikation | Akte | Split | Kriterien | Antwortvarianten (`accept_any`) | gefährliche Fehler | Fehlentscheidungen | offene Punkte der Referenz | Alternativergebnisse |
|---|---|---|---|---|---|---|---|---|
| LA-D005 | LE-D005 (BGH VIII ZR 18/24) | dev | 10 | 23 | 2 | 3 | 2 | 0 (Status `juristische_pruefung_offen`) |
| LA-D006 | LE-D006 (BAG 6 AZR 32/24) | dev | 12 | 28 | 2 | 3 | 2 | 0 (Status `juristische_pruefung_offen`) |
| LA-T004 | LE-T004 (BAG 2 AZR 72/24) | test, `frozen` | 12 | 25 | 2 | 3 | 2 | 0 (Status `juristische_pruefung_offen`) |

Alle drei haben den Status `entwurf_juristische_freigabe_offen`. In `rubric.json` ist `aggregation.weights/thresholds`
= `null`, und `thresholds_status` ist „OFFEN“.

---

## J1 – Referenzlösungen

- **Frage:** Stimmen `solution.expected_result`, `wrong_results`, `decisive_facts` und `open_points` der drei Fälle
  juristisch, und tragen sie für die synthetischen Akten?
- **Nachweise:**
  - Akten: `benchmarks/legal_e2e_v1/cases/dev/LE-D005.json`, `…/dev/LE-D006.json`, `…/test/LE-T004.json`, jeweils
    Block `solution`;
  - Quellen: `benchmarks/legal_e2e_v1/source_register.json`;
  - Befund V5 in `AUDIT_S77.md`: „aus Leitsätzen abgeleitet; juristisch geprüft sind sie nicht“.
- **Optionen:**

| Option | Vorteil | Nachteil / Folge für den Benchmark |
|---|---|---|
| a) unverändert freigeben | kein Änderungsaufwand | nur sinnvoll nach tatsächlicher Prüfung; ein Fehler in der Referenz verfälscht jede Bewertung |
| b) korrigieren | Referenz belastbar | Versionssprung, Neusperre (auch LA-T004 – zulässig, solange kein Lauf stattfand); J2 und J3 neu prüfen |
| c) Fall verwerfen | keine unsichere Referenz | weniger als 3 Fälle; die ohnehin geringe Aussagekraft sinkt weiter; beim Test-Fall bliebe kein Test-Split |

- **Risiken:** Die synthetischen Akten sind kürzer und eindeutiger als die Originalentscheidungen; ein Leitsatz kann
  Sachverhaltsdetails voraussetzen, die in der Akte fehlen.
- **Trennung der Verantwortung:**
  - juristisch: die Bewertung;
  - technisch: Versionierung und Sperre;
  - organisatorisch: wer prüft (J5).
- **Abschluss:** je Fall eine schriftliche Entscheidung mit Person und Datum.

## J2 – Antwortvarianten und Verdachtsformulierungen

- **Frage:** Sind die `accept_any`-Varianten (zusammen 76) und die Verdachtsformulierungen der 6 gefährlichen Fehler
  vollständig und richtig, einschließlich der §77-Ergänzungen (K8–K12 und die G1-Ergänzung in LA-T004)?
- **Nachweise:** `specs/dev/LA-D005.json`, `specs/dev/LA-D006.json`, `specs/test/LA-T004.json` sowie in `AUDIT_S77.md`
  die Befunde N4, V1 und V2. V1 zeigt die Grenze: Die Vorprüfung ist wortbasiert und versteht keine Verneinung.
- **Optionen:**

| Option | Vorteil | Nachteil / Folge |
|---|---|---|
| a) bestätigen | sofort nutzbar | fehlende Varianten führen zu „nicht_gefunden“-Hinweisen; die Note vergibt ohnehin die Person (Rubrik-Regel) |
| b) ergänzen/ändern | weniger Fehlhinweise | Versionssprung, Neusperre |
| c) Varianten nur als Hilfe kennzeichnen | macht die Grenze der Vorprüfung sichtbar | ändert nichts an der Note, die schon heute bei der Person liegt |

- **Risiko:** Verdachtsformulierungen schlagen auch bei korrekter Verneinung an. Das ist gewollt, denn ein Verdacht
  verpflichtet nur zur Prüfung (Rubrik-Regel 2).
- **Abschluss:** je Kriterium und je gefährlichem Fehler eine Entscheidung (bestätigt, geändert oder gestrichen).

## J3 – Alternativergebnisse

- **Frage:** Welche abweichenden, aber vertretbaren Ergebnisse sind wie die Referenz zu bewerten, oder ausdrücklich
  keine?
- **Nachweise:** In allen drei Spezifikationen ist `alternative_results` leer, und `alternative_results_status` steht
  auf `juristische_pruefung_offen`. Rubrik-Regel 5: Ist kein Alternativergebnis festgelegt, entscheidet und begründet
  die Person.
- **Status OFFEN:** Vorschläge hat der Agent bewusst **nicht** gemacht (`AUDIT_S77.md`, V3).
- **Optionen:**
  - a) Alternativen je Fall festlegen: einheitliche Bewertung, braucht aber juristischen Aufwand;
  - b) ausdrücklich „keine“: klare Regel, kann vertretbare Antworten aber zu streng bewerten;
  - c) offen lassen: dann entscheidet jede Person einzeln, was die Vergleichbarkeit verringert.
- **Abschluss:** Feld gefüllt oder ausdrücklich leer; der Status ist nicht mehr „offen“.

## J4 – Gewichte und Schwellen

- **Frage:** Wie werden die 7 Dimensionen gewichtet, und ab welcher Schwelle gilt PASS bzw. FAIL?
- **Nachweise:** `rubric.json` mit `aggregation.weights/thresholds = null` und `thresholds_status` „OFFEN …“. Die
  Regel heute: höchstens FAIL_KRITISCH oder OFFEN, nie PASS.
- **Status OFFEN:** Es gibt **keine Kalibrierungsdaten**, denn Modellantworten existieren nicht.
- **Optionen:**

| Option | Vorteil | Nachteil / Folge |
|---|---|---|
| a) Schwellen a priori festlegen | unabhängig von Ergebnissen, kein Anpassen an Ergebnisse | ohne Erfahrungswerte womöglich zu streng oder zu lax |
| b) erst ein Dev-Lauf, dann Schwellen | Erfahrungswerte aus LA-D005/D006 | braucht einen freigegebenen Lauf mit allen übrigen Freigaben; Schwellen dürfen den **Test-Split** nicht sehen |
| c) keine Gesamtschwelle, nur Einzeldimensionen berichten | ehrlich, kein Scheinurteil | kein PASS/FAIL; dafür bleibt der Bericht deskriptiv |

- **Abschluss:** Werte in der Rubrik, Versionssprung, Neusperre.

## J5 – Bewertende Personen

- **Frage:** Welche Qualifikation wird verlangt, wann ist eine Zweitprüfung Pflicht (die Vorlage sieht
  `second_reviewer_for_critical` vor), und wie werden Abweichungen aufgelöst?
- **Nachweise:** `run_config.template.json` mit `evaluation.reviewers = null`, `FREIGABE_CHECKLISTE.md` C-15 und die
  README-Empfehlung „mindestens eine qualifizierte juristische Person, bei kritischen Dimensionen besser zwei“.
- **Status NACHWEIS FEHLT:** Es sind weder Personen noch eine Qualifikationsanforderung dokumentiert.
- **Optionen:**
  - a) eine Person: geringer Aufwand, keine Kontrolle der Übereinstimmung;
  - b) zwei Personen für kritische Dimensionen;
  - c) zwei Personen überall, mit Übereinstimmungsmaß.

  Je mehr Personen, desto höher die Belastbarkeit und desto höher der Aufwand.
- **Abschluss:** eine namentliche Liste mit Qualifikation und dem Verfahren bei Abweichungen.

## J6 – Regeln zu n/a und Unentscheidbarem

- **Frage:** Werden die Rubrik-Regeln 4–6 bestätigt?
  - **Regel 4:** n/a nur mit schriftlicher Begründung, sonst NICHT_BEWERTBAR.
  - **Regel 5:** Alternativergebnis.
  - **Regel 6:** Nicht Entscheidbares soll als Unsicherheit benannt werden; eine Festlegung ohne Hinweis ergibt 0 in
    „unsicherheiten“.
- **Nachweise:** `rubric.json` (`aggregation.rule_text`), `AUDIT_S77.md` (J6).
- **Lücke W2:** J6 ist in der Checkliste **keinem** Punkt und keinem Freigabefeld zugeordnet. Ob J6 unter C-13
  (Rubrik) fällt oder einen eigenen Punkt bekommt, ist eine Owner-Entscheidung; der Agent ordnet es nicht zu.
- **Optionen:**
  - a) bestätigen;
  - b) ändern, zum Beispiel n/a strenger begrenzen. Das erfordert einen Versionssprung und eine Neusperre.

## F-1 – Nachweis der Freigaben

- **Frage:** Wie wird belastbar nachgewiesen, **wer** freigegeben hat?
- **Heute:** Erkannt werden Form, Datum, Planbindung und Widersprüche (§81). Wer den Eintrag geschrieben hat, ist
  **nicht** feststellbar.
- **Nachweise:** `AUDIT_S81.md` (Abschnitt 1), `scripts/legal_bench/answer_run.py` (`_approval_findings`),
  `tests/test_s81_benchmark_hardening.py`.

| Option | Vertrauensanker | Schlüsselverwaltung | Rotation/Widerruf | Vorteil | Nachteil |
|---|---|---|---|---|---|
| a) signierte Git-Commits/Tags (GPG, SSH oder S/MIME) der Freigabekonfiguration durch die Freigebenden | öffentliche Schlüssel je Rolle, hinterlegt außerhalb der Konfiguration (z. B. eigene, geschützte Datei oder Git-Hoster) | bei den Personen (Token oder Smartcard empfohlen) | Schlüsselliste versionieren; Widerruf = Eintrag entfernen | nutzt vorhandene Git-Werkzeuge | Einrichtung je Person; die Prüfung im Gate muss erst gebaut und negativ getestet werden |
| b) Freigabe außerhalb des Repositorys (unterschriebenes Dokument oder Ticket), in der Konfiguration nur ein Verweis | organisatorisch (Ablage beim Owner) | entfällt | entfällt | keine Technik nötig | technisch nicht prüfbar; das Gate prüft weiter nur Form und Bezug |
| c) bewusster Verzicht | – | – | – | kein Aufwand | Urheberschaft bleibt unbelegt (Restrisiko bewusst akzeptiert und dokumentiert) |

- **Vier-Augen-Prinzip:** Heute ist es nicht erzwungen; Owner, Datenschutz und juristische Freigabe könnten von
  derselben Person stammen. Bei einer Entscheidung dafür wäre eine Prüfung „Personen verschieden“ technisch einfach.
- **Abschluss:** Verfahren gewählt; bei a) Schlüssel hinterlegt und Prüfung implementiert. Keine Scheinsignatur.

## C-7 – Kosten

**Nachweise:** `AUDIT_S80.md` (E3), `AUDIT_S81.md` (Abschnitt 2), `run_config.template.json` (Felder `null`),
`scripts/legal_bench/answer_exec.py` (`actual_cost`).

**Fehlende Angaben** (alle beim Owner, nichts davon geschätzt):

| Angabe | Feld / Ort | Hinweis |
|---|---|---|
| Modell-ID und genaue Version | `model.model_id`, `model.model_version` (C-1) | bestimmt den Preis |
| Eingabe- und Ausgabepreis je 1 Mio. Tokens | `limits.price_eur_per_million_*` | mit Quelle (Preisliste) und Datum belegen |
| Währung, Kurs, Stichtag | – (nicht im Schema) | Felder sind EUR; Preislisten oft in USD |
| netto/brutto (USt.) | – (nicht im Schema) | Grundlage des Budgets |
| Zusatzgebühren (Caching, Batch, Web-Suche) | – (im Kostenbuch **nicht** abgebildet) | Web-Suche des Providers prüfen bzw. deaktivieren |
| Schätzung je Aufruf | `limits.price_per_call_eur_estimate` | Gate verlangt mindestens `max_tokens` × Ausgabepreis |
| Budgetgrenze | `limits.max_cost_eur` | Owner |
| Abrechnungsabgleich | – | Person und Frist für den Vergleich Kostenbuch ↔ Rechnung |

## C-11 – Ablage

**Nachweise:** `AUDIT_S80.md` (E2), `AUDIT_S81.md` (Abschnitt 3), `scripts/legal_bench/answer_store.py`, Tests §80 und
§81. Heute umgesetzt: Ablage nur außerhalb von Repository und Lexono-Daten, Konto-ACL, Hashes, Prüfung erweiterter
Rechte, kontrollierte Löschung. Es gibt **keine Verschlüsselung**.

| Option | Schlüssel | Wiederherstellung | Zugriff | Aufbewahrung/Löschung | Sicherung | offen |
|---|---|---|---|---|---|---|
| A DPAPI (kontogebunden) | Betriebssystem, Benutzerkonto | **keine** bei Kontoverlust | nur dieses Konto | `RunStore.delete` | Sicherung bleibt verschlüsselt, nur mit demselben Konto lesbar | Datenverlust-Risiko akzeptieren? |
| B EFS auf dem Ablageordner | Zertifikat des Kontos | über Wiederherstellungsagent (DRA), falls eingerichtet | Konto plus DRA | wie oben | abhängig von der Sicherungssoftware | DRA vorhanden? (**NICHT VERIFIZIERT**) |
| C BitLocker auf dem Laufwerk | TPM / Betriebssystem | Wiederherstellungsschlüssel | alle Prozesse des angemeldeten Kontos | wie oben | Sicherung **unverschlüsselt**, sofern nicht zusätzlich geregelt | Status auf diesem Gerät **NICHT VERIFIZIERT** |
| D anwendungsseitig (`cryptography`) | eigene sichere Schlüsselablage nötig | Schlüsselsicherung nötig | konfigurierbar | wie oben | verschlüsselt | Schlüsselort und -sicherung ungeklärt – **nicht ohne Entscheidung bauen** |

**Zu entscheiden:**
1. Speicherort;
2. Verfahren (A–D oder Kombination);
3. Zugriffskreis;
4. Aufbewahrungsdauer (`storage.retention`);
5. wer löscht und ob die Löschung bestätigt wird;
6. ob gesichert wird, und wenn ja, ob verschlüsselt.
