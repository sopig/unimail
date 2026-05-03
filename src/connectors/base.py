"""Abstract base class for mail connectors."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from ..models import Attachment, MailAccount, UnifiedMessage


class MailConnector(ABC):
    """Abstract interface that all mail connectors must implement."""

    def __init__(self, account: MailAccount, token_store=None):
        self.account = account
        self._token_store = token_store

    def _persist_tokens(self) -> None:
        """Persist current tokens to TokenStore if available."""
        if self._token_store and hasattr(self, '_tokens') and self._tokens:
            self._token_store.save(self.account.id, self._tokens)

    @abstractmethod
    async def connect(self) -> None:
        """Establish connection to the mail server."""
        ...

    @abstractmethod
    async def disconnect(self) -> None:
        """Close connection."""
        ...

    @abstractmethod
    async def list_messages(
        self,
        folder: str = "inbox",
        limit: int = 20,
        unread_only: bool = False,
        since: Optional[str] = None,
    ) -> list[UnifiedMessage]:
        """List messages from a folder."""
        ...

    @abstractmethod
    async def get_message(self, external_id: str) -> UnifiedMessage:
        """Get full message content by external ID."""
        ...

    @abstractmethod
    async def send_message(
        self,
        to: list[str],
        subject: str,
        body_text: str,
        body_html: Optional[str] = None,
        cc: list[str] | None = None,
        bcc: list[str] | None = None,
        attachments: list[str] | None = None,
        reply_to_id: Optional[str] = None,
    ) -> str:
        """Send a message. Returns message ID."""
        ...

    @abstractmethod
    async def mark_read(self, external_id: str) -> None:
        """Mark a message as read."""
        ...

    @abstractmethod
    async def mark_unread(self, external_id: str) -> None:
        """Mark a message as unread."""
        ...

    @abstractmethod
    async def archive(self, external_id: str) -> None:
        """Archive a message."""
        ...

    @abstractmethod
    async def trash(self, external_id: str) -> None:
        """Move message to trash."""
        ...

    @abstractmethod
    async def search(
        self,
        query: str,
        from_filter: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        limit: int = 10,
    ) -> list[UnifiedMessage]:
        """Search messages."""
        ...

    @abstractmethod
    async def download_attachment(
        self, message_id: str, attachment_id: str
    ) -> tuple[bytes, str]:
        """Download attachment. Returns (content_bytes, filename)."""
        ...

    @abstractmethod
    async def sync_incremental(self) -> list[UnifiedMessage]:
        """Fetch new messages since last sync. Updates sync state."""
        ...
