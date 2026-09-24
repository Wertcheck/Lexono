"""Tests für app/mail/factory.py::build_mail_provider (14.09.) - analoges
Muster wie tests/test_ai_provider_factory.py::build_local_llm_provider."""

from __future__ import annotations

from app.config.settings import Settings
from app.mail.factory import build_mail_provider
from app.mail.imap_provider import ImapMailProvider


def test_build_mail_provider_returns_none_when_not_configured() -> None:
    settings = Settings()
    assert settings.mail_provider is None
    assert build_mail_provider(settings) is None


def test_build_mail_provider_returns_none_for_unknown_provider_value() -> None:
    settings = Settings(mail_provider="exchange")
    assert build_mail_provider(settings) is None


def test_build_mail_provider_returns_none_when_imap_credentials_incomplete() -> None:
    """`mail_provider="imap"` allein reicht nicht - ohne Host/Nutzername/
    Passwort waere ein Verbindungsversuch ohnehin sinnlos."""
    settings = Settings(mail_provider="imap")
    assert build_mail_provider(settings) is None


def test_build_mail_provider_returns_imap_provider_when_fully_configured() -> None:
    settings = Settings(
        mail_provider="imap",
        mail_host="imap.example.test",
        mail_port=993,
        mail_username="kanzlei@example.test",
        mail_password="geheim123",
        mail_mailbox="Posteingang",
        mail_use_ssl=True,
        mail_mark_seen=False,
    )
    provider = build_mail_provider(settings)
    assert isinstance(provider, ImapMailProvider)
    assert provider.host == "imap.example.test"
    assert provider.port == 993
    assert provider.username == "kanzlei@example.test"
    assert provider.password == "geheim123"
    assert provider.mailbox == "Posteingang"
    assert provider.use_ssl is True
    assert provider.mark_seen is False
