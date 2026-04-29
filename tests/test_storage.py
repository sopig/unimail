"""Tests for database storage layer."""

from datetime import datetime

import pytest

from src.models import (
    Contact,
    ImapConfig,
    MailAccount,
    Provider,
    SyncState,
    UnifiedMessage,
)
from src.storage.database import Database


class TestDatabase:
    def test_init_creates_schema(self, db: Database):
        """Database should create tables on init."""
        cursor = db.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
        tables = {row[0] for row in cursor.fetchall()}
        assert "accounts" in tables
        assert "messages" in tables
        assert "send_log" in tables

    def test_save_and_get_account(self, db: Database, sample_account):
        db.save_account(sample_account)
        retrieved = db.get_account(sample_account.id)
        assert retrieved is not None
        assert retrieved.email == sample_account.email
        assert retrieved.provider == Provider.IMAP

    def test_get_account_by_email(self, db: Database, sample_account):
        db.save_account(sample_account)
        retrieved = db.get_account_by_email("test@example.com")
        assert retrieved is not None
        assert retrieved.id == sample_account.id

    def test_get_default_account(self, db: Database, sample_account):
        db.save_account(sample_account)
        default = db.get_default_account()
        assert default is not None
        assert default.is_default is True

    def test_delete_account(self, db: Database, sample_account):
        db.save_account(sample_account)
        db.delete_account(sample_account.id)
        assert db.get_account(sample_account.id) is None

    def test_cache_message(self, db: Database, sample_account, sample_message):
        db.save_account(sample_account)
        db.cache_message(sample_message)
        retrieved = db.get_message(sample_message.id)
        assert retrieved is not None
        assert retrieved["subject"] == "Test Subject"
        assert retrieved["from_email"] == "sender@example.com"

    def test_cache_messages_batch(self, db: Database, sample_account, sample_message):
        db.save_account(sample_account)
        db.cache_messages([sample_message])
        messages = db.get_messages(folder="inbox", limit=10)
        assert len(messages) == 1

    def test_search_messages_fts(self, db: Database, sample_account, sample_message):
        db.save_account(sample_account)
        db.cache_message(sample_message)
        results = db.search_messages("test email", limit=5)
        assert len(results) >= 1
        assert results[0]["subject"] == "Test Subject"

    def test_mark_read(self, db: Database, sample_account, sample_message):
        db.save_account(sample_account)
        db.cache_message(sample_message)
        db.mark_read(sample_message.id)
        msg = db.get_message(sample_message.id)
        assert msg["is_read"] == 1

    def test_send_log(self, db: Database):
        db.log_send("acc1", ["user@example.com"], "Test Subject")
        count = db.get_send_count_today("acc1")
        assert count == 1

    def test_send_count_multiple(self, db: Database):
        for i in range(5):
            db.log_send("acc1", [f"user{i}@example.com"], f"Subject {i}")
        assert db.get_send_count_today("acc1") == 5

    def test_get_messages_unread_only(self, db: Database, sample_account, sample_message):
        db.save_account(sample_account)
        db.cache_message(sample_message)
        messages = db.get_messages(folder="inbox", unread_only=True)
        assert len(messages) == 1  # sample_message is unread

        db.mark_read(sample_message.id)
        messages = db.get_messages(folder="inbox", unread_only=True)
        assert len(messages) == 0

    def test_update_sync_state(self, db: Database, sample_account):
        db.save_account(sample_account)
        new_state = SyncState(imap_last_uid=42)
        db.update_sync_state(sample_account.id, new_state)

        account = db.get_account(sample_account.id)
        assert account.sync_state.imap_last_uid == 42
