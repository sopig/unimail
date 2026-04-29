"""Tests for UniMail data models."""

from datetime import datetime

import pytest

from src.models import (
    Attachment,
    Contact,
    GmailConfig,
    IMAP_PRESETS,
    ImapConfig,
    MailAccount,
    MailListInput,
    MailSearchInput,
    MailSendInput,
    OutlookConfig,
    Provider,
    RateLimitConfig,
    SyncState,
    UnifiedMessage,
    detect_preset,
)


class TestContact:
    def test_create_with_name(self):
        c = Contact(name="Alice", email="alice@example.com")
        assert c.name == "Alice"
        assert c.email == "alice@example.com"

    def test_create_without_name(self):
        c = Contact(email="bob@example.com")
        assert c.name is None
        assert c.email == "bob@example.com"


class TestUnifiedMessage:
    def test_create_message(self, sample_message):
        assert sample_message.id == "imap_test-account-1_123"
        assert sample_message.subject == "Test Subject"
        assert sample_message.is_read is False

    def test_message_serialization(self, sample_message):
        data = sample_message.model_dump(by_alias=True)
        assert "from_" in data
        assert data["from_"]["email"] == "sender@example.com"
        assert data["subject"] == "Test Subject"


class TestMailAccount:
    def test_imap_account(self, sample_account):
        assert sample_account.provider == Provider.IMAP
        assert sample_account.email == "test@example.com"
        assert sample_account.is_default is True
        assert isinstance(sample_account.config, ImapConfig)

    def test_gmail_config(self):
        config = GmailConfig(
            client_id="test-id",
            client_secret="test-secret",
        )
        assert config.redirect_uri == "http://localhost:9876/callback"

    def test_outlook_config(self):
        config = OutlookConfig(
            client_id="test-id",
            client_secret="test-secret",
        )
        assert config.tenant_id == "common"


class TestRateLimitConfig:
    def test_defaults(self):
        config = RateLimitConfig()
        assert config.default_daily == 50
        assert config.account_overrides == {}

    def test_custom_values(self):
        config = RateLimitConfig(default_daily=100, account_overrides={"acc1": 200})
        assert config.default_daily == 100
        assert config.account_overrides["acc1"] == 200


class TestMailSendInput:
    def test_basic_send(self):
        msg = MailSendInput(
            to=["user@example.com"],
            subject="Test",
            body="Hello",
        )
        assert msg.to == ["user@example.com"]
        assert msg.template is None

    def test_template_send(self):
        msg = MailSendInput(
            to=["user@example.com"],
            subject="Welcome",
            body="",
            template="welcome.html",
            template_context={"name": "Alice"},
        )
        assert msg.template == "welcome.html"
        assert msg.template_context["name"] == "Alice"


class TestPresetDetection:
    def test_163(self):
        assert detect_preset("user@163.com") == "163"

    def test_qq(self):
        assert detect_preset("12345@qq.com") == "qq"

    def test_outlook(self):
        assert detect_preset("user@outlook.com") == "outlook"
        assert detect_preset("user@hotmail.com") == "outlook"

    def test_unknown(self):
        assert detect_preset("user@custom.org") is None


class TestImapPresets:
    def test_presets_exist(self):
        assert "163" in IMAP_PRESETS
        assert "qq" in IMAP_PRESETS
        assert "outlook" in IMAP_PRESETS

    def test_preset_has_required_fields(self):
        for name, preset in IMAP_PRESETS.items():
            assert "imap_host" in preset
            assert "smtp_host" in preset
            assert "imap_port" in preset
            assert "smtp_port" in preset
