"""Shared test fixtures for UniMail."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import AsyncGenerator, Generator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

# Set test environment before importing modules
os.environ.setdefault("UNIMAIL_LOG_LEVEL", "DEBUG")
os.environ.setdefault("UNIMAIL_LOG_FORMAT", "console")


@pytest.fixture
def tmp_dir() -> Generator[Path, None, None]:
    """Create a temporary directory for test data."""
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


@pytest.fixture
def db(tmp_dir: Path):
    """Create a test database."""
    from src.storage.database import Database
    return Database(tmp_dir / "test.db")


@pytest.fixture
def token_store(tmp_dir: Path):
    """Create a test token store."""
    from src.storage.token_store import TokenStore
    return TokenStore(tmp_dir / "tokens.enc", "test-passphrase")


@pytest.fixture
def mock_connector():
    """Create a mock mail connector."""
    from src.connectors.base import MailConnector
    connector = AsyncMock(spec=MailConnector)
    connector.connect = AsyncMock()
    connector.disconnect = AsyncMock()
    connector.list_messages = AsyncMock(return_value=[])
    connector.get_message = AsyncMock()
    connector.send_message = AsyncMock(return_value="mock_msg_id")
    connector.mark_read = AsyncMock()
    connector.archive = AsyncMock()
    connector.trash = AsyncMock()
    connector.search = AsyncMock(return_value=[])
    connector.download_attachment = AsyncMock(return_value=(b"content", "file.txt"))
    connector.sync_incremental = AsyncMock(return_value=[])
    return connector


@pytest_asyncio.fixture
async def engine(db, token_store, mock_connector):
    """Create a test MailEngine with mocked connectors."""
    from src.engine.mail_engine import MailEngine
    eng = MailEngine(db, token_store)
    # Don't call initialize (no real accounts)
    yield eng
    await eng.shutdown()


@pytest.fixture
def sample_account():
    """Create a sample MailAccount for testing."""
    from src.models import ImapConfig, MailAccount, Provider, SyncState
    return MailAccount(
        id="test-account-1",
        provider=Provider.IMAP,
        email="test@example.com",
        display_name="Test User",
        is_default=True,
        config=ImapConfig(
            imap_host="imap.example.com",
            imap_port=993,
            smtp_host="smtp.example.com",
            smtp_port=465,
            username="test@example.com",
            tls=True,
        ),
        sync_state=SyncState(),
    )


@pytest.fixture
def sample_message():
    """Create a sample UnifiedMessage for testing."""
    from datetime import datetime
    from src.models import Contact, UnifiedMessage
    return UnifiedMessage(
        id="imap_test-account-1_123",
        account_id="test-account-1",
        external_id="123",
        thread_id=None,
        folder="inbox",
        from_=Contact(name="Sender", email="sender@example.com"),
        to=[Contact(name="Test", email="test@example.com")],
        cc=[],
        subject="Test Subject",
        snippet="This is a test email...",
        body_text="This is a test email body.",
        body_html="<p>This is a test email body.</p>",
        attachments=[],
        received_at=datetime(2024, 1, 15, 10, 30, 0),
        is_read=False,
        is_starred=False,
        labels=[],
    )


@pytest.fixture
def api_client(tmp_dir: Path):
    """Create a test HTTP client for the FastAPI app."""
    from httpx import AsyncClient, ASGITransport
    from src.api import create_app

    # Patch data dir
    with patch("src.api.Path.home", return_value=tmp_dir):
        app = create_app("test-passphrase")
        transport = ASGITransport(app=app)
        client = AsyncClient(transport=transport, base_url="http://test")
        return client
