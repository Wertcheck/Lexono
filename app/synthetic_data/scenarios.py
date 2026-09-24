"""Fallszenario-Vorlagen (Prompt 29).

Jedes Szenario beschreibt einen realistischen, aber VOLLSTÄNDIG
FIKTIVEN steuerrechtlichen Fall - orientiert an den bereits im Projekt
verwendeten Dokumenttypen (`ALLOWED_DOCUMENT_TYPES`,
app/classification/schema.py) und Rechtsquellentypen (Prompt 14).

GRUNDREGEL (Konzept-Annahme A3, ARCHITECTURE.md §9, durchgängig im
gesamten Projekt eingehalten): AUSSCHLIESSLICH synthetische Daten - keine
echten Namen, keine echten Mandanten, keine echten Aktenzeichen. Alle
Namen sind deutsche Standard-Platzhandernamen ("Max Mustermann" u. Ä.,
das deutsche Äquivalent zu "John Doe") oder klar erfundene
Firmennamen-Muster. E-Mail-Adressen nutzen ausschließlich
`.invalid`/`.test`-Domains (RFC 2606) - technisch garantiert nicht
zustellbar, können also nie versehentlich eine echte Person erreichen.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CaseScenario:
    key: str
    matter_title_template: str
    practice_area: str
    email_subject_template: str
    email_body_template: str
    document_filename_template: str
    document_extracted_text_template: str
    classified_type: str
    has_deadline: bool
    deadline_days_from_now: int | None
    deadline_source_text_template: str | None
    #: Antwortentwurf der Kanzlei zu diesem Fall (15.09.).
    #:
    #: Grund: der Generator erzeugte Mandanten/Akten/Dokumente/Fristen, aber
    #: KEINE Entwuerfe - die Gold-Workflow-Stationen am Ende (Entwurf ->
    #: anwaltliche Freigabe -> Postausgang) hatten damit in der gesamten
    #: Demo-/Testbasis keinen einzigen realistischen Datensatz und liessen
    #: sich weder vorfuehren noch ehrlich End-to-End pruefen.
    #:
    #: Bewusst als feste Vorlage: der Generator ruft KEINE KI auf (keine
    #: Kosten, deterministisch, siehe Modul-Docstring). Der Text ist ein
    #: plausibler Kanzleischriftsatz, KEIN echter Rechtsrat - die Akte ist
    #: ueber die DEMO-Mandantennummer eindeutig als synthetisch erkennbar.
    draft_body_template: str | None = None


SCENARIOS: tuple[CaseScenario, ...] = (
    CaseScenario(
        key="einspruch_steuerbescheid",
        matter_title_template="Einspruch Steuerbescheid {jahr} – {mandant_kurz}",
        practice_area="Einkommensteuer",
        email_subject_template="Steuerbescheid {jahr} erhalten – bitte um Prüfung",
        email_body_template=(
            "Sehr geehrte Damen und Herren,\n\n"
            "anbei erhalten Sie den Steuerbescheid für das Jahr {jahr}. Ich bitte um "
            "Prüfung, insbesondere hinsichtlich der Werbungskosten in Zeile 14, die "
            "meines Erachtens nicht vollständig berücksichtigt wurden.\n\n"
            "Mit freundlichen Grüßen\n{mandant}"
        ),
        document_filename_template="steuerbescheid_{jahr}_{mandant_dateiname}.pdf",
        document_extracted_text_template=(
            "Bescheid für {jahr} über Einkommensteuer und Solidaritätszuschlag.\n"
            "Festgesetzte Einkommensteuer: {betrag} EUR.\n"
            "Werbungskosten wurden pauschal mit 1.230 EUR angesetzt.\n"
            "Rechtsbehelfsbelehrung: Einspruch innerhalb eines Monats nach Bekanntgabe."
        ),
        classified_type="Sonstiges",
        has_deadline=True,
        deadline_days_from_now=28,
        deadline_source_text_template=(
            "Einspruch ist innerhalb eines Monats nach Bekanntgabe des Bescheids "
            "vom {bescheid_datum} einzulegen."
        ),
        draft_body_template=(
            "Sehr geehrte Damen und Herren,\n\n"
            "namens und in Vollmacht unseres Mandanten legen wir gegen den "
            "Bescheid fuer {jahr} ueber Einkommensteuer und Solidaritaetszuschlag "
            "vom {bescheid_datum}\n\n"
            "                    E i n s p r u c h\n\n"
            "ein.\n\n"
            "Begruendung:\n"
            "Die Werbungskosten wurden lediglich mit dem Pauschbetrag von "
            "1.230 EUR beruecksichtigt. Tatsaechlich sind hoehere Aufwendungen "
            "angefallen, die wir belegen koennen. Wir beantragen, den Bescheid "
            "entsprechend zu aendern und die Steuer neu festzusetzen.\n\n"
            "Die Belege reichen wir binnen zwei Wochen nach.\n\n"
            "Mit freundlichen Gruessen"
        ),
    ),
    CaseScenario(
        key="betriebspruefung",
        matter_title_template="Betriebsprüfung {jahr} – {mandant_kurz}",
        practice_area="Betriebsprüfung",
        email_subject_template="Ankündigung einer Betriebsprüfung",
        email_body_template=(
            "Sehr geehrte Damen und Herren,\n\n"
            "das Finanzamt hat für unser Unternehmen eine Betriebsprüfung für die "
            "Jahre {jahr_von} bis {jahr} angekündigt. Der Prüfungsbeginn ist für den "
            "{pruefungsbeginn} vorgesehen. Könnten Sie uns dabei unterstützen und "
            "begleiten?\n\n"
            "Mit freundlichen Grüßen\n{mandant}"
        ),
        document_filename_template="pruefungsanordnung_{mandant_dateiname}.pdf",
        document_extracted_text_template=(
            "Prüfungsanordnung gemäß § 196 AO.\n"
            "Prüfungszeitraum: {jahr_von} bis {jahr}.\n"
            "Prüfungsbeginn: {pruefungsbeginn}.\n"
            "Zu prüfende Steuerarten: Körperschaftsteuer, Gewerbesteuer, Umsatzsteuer."
        ),
        classified_type="Gerichtliches Schreiben",
        has_deadline=False,
        deadline_days_from_now=None,
        deadline_source_text_template=None,
        draft_body_template=(
            "Sehr geehrte Damen und Herren,\n\n"
            "wir bestaetigen den Eingang der Pruefungsanordnung und zeigen an, "
            "dass wir unseren Mandanten im Pruefungsverfahren vertreten.\n\n"
            "Gegen die Pruefung als solche bestehen keine Einwaende. Wir bitten "
            "jedoch um Verlegung des vorgesehenen Pruefungsbeginns am "
            "{pruefungsbeginn}, da die angeforderten Unterlagen zu diesem "
            "Zeitpunkt noch nicht vollstaendig aufbereitet vorliegen.\n\n"
            "Wir schlagen einen Beginn zwei Wochen spaeter vor und sichern zu, "
            "die Unterlagen bis dahin vollstaendig bereitzustellen.\n\n"
            "Mit freundlichen Gruessen"
        ),
    ),
    CaseScenario(
        key="umsatzsteuer_nachschau",
        matter_title_template="Umsatzsteuer-Nachschau – {mandant_kurz}",
        practice_area="Umsatzsteuer",
        email_subject_template="Unterlagen zur Umsatzsteuer-Nachschau",
        email_body_template=(
            "Sehr geehrte Damen und Herren,\n\n"
            "anbei die angeforderten Unterlagen zur Umsatzsteuer-Nachschau, wie "
            "telefonisch besprochen. Bitte prüfen Sie, ob die Vorsteuerabzüge korrekt "
            "dokumentiert sind.\n\n"
            "Mit freundlichen Grüßen\n{mandant}"
        ),
        document_filename_template="ust_unterlagen_{mandant_dateiname}.pdf",
        document_extracted_text_template=(
            "Zusammenstellung der Vorsteuerabzüge für den Zeitraum {jahr}.\n"
            "Summe Vorsteuer: {betrag} EUR.\n"
            "Belege liegen für alle Positionen vor."
        ),
        classified_type="Sonstiges",
        has_deadline=False,
        deadline_days_from_now=None,
        deadline_source_text_template=None,
        draft_body_template=(
            "Sehr geehrte Damen und Herren,\n\n"
            "wir nehmen Bezug auf die angekuendigte Umsatzsteuer-Nachschau und "
            "uebersenden die angeforderten Unterlagen fuer den Zeitraum "
            "{jahr_von} bis {jahr}.\n\n"
            "Die Ausgangsrechnungen sind chronologisch geordnet; die "
            "Vorsteuerbetraege sind in der beigefuegten Aufstellung den "
            "jeweiligen Eingangsrechnungen zugeordnet.\n\n"
            "Fuer Rueckfragen stehen wir zur Verfuegung.\n\n"
            "Mit freundlichen Gruessen"
        ),
    ),
    CaseScenario(
        key="mahnung_zahlungsverzug",
        matter_title_template="Mahnung Zahlungsverzug – {mandant_kurz}",
        practice_area="Forderungsmanagement",
        email_subject_template="Mahnung erhalten – bitte um Prüfung",
        email_body_template=(
            "Sehr geehrte Damen und Herren,\n\n"
            "ich habe die beigefügte Mahnung erhalten und halte die Forderung für "
            "nicht berechtigt, da die zugrunde liegende Leistung nicht vollständig "
            "erbracht wurde. Ich bitte um rechtliche Einschätzung.\n\n"
            "Mit freundlichen Grüßen\n{mandant}"
        ),
        document_filename_template="mahnung_{mandant_dateiname}.pdf",
        document_extracted_text_template=(
            "Mahnung wegen Zahlungsverzugs.\n"
            "Offener Betrag: {betrag} EUR zzgl. Verzugszinsen.\n"
            "Zahlungsfrist: 14 Tage ab Zugang dieses Schreibens."
        ),
        classified_type="Mahnung",
        has_deadline=True,
        deadline_days_from_now=14,
        deadline_source_text_template="Zahlungsfrist: 14 Tage ab Zugang dieses Schreibens.",
        draft_body_template=(
            "Sehr geehrte Damen und Herren,\n\n"
            "wir zeigen an, dass wir die rechtlichen Interessen des "
            "Rechnungsempfaengers vertreten.\n\n"
            "Die von Ihnen geltend gemachte Forderung ueber {betrag} EUR weisen "
            "wir derzeit zurueck. Eine pruefbare Rechnung ist unserem Mandanten "
            "nicht zugegangen; der Verzug ist damit nicht eingetreten.\n\n"
            "Wir bitten um Uebersendung der Rechnung sowie um Nachweis des "
            "Zugangs. Bis dahin weisen wir die Mahnkosten zurueck.\n\n"
            "Mit freundlichen Gruessen"
        ),
    ),
    CaseScenario(
        key="vertragspruefung",
        matter_title_template="Vertragsprüfung – {mandant_kurz}",
        practice_area="Vertragsrecht",
        email_subject_template="Bitte um Prüfung eines Vertragsentwurfs",
        email_body_template=(
            "Sehr geehrte Damen und Herren,\n\n"
            "anbei ein Vertragsentwurf, den ich vor Unterzeichnung gerne rechtlich "
            "geprüft haben möchte, insbesondere die Haftungsklausel in § 8.\n\n"
            "Mit freundlichen Grüßen\n{mandant}"
        ),
        document_filename_template="vertragsentwurf_{mandant_dateiname}.pdf",
        document_extracted_text_template=(
            "Vertragsentwurf zwischen den Parteien.\n"
            "§ 8 Haftung: Die Haftung wird auf Vorsatz und grobe Fahrlässigkeit "
            "beschränkt.\n"
            "Laufzeit: 24 Monate, Kündigungsfrist 3 Monate zum Laufzeitende."
        ),
        classified_type="Vertrag",
        has_deadline=False,
        deadline_days_from_now=None,
        deadline_source_text_template=None,
        draft_body_template=(
            "Sehr geehrter Mandant,\n\n"
            "wir haben den uebersandten Vertragsentwurf geprueft und fassen das "
            "Ergebnis zusammen.\n\n"
            "1. Die Haftungsregelung ist einseitig zu Ihren Lasten ausgestaltet "
            "und sollte auf Vorsatz und grobe Fahrlaessigkeit begrenzt werden.\n"
            "2. Die Laufzeit von 24 Monaten bei automatischer Verlaengerung "
            "empfehlen wir auf 12 Monate zu verkuerzen.\n"
            "3. Die Zahlungsfrist von 60 Tagen sollte auf 30 Tage angepasst "
            "werden.\n\n"
            "Einen entsprechend geaenderten Entwurf fuegen wir bei.\n\n"
            "Mit freundlichen Gruessen"
        ),
    ),
    CaseScenario(
        key="kuendigung_widerspruch",
        matter_title_template="Widerspruch Kündigung – {mandant_kurz}",
        practice_area="Arbeitsrecht",
        email_subject_template="Kündigung erhalten – Widerspruch prüfen",
        email_body_template=(
            "Sehr geehrte Damen und Herren,\n\n"
            "mir wurde fristlos gekündigt, obwohl ich die vorgeworfenen Pflichtverletzungen "
            "bestreite. Ich möchte gerne Widerspruch einlegen und bitte um Ihre Einschätzung "
            "der Erfolgsaussichten.\n\n"
            "Mit freundlichen Grüßen\n{mandant}"
        ),
        document_filename_template="kuendigung_{mandant_dateiname}.pdf",
        document_extracted_text_template=(
            "Fristlose Kündigung des Arbeitsverhältnisses zum {bescheid_datum}.\n"
            "Begründung: Wiederholte Verletzung arbeitsvertraglicher Pflichten.\n"
            "Hinweis auf Klagefrist von drei Wochen (§ 4 KSchG)."
        ),
        classified_type="Kündigungsschreiben",
        has_deadline=True,
        deadline_days_from_now=21,
        deadline_source_text_template=(
            "Klagefrist von drei Wochen nach Zugang der Kündigung (§ 4 KSchG)."
        ),
        draft_body_template=(
            "Sehr geehrte Damen und Herren,\n\n"
            "namens und in Vollmacht unseres Mandanten widersprechen wir der "
            "Kuendigung vom {bescheid_datum}.\n\n"
            "Die Kuendigung ist aus unserer Sicht unwirksam. Eine "
            "ordnungsgemaesse Anhoerung ist nicht erfolgt; zudem fehlt es an "
            "einem hinreichenden Kuendigungsgrund.\n\n"
            "Wir fordern Sie auf, die Kuendigung bis zum Ablauf von zwei Wochen "
            "ab Zugang dieses Schreibens zurueckzunehmen und die Fortsetzung des "
            "Vertragsverhaeltnisses zu bestaetigen.\n\n"
            "Mit freundlichen Gruessen"
        ),
    ),
)
