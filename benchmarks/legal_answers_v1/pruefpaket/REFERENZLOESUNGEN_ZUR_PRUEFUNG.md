# Referenzlösungen zur fachlichen Prüfung (`legal_answers_v1`)

**NICHT juristisch geprüft.** Zusammenstellung der vorhandenen Inhalte für die spätere Prüfung durch eine qualifizierte juristische Person (J1–J3, `ENTSCHEIDUNGSMATRIX.md`). Die Akten sind synthetisch; Rechtsfrage und Ergebnis stammen aus amtlichen Leitsätzen. Die Entscheidungsfelder sind absichtlich leer – eingetragene Entscheidungen ändern nichts an Spezifikation, Sperre oder Gate; Änderungen erfolgen erst danach bewusst (Versionssprung, begründete Neusperre).

Erzeugt aus den Quelldateien durch `scripts/legal_bench/review_packet.py`; `tests/test_s83_pilot_readiness.py` prüft die Übereinstimmung.

## LA-D005 – Mietervorkaufsrecht bei Verkauf an eine personenidentische Gesellschaft (LE-D005, Split `dev`)

- Spezifikation: `benchmarks/legal_answers_v1/specs/dev/LA-D005.json`
- Akte: `benchmarks/legal_e2e_v1/cases/dev/LE-D005.json` (Block `solution`)
- Status: `entwurf_juristische_freigabe_offen`; Alternativergebnisse: `juristische_pruefung_offen`
- Quelle: BGH VIII ZR 18/24 vom 2025-10-08 (ECLI:DE:BGH:2025:081025UVIIIZR18.24.0), verwendet: amtlicher Leitsatz (Rechtsfrage und Ergebnis)
- Quellenumfang laut Spezifikation: BGH VIII ZR 18/24, amtlicher Leitsatz (Rechtsfrage und Ergebnis); Wortlaut §§ 469, 577 BGB. Keine Entscheidungsgründe, keine Literatur, keine fremden Falllösungen.
- Register: Q-RECHTSPRECHUNG-IM-INTERNET = Rechtsprechung im Internet (Bundesgerichte); Q-GESETZE-IM-INTERNET = Gesetze im Internet (Bundesrecht)

**Aufgaben:**
- Q1 (chat): Besteht ein Vorkaufsrecht, und bis wann muss es ausgeübt werden?
- Q2 (schriftsatz): Ausübung des Vorkaufsrechts gegenüber der Verkäuferin mit Begründung.

### J1 – Referenzlösung

**Rechtsfrage:** Steht dem Mieter beim Verkauf der umgewandelten Wohnung an eine Gesellschaft mit personenidentischen Gesellschaftern ein Vorkaufsrecht zu, und bis wann ist es auszuüben?

**Erwartetes Ergebnis:** Ja, dem Mieter steht das Vorkaufsrecht zu. Auch die Veräußerung an eine Personenhandelsgesellschaft mit personenidentischen Gesellschaftern ist ein Verkauf an einen Dritten (§ 577 Abs. 1 Satz 1 BGB; BGH VIII ZR 18/24, Leitsatz). Das Wohnungseigentum wurde nach der Überlassung begründet. Die Ausübung erfolgt schriftlich gegenüber der Verkäuferin (§ 577 Abs. 3 BGB) innerhalb von zwei Monaten nach Empfang der Mitteilung (§ 469 Abs. 2 BGB); wegen des streitigen Zugangs vorsorglich bis 28.04.2026.

**Tragende Normen:**
- § 577
- § 469

**Entscheidende Tatsachen:**
- F2: Wohnungseigentum nach Überlassung begründet (Teilungserklärung) (12.06.2019)
- F3: Kaufvertrag und Kaufpreis (24.02.2026, 287.500,00 EUR)
- F5: Zugang der Mitteilung laut Verkäuferin (28.02.2026)
- F6: Käuferin mit denselben Gesellschaftern (dieselben Gesellschafter)

**Fristen der Referenz:**
- 28.04.2026

**Falsche Ergebnisse (als Verdacht erfasst):**
- kein Vorkaufsrecht besteht
- Vorkaufsrecht besteht nicht
- kein Verkauf an einen Dritten

**Offene Punkte der Referenz:**
- O1: Zugang der Mitteilung streitig (W1) - vorsorglich vom früheren Zugang ausgehen
- O2: Zustellnachweis fehlt - anfordern

**Herkunft:**
- belegt: Rechtsfrage und Ergebnis (amtlicher Leitsatz VIII ZR 18/24)
- belegt: Wortlaut §§ 469, 577 BGB
- synthetisch: Personen, Gesellschaften, Anschriften, Daten, Kaufpreis, Zugangsstreit, Fristberechnung

| Entscheidung (☐ bestätigt / ☐ korrigiert / ☐ verworfen) | Begründung | Person, Rolle | Datum |
|---|---|---|---|
| | | | |

### J2 – Kriterien, Antwortvarianten, gefährliche Fehler

| Kriterium | Dimension | Aussage | zulässige Varianten (`accept_any`) |
|---|---|---|---|
| K1 | ergebnis | Dem Mieter steht das Vorkaufsrecht zu; auch der Verkauf an eine Gesellschaft mit personenidentischen Gesellschaftern ist ein Verkauf an einen Dritten. | Vorkaufsrecht+steht+Mieter / Vorkaufsrecht+besteht / vorkaufsberechtigt |
| K2 | rechtsgrundlagen | § 577 Abs. 1 BGB (Vorkaufsrecht bei Umwandlung nach Überlassung) | § 577 / §577 / 577 BGB |
| K3 | rechtsgrundlagen | § 469 Abs. 2 BGB (Ausübungsfrist zwei Monate nach Empfang der Mitteilung) | § 469 / §469 / 469 BGB |
| K4 | tatsachen | Wohnungseigentum nach der Überlassung begründet (Teilungserklärung 12.06.2019) | 12.06.2019 / Teilungserklärung / nach der Überlassung |
| K5 | tatsachen | Käuferin mit denselben Gesellschaftern | dieselben Gesellschafter / denselben Gesellschaftern / personenidentisch |
| K6 | fristen | Vorsorglich Ausübung bis 28.04.2026 (früherer Zugang laut Verkäuferin) | 28.04.2026 |
| K7 | unsicherheiten | Zugang der Mitteilung streitig (28.02. vs. 03.03.2026) - vorsorglich vom früheren Zugang ausgehen | Zugang+streitig / Zugang+28.02.2026 / 28.02.2026 |
| K8 | tatsachen | Kaufvertrag vom 24.02.2026, Kaufpreis 287.500,00 EUR | 24.02.2026 / 287.500,00 EUR |
| K9 | tatsachen | Zugang der Mitteilung laut Verkäuferin am 28.02.2026 | 28.02.2026 |
| K10 | unsicherheiten | Zustellnachweis fehlt - anfordern | Zustellnachweis |

| Gefährlicher Fehler | Aussage | Verdachtsformulierungen |
|---|---|---|
| G1 | Vorkaufsrecht verneint, weil die Käuferin dieselben Gesellschafter hat | kein Vorkaufsrecht / Vorkaufsrecht besteht nicht / Vorkaufsrecht+nicht zu / kein Verkauf an einen Dritten |
| G2 | Späteres Fristende (04.05.2026) als sicheres Fristende ohne Vorsorgehinweis | bis 04.05.2026+ausreichend / Frist endet sicher am 04.05.2026 |

**Typische Fehlentscheidungen:**
- Annahme, ein Verkauf an eine personenidentische Gesellschaft sei kein Verkauf an einen Dritten
- Fristbeginn mit dem späteren Zugangsdatum ohne Hinweis auf den Streit
- Ausübung gegenüber der Käuferin statt gegenüber der Verkäuferin

| Entscheidung (☐ bestätigt / ☐ korrigiert / ☐ verworfen) | Begründung | Person, Rolle | Datum |
|---|---|---|---|
| | | | |

### J3 – Alternativergebnisse

Derzeit: keine eingetragen (Status `juristische_pruefung_offen`). Festzulegen: vertretbare Alternativen oder ausdrücklich keine.

| Entscheidung (☐ bestätigt / ☐ korrigiert / ☐ verworfen) | Begründung | Person, Rolle | Datum |
|---|---|---|---|
| | | | |

## LA-D006 – Insolvenz: Sammelanmeldung der Bundesagentur für Arbeit ohne Einzelangaben (LE-D006, Split `dev`)

- Spezifikation: `benchmarks/legal_answers_v1/specs/dev/LA-D006.json`
- Akte: `benchmarks/legal_e2e_v1/cases/dev/LE-D006.json` (Block `solution`)
- Status: `entwurf_juristische_freigabe_offen`; Alternativergebnisse: `juristische_pruefung_offen`
- Quelle: BAG 6 AZR 32/24 vom 2025-02-20 (ECLI:DE:BAG:2025:200225.U.6AZR32.24.0), verwendet: amtliche Leitsaetze; Gruende Rn. 12 f. (ordnungsgemaesse Anmeldung als Sachurteilsvoraussetzung)
- Quellenumfang laut Spezifikation: BAG 6 AZR 32/24, amtliche Leitsätze 1 und 2, Gründe zur Anmeldung als Sachurteilsvoraussetzung; Wortlaut § 174 InsO. Keine Literatur, keine fremden Falllösungen.
- Register: Q-RECHTSPRECHUNG-IM-INTERNET = Rechtsprechung im Internet (Bundesgerichte); Q-GESETZE-IM-INTERNET = Gesetze im Internet (Bundesrecht)

**Aufgaben:**
- Q1 (chat): Ist die Forderungsanmeldung der Agentur ordnungsgemäß, und schadet die fehlende Unterschrift?
- Q2 (schriftsatz): Stellungnahme an die Agentur zur Anmeldung und zur ungeklärten Differenz.

### J1 – Referenzlösung

**Rechtsfrage:** Ist die Sammelanmeldung der Agentur ordnungsgemäß, und schadet die fehlende Unterschrift?

**Erwartetes Ergebnis:** Die Anmeldung vom 12.01.2026 war ohne Angabe der einzelnen Arbeitnehmer, ihres monatlichen Bruttoentgelts und der Anspruchszeiträume nicht ordnungsgemäß (§ 174 Abs. 2 InsO; BAG 6 AZR 32/24, Leitsatz 2). Die fehlende Unterschrift schadet nicht, weil § 130 Nr. 6 ZPO auf Forderungsanmeldungen keine Anwendung findet (Leitsatz 1). Die ordnungsgemäße Anmeldung ist Sachurteilsvoraussetzung einer Feststellung zur Tabelle. Erst mit der Aufstellung (Eingang 06.02.2026) ist sie für 31.497,75 EUR prüffähig; die Differenz von 614,25 EUR ist nicht belegt.

**Tragende Normen:**
- § 174
- § 130

**Entscheidende Tatsachen:**
- F1: Anmeldung mit Gesamtbetrag (12.01.2026, 32.112,00 EUR)
- F2: Keine Einzelangaben zu Arbeitnehmern und Bruttoentgelt (nicht aufgeführt)
- F3: Anmeldung nicht unterschrieben (nicht unterschrieben)
- F4: Nachgereichte Aufstellung (Eingang) (06.02.2026)
- F5: Summe der Aufstellung (31.497,75 EUR)

**Fristen der Referenz:**
- 17.02.2026

**Falsche Ergebnisse (als Verdacht erfasst):**
- Unterschrift ist zwingend
- wegen der fehlenden Unterschrift unwirksam
- Anmeldung vom 12.01.2026 ist ordnungsgemäß

**Offene Punkte der Referenz:**
- O1: Differenz von 614,25 EUR ungeklärt (W1)
- O2: Unterlagen zum vierten Arbeitnehmer fehlen

**Herkunft:**
- belegt: Leitsaetze 1 und 2 von 6 AZR 32/24
- belegt: Gruende: Anmeldung als Sachurteilsvoraussetzung
- belegt: Wortlaut § 174 InsO
- synthetisch: Schuldnerin, Arbeitnehmer, Betraege, Daten, Differenz, Pruefungstermin

| Entscheidung (☐ bestätigt / ☐ korrigiert / ☐ verworfen) | Begründung | Person, Rolle | Datum |
|---|---|---|---|
| | | | |

### J2 – Kriterien, Antwortvarianten, gefährliche Fehler

| Kriterium | Dimension | Aussage | zulässige Varianten (`accept_any`) |
|---|---|---|---|
| K1 | ergebnis | Die Anmeldung vom 12.01.2026 war ohne Einzelangaben nicht ordnungsgemäß; die fehlende Unterschrift schadet nicht. | nicht ordnungsgemäß / nicht ordnungsgemäss / nicht hinreichend |
| K2 | ergebnis | Fehlende Unterschrift unschädlich (§ 130 Nr. 6 ZPO nicht anwendbar) | Unterschrift+schadet nicht / Unterschrift+nicht erforderlich / Unterschrift+unschädlich |
| K3 | rechtsgrundlagen | § 174 Abs. 2 InsO (Angabe von Grund und Betrag) | § 174 / §174 / 174 InsO |
| K4 | rechtsgrundlagen | § 130 Nr. 6 ZPO nicht anwendbar | § 130 / §130 / 130 ZPO |
| K5 | tatsachen | Anmeldung vom 12.01.2026 mit Gesamtbetrag ohne Einzelangaben | 12.01.2026 / 32.112,00 EUR |
| K6 | tatsachen | Nachgereichte Aufstellung (Eingang 06.02.2026) über 31.497,75 EUR | 31.497,75 EUR / 31.497,75 |
| K7 | fristen | Stellungnahme an die Agentur bis 17.02.2026 | 17.02.2026 |
| K8 | unsicherheiten | Differenz von 614,25 EUR ungeklärt | 614,25 / Differenz |
| K9 | tatsachen | Keine Einzelangaben zu Arbeitnehmern und Bruttoentgelt | nicht aufgeführt / keine Einzelangaben / ohne Einzelangaben |
| K10 | tatsachen | Anmeldung nicht unterschrieben | nicht unterschrieben / fehlende Unterschrift / ohne Unterschrift |
| K11 | tatsachen | Nachgereichte Aufstellung, Eingang 06.02.2026 | 06.02.2026 |
| K12 | unsicherheiten | Unterlagen zum vierten Arbeitnehmer fehlen | vierten Arbeitnehmer / Unterlagen |

| Gefährlicher Fehler | Aussage | Verdachtsformulierungen |
|---|---|---|
| G1 | Anmeldung wegen fehlender Unterschrift für unwirksam gehalten | Unterschrift ist zwingend / wegen der fehlenden Unterschrift unwirksam |
| G2 | Anmeldung vom 12.01.2026 als ordnungsgemäß bewertet | Anmeldung vom 12.01.2026 ist ordnungsgemäß / Anmeldung war ordnungsgemäß |

**Typische Fehlentscheidungen:**
- Ordnungsgemäßheit allein wegen des Gesamtbetrags bejaht
- Unterschrift als Wirksamkeitsvoraussetzung behandelt
- Differenz von 614,25 EUR ungeprüft übernommen

| Entscheidung (☐ bestätigt / ☐ korrigiert / ☐ verworfen) | Begründung | Person, Rolle | Datum |
|---|---|---|---|
| | | | |

### J3 – Alternativergebnisse

Derzeit: keine eingetragen (Status `juristische_pruefung_offen`). Festzulegen: vertretbare Alternativen oder ausdrücklich keine.

| Entscheidung (☐ bestätigt / ☐ korrigiert / ☐ verworfen) | Begründung | Person, Rolle | Datum |
|---|---|---|---|
| | | | |

## LA-T004 – Kündigung durch ein Generalkonsulat: Staatenimmunität und Sonderkündigungsschutz (LE-T004, Split `test`)

- Spezifikation: `benchmarks/legal_answers_v1/specs/test/LA-T004.json`
- Akte: `benchmarks/legal_e2e_v1/cases/test/LE-T004.json` (Block `solution`)
- Status: `entwurf_juristische_freigabe_offen`; Alternativergebnisse: `juristische_pruefung_offen`
- Quelle: BAG 2 AZR 72/24 vom 2025-04-03 (ECLI:DE:BAG:2025:030425.U.2AZR72.24.0), verwendet: amtlicher Leitsatz; Tenor (Aufhebung und Zurueckverweisung)
- Quellenumfang laut Spezifikation: BAG 2 AZR 72/24, amtlicher Leitsatz und Tenor; Wortlaut § 4 KSchG, § 168 SGB IX. Keine Literatur, keine fremden Falllösungen.
- Register: Q-RECHTSPRECHUNG-IM-INTERNET = Rechtsprechung im Internet (Bundesgerichte); Q-GESETZE-IM-INTERNET = Gesetze im Internet (Bundesrecht)

**Aufgaben:**
- Q1 (chat): Kann die Mandantin trotz der behaupteten Immunität klagen, und bis wann?
- Q2 (schriftsatz): Kündigungsschutzklage mit Ausführungen zur Gerichtsbarkeit und zum Sonderkündigungsschutz.

### J1 – Referenzlösung

**Rechtsfrage:** Ist die Kündigungsschutzklage trotz Berufung des Staates auf Gründe aus dem hoheitlichen Bereich zulässig, und welche Frist und welcher Sonderkündigungsschutz sind zu beachten?

**Erwartetes Ergebnis:** Die Überprüfung der Kündigung ist nicht insgesamt der deutschen Gerichtsbarkeit entzogen, sondern nur, soweit hoheitliches Handeln zu beurteilen wäre (BAG 2 AZR 72/24, Leitsatz). Die Klage ist deshalb zu erheben. Die Dreiwochenfrist läuft ab Zugang (§ 4 Satz 1 KSchG); wegen des streitigen Zugangs vorsorglich bis 05.02.2026. Bei Schwerbehinderung ist die Zustimmung des Integrationsamts erforderlich (§ 168 SGB IX); sie fehlt.

**Tragende Normen:**
- § 4
- § 168

**Entscheidende Tatsachen:**
- F3: Ordentliche Kündigung zum (30.04.2026)
- F4: Zugang laut Konsulat (15.01.2026)
- F5: Zugang laut Mandantin (16.01.2026)
- F6: Schwerbehinderung (Grad der Behinderung von 50)
- F7: Berufung auf hoheitliche Gründe (Gründen aus dem hoheitlichen Bereich)
- F8: Tätigkeit ohne hoheitlichen Bezug laut Mandantin (Visa-Anträge erfasst)

**Fristen der Referenz:**
- 05.02.2026

**Falsche Ergebnisse (als Verdacht erfasst):**
- kann nicht überprüft werden
- Klage ist unzulässig
- keine deutsche Gerichtsbarkeit

**Offene Punkte der Referenz:**
- O1: Zugang streitig (W1) - vorsorglich Fristende 05.02.2026
- O2: Schwerbehindertenbescheid liegt nicht vor

**Herkunft:**
- belegt: amtlicher Leitsatz und Tenor 2 AZR 72/24
- synthetisch: Staat, Personen, Orte, Daten, Gehalt, Zugangsstreit, Schwerbehinderung

| Entscheidung (☐ bestätigt / ☐ korrigiert / ☐ verworfen) | Begründung | Person, Rolle | Datum |
|---|---|---|---|
| | | | |

### J2 – Kriterien, Antwortvarianten, gefährliche Fehler

| Kriterium | Dimension | Aussage | zulässige Varianten (`accept_any`) |
|---|---|---|---|
| K1 | ergebnis | Die Kündigung ist nicht insgesamt der deutschen Gerichtsbarkeit entzogen, nur soweit hoheitliches Handeln zu beurteilen wäre; Klage ist zu erheben. | Gerichtsbarkeit+nicht+entzogen / Klage+erheben / nicht insgesamt+Gerichtsbarkeit |
| K2 | rechtsgrundlagen | § 4 Satz 1 KSchG (Dreiwochenfrist ab Zugang) | § 4 KSchG / § 4 Satz 1 KSchG / §4 KSchG |
| K3 | rechtsgrundlagen | § 168 SGB IX (Zustimmung des Integrationsamts) | § 168 / §168 / 168 SGB IX |
| K4 | tatsachen | Zugang laut Konsulat am 15.01.2026 | 15.01.2026 |
| K5 | tatsachen | Schwerbehinderung (Grad der Behinderung von 50) | Grad der Behinderung von 50 / GdB 50 / schwerbehindert |
| K6 | tatsachen | Tätigkeit ohne hoheitlichen Bezug (Visa-Anträge erfasst) | Visa-Anträge erfasst / Visa-Anträge / Visaanträge |
| K7 | fristen | Klage vorsorglich bis 05.02.2026 | 05.02.2026 |
| K8 | unsicherheiten | Zugang streitig (15.01. vs. 16.01.2026) - vorsorglich Fristende 05.02.2026 | Zugang+streitig / Zugang+05.02.2026 |
| K9 | tatsachen | Ordentliche Kündigung zum 30.04.2026 | 30.04.2026 |
| K10 | tatsachen | Zugang laut Mandantin am 16.01.2026 | 16.01.2026 |
| K11 | tatsachen | Berufung des Staates auf Gründe aus dem hoheitlichen Bereich | Gründen aus dem hoheitlichen Bereich / hoheitlichen Bereich / hoheitliche Gründe |
| K12 | unsicherheiten | Schwerbehindertenbescheid liegt nicht vor | Bescheid |

| Gefährlicher Fehler | Aussage | Verdachtsformulierungen |
|---|---|---|
| G1 | Abraten von der Klage wegen Staatenimmunität | Klage ist unzulässig / keine deutsche Gerichtsbarkeit / kann nicht überprüft werden / Gerichtsbarkeit+nicht gegeben |
| G2 | Späteres Fristende (06.02.2026) als sicheres Fristende ohne Vorsorgehinweis | Frist endet sicher am 06.02.2026 / bis 06.02.2026+ausreichend |

**Typische Fehlentscheidungen:**
- Pauschale Annahme der Immunität für jede Kündigung durch einen ausländischen Staat
- Übersehen des Sonderkündigungsschutzes bei Schwerbehinderung
- Fristberechnung ab dem späteren Zugang ohne Vorsorge

| Entscheidung (☐ bestätigt / ☐ korrigiert / ☐ verworfen) | Begründung | Person, Rolle | Datum |
|---|---|---|---|
| | | | |

### J3 – Alternativergebnisse

Derzeit: keine eingetragen (Status `juristische_pruefung_offen`). Festzulegen: vertretbare Alternativen oder ausdrücklich keine.

| Entscheidung (☐ bestätigt / ☐ korrigiert / ☐ verworfen) | Begründung | Person, Rolle | Datum |
|---|---|---|---|
| | | | |
