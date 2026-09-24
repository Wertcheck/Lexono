"""Baut den `MailProvider` aus der Konfiguration - oder `None`, wenn kein
Postfach eingerichtet ist (`settings.mail_provider` ist `None`, Standard).
Analoges Muster wie `app/ai_providers/factory.py::build_local_llm_provider`
(optionale Faehigkeit, `None` bedeutet fuer den Aufrufer "nichts tun",
NICHT "versuchen und scheitern").

Aktuell einziger unterstuetzter Wert: "imap" (ARCHITECTURE.md §10: "IMAP
zuerst", einzige bisher implementierte `MailProvider`-Konkretisierung).
Ein anderer/unbekannter Wert in `mail_provider` liefert bewusst `None`
(kein Fehler) - derselbe sichere Standardfall wie "gar nicht
konfiguriert", da es strukturell noch keine zweite Implementierung zum
Verwechseln gibt."""

from __future__ import annotations

from app.config import Settings
from app.mail.base import MailProvider
from app.mail.imap_provider import ImapMailProvider


def build_mail_provider(settings: Settings) -> MailProvider | None:
    if not settings.mail_provider:
        return None
    if settings.mail_provider == "imap":
        if not settings.mail_host or not settings.mail_username or not settings.mail_password:
            return None
        return ImapMailProvider(
            host=settings.mail_host,
            port=settings.mail_port,
            username=settings.mail_username,
            password=settings.mail_password.get_secret_value(),
            mailbox=settings.mail_mailbox,
            use_ssl=settings.mail_use_ssl,
            mark_seen=settings.mail_mark_seen,
        )
    return None
