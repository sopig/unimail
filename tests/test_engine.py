"""Tests for MailEngine business logic."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from src.engine.mail_engine import MailEngine
from src.models import Contact, MailAccount, Provider, UnifiedMessage


class TestMailEngine:
    @pytest.mark.asyncio
    async def test_list_messages_aggregates(self, engine, mock_connector, sample_account, db):
        """Engine should aggregate messages from all connectors."""
        db.save_account(sample_account)
        mock_connector.account = sample_account
        engine._connectors["test-account-1"] = mock_connector

        msg = UnifiedMessage(
            id="imap_test-account-1_1",
            account_id="test-account-1",
            external_id="1",
            folder="inbox",
            from_=Contact(email="sender@example.com"),
            to=[Contact(email="test@example.com")],
            subject="Hello",
            snippet="Hi there",
            body_text="Hi there body",
            received_at=datetime(2024, 1, 1),
            is_read=False,
            is_starred=False,
        )
        mock_connector.list_messages.return_value = [msg]

        messages = await engine.list_messages(limit=10)
        assert len(messages) == 1
        assert messages[0].subject == "Hello"

    @pytest.mark.asyncio
    async def test_send_message_checks_rate_limit(self, engine, mock_connector, sample_account, db, token_store):
        """Engine should enforce rate limits."""
        db.save_account(sample_account)
        token_store.save(sample_account.id, {"password": "test"})
        mock_connector.account = sample_account
        engine._connectors["test-account-1"] = mock_connector

        # Fill up rate limit
        for i in range(50):
            db.log_send("test-account-1", [f"user{i}@example.com"], f"Subject {i}")

        with pytest.raises(ValueError, match="Daily send limit"):
            await engine.send_message(
                to=["user@example.com"],
                subject="Test",
                body="Hello",
            )

    @pytest.mark.asyncio
    async def test_send_message_success(self, engine, mock_connector, sample_account, db, token_store):
        """Engine should send message and log it."""
        db.save_account(sample_account)
        token_store.save(sample_account.id, {"password": "test"})
        mock_connector.account = sample_account
        engine._connectors["test-account-1"] = mock_connector

        result = await engine.send_message(
            to=["recipient@example.com"],
            subject="Test Send",
            body="Hello world",
        )

        assert result["from"] == "test@example.com"
        assert result["to"] == ["recipient@example.com"]
        mock_connector.send_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_with_template(self, engine, mock_connector, sample_account, db, token_store):
        """Engine should render template when specified."""
        db.save_account(sample_account)
        token_store.save(sample_account.id, {"password": "test"})
        mock_connector.account = sample_account
        engine._connectors["test-account-1"] = mock_connector

        result = await engine.send_message(
            to=["recipient@example.com"],
            subject="Welcome",
            body="fallback text",
            template="welcome.html",
            template_context={"name": "Alice"},
        )

        # Verify the connector received HTML from template
        call_args = mock_connector.send_message.call_args
        assert "Alice" in call_args.kwargs.get("body_html", "") or "Alice" in str(call_args)

    @pytest.mark.asyncio
    async def test_check_rate_limit(self, engine, db, sample_account):
        """Rate limit check should return correct values."""
        db.save_account(sample_account)

        is_allowed, count, limit = engine.check_rate_limit("test-account-1")
        assert is_allowed is True
        assert count == 0
        assert limit == 50

    @pytest.mark.asyncio
    async def test_cache_integration(self, engine, mock_connector, sample_account, db):
        """Engine should use cache for repeated calls."""
        db.save_account(sample_account)
        mock_connector.account = sample_account
        engine._connectors["test-account-1"] = mock_connector
        mock_connector.list_messages.return_value = []

        # First call hits connector
        await engine.list_messages(account="test@example.com", folder="inbox", limit=20)
        assert mock_connector.list_messages.call_count == 1

        # Second call should use cache
        await engine.list_messages(account="test@example.com", folder="inbox", limit=20)
        # Still 1 because cache returned result
        assert mock_connector.list_messages.call_count == 1

    @pytest.mark.asyncio
    async def test_sync_notifies_webhooks(self, engine, mock_connector, sample_account, db):
        """Sync should notify webhooks about new messages."""
        db.save_account(sample_account)
        mock_connector.account = sample_account
        engine._connectors["test-account-1"] = mock_connector

        msg = UnifiedMessage(
            id="imap_test-account-1_999",
            account_id="test-account-1",
            external_id="999",
            folder="inbox",
            from_=Contact(email="new@example.com"),
            to=[Contact(email="test@example.com")],
            subject="New Message",
            snippet="Something new",
            body_text="New body",
            received_at=datetime(2024, 6, 1),
            is_read=False,
            is_starred=False,
        )
        mock_connector.sync_incremental.return_value = [msg]

        with patch.object(engine._webhook_manager, "notify_new_messages", new_callable=AsyncMock) as mock_notify:
            count = await engine.sync_all()
            assert count == 1
            mock_notify.assert_called_once()

    @pytest.mark.asyncio
    async def test_shutdown_clears_state(self, engine, mock_connector, sample_account):
        """Shutdown should disconnect connectors and clear cache."""
        mock_connector.account = sample_account
        engine._connectors["test-account-1"] = mock_connector

        await engine.shutdown()
        assert len(engine._connectors) == 0
        mock_connector.disconnect.assert_called_once()
