"""Core mail engine - orchestrates connectors, routing, caching, and webhooks."""

from __future__ import annotations

from typing import Optional

import markdown

from ..cache import MailCache, create_mail_cache
from ..config import get_config
from ..connectors.base import MailConnector
from ..connectors.gmail_connector import GmailConnector
from ..connectors.imap_connector import ImapSmtpConnector
from ..connectors.outlook_connector import OutlookConnector
from ..log import get_logger
from ..models import (
    MailAccount,
    MailListInput,
    MailSearchInput,
    MailSendInput,
    Provider,
    UnifiedMessage,
)
from ..storage.database import Database
from ..storage.token_store import TokenStore
from ..templates import get_template_engine
from ..webhook import WebhookManager

logger = get_logger(__name__)


class MailEngine:
    """
    Central engine that manages connectors and provides unified operations.

    Features:
    - Connection pool management
    - In-memory LRU cache
    - Configurable rate limiting
    - Webhook notifications
    - Template-based email sending
    """

    def __init__(self, db: Database, token_store: TokenStore):
        self.db = db
        self.token_store = token_store
        self._connectors: dict[str, MailConnector] = {}
        self._cache: MailCache = create_mail_cache()
        self._webhook_manager = WebhookManager()
        self._config = get_config()

    async def initialize(self) -> None:
        """Load all accounts and initialize connectors."""
        accounts = self.db.get_accounts()
        for account in accounts:
            await self._init_connector(account)
        logger.info(f"Engine initialized with {len(accounts)} account(s)")

    async def _init_connector(self, account: MailAccount) -> MailConnector:
        """Create and connect a connector for an account."""
        tokens = self.token_store.get(account.id) or {}

        if account.provider == Provider.GMAIL:
            connector = GmailConnector(account, tokens)
        elif account.provider == Provider.OUTLOOK:
            connector = OutlookConnector(account, tokens)
        elif account.provider == Provider.IMAP:
            password = tokens.get("password", "")
            connector = ImapSmtpConnector(account, password)
        else:
            raise ValueError(f"Unknown provider: {account.provider}")

        await connector.connect()
        self._connectors[account.id] = connector
        return connector

    def _get_connector(self, account_id: str) -> MailConnector:
        connector = self._connectors.get(account_id)
        if not connector:
            raise ValueError(f"No connector for account {account_id}")
        return connector

    def _resolve_account(self, email_or_id: Optional[str] = None) -> MailAccount:
        """Resolve account by email, id, or return default."""
        if not email_or_id:
            account = self.db.get_default_account()
            if not account:
                raise ValueError("No accounts configured. Use `unimail add` to add one.")
            return account

        # Try by email
        account = self.db.get_account_by_email(email_or_id)
        if account:
            return account

        # Try by ID
        account = self.db.get_account(email_or_id)
        if account:
            return account

        raise ValueError(f"Account not found: {email_or_id}")

    # === Rate Limiting ===

    def check_rate_limit(self, account_id: str) -> tuple[bool, int, int]:
        """Check if account is within rate limit.

        Returns:
            (is_allowed, current_count, limit)
        """
        config = self._config
        limit = config.rate_limit.default_daily
        current_count = self.db.get_send_count_today(account_id)
        return (current_count < limit, current_count, limit)

    def record_send(self, account_id: str, to_emails: list[str], subject: str) -> None:
        """Record a send operation for rate limiting."""
        self.db.log_send(account_id, to_emails, subject)

    # === Core Operations ===

    async def list_messages(
        self,
        account: Optional[str] = None,
        folder: str = "inbox",
        limit: int = 20,
        unread_only: bool = False,
        since: Optional[str] = None,
    ) -> list[UnifiedMessage]:
        """List messages, optionally from a specific account."""
        # Check cache first
        acct = self._resolve_account(account) if account else None
        acct_id = acct.id if acct else None

        cached = self._cache.get_inbox(acct_id, folder, limit, unread_only)
        if cached is not None:
            return cached

        if account:
            acct = self._resolve_account(account)
            connector = self._get_connector(acct.id)
            messages = await connector.list_messages(folder, limit, unread_only, since)
        else:
            # Aggregate from all accounts
            messages = []
            for acct_id_iter, connector in self._connectors.items():
                try:
                    msgs = await connector.list_messages(folder, limit, unread_only, since)
                    messages.extend(msgs)
                except Exception as e:
                    logger.error(f"Error listing from {acct_id_iter}: {e}")

            # Sort by received time, limit
            messages.sort(key=lambda m: m.received_at, reverse=True)
            messages = messages[:limit]

        # Cache locally
        self.db.cache_messages(messages)
        self._cache.set_inbox(acct_id, folder, limit, unread_only, messages)
        return messages

    async def get_message(self, message_id: str) -> UnifiedMessage:
        """Get full message content."""
        # Check memory cache
        cached_msg = self._cache.get_message(message_id)
        if cached_msg is not None:
            return cached_msg

        # Try DB cache
        cached = self.db.get_message(message_id)
        if cached and cached.get("body_text"):
            msg = self._dict_to_message(cached)
            self._cache.set_message(message_id, msg)
            return msg

        # Parse message_id to find connector
        # Format: {provider}_{account_id}_{external_id} or {provider}_{external_id}
        parts = message_id.split("_", 2)
        if len(parts) < 2:
            raise ValueError(f"Invalid message ID: {message_id}")

        # Find the right connector
        for acct_id, connector in self._connectors.items():
            if acct_id in message_id or connector.account.provider.value in parts[0]:
                external_id = parts[-1]
                msg = await connector.get_message(external_id)
                self.db.cache_message(msg)
                self._cache.set_message(message_id, msg)
                return msg

        raise ValueError(f"Cannot find connector for message: {message_id}")

    async def send_message(
        self,
        to: list[str],
        subject: str,
        body: str,
        from_: Optional[str] = None,
        cc: list[str] | None = None,
        bcc: list[str] | None = None,
        attachments: list[str] | None = None,
        reply_to_id: Optional[str] = None,
        template: Optional[str] = None,
        template_context: Optional[dict] = None,
    ) -> dict:
        """Send a message through the appropriate connector.

        Supports both direct body and template-based rendering.
        """
        # Resolve sender account
        account = self._resolve_account(from_)
        connector = self._get_connector(account.id)

        # Check rate limit
        is_allowed, current_count, limit = self.check_rate_limit(account.id)
        if not is_allowed:
            raise ValueError(
                f"Daily send limit reached ({current_count}/{limit}) for {account.email}"
            )

        # Resolve body content
        if template:
            # Use template engine
            engine = get_template_engine()
            ctx = template_context or {}
            body_html = engine.render(template, **ctx)
            # Use body as plain text fallback
            body_text = body or subject
        else:
            body_text = body
            # Convert markdown to HTML
            body_html = markdown.markdown(body, extensions=["tables", "fenced_code"])

        logger.info(
            f"Sending email: to={to}, subject='{subject[:50]}'",
            extra={"account_id": account.id, "action": "send"},
        )

        # Send
        message_id = await connector.send_message(
            to=to,
            subject=subject,
            body_text=body_text,
            body_html=body_html,
            cc=cc,
            bcc=bcc,
            attachments=attachments,
            reply_to_id=reply_to_id,
        )

        # Record send for rate limiting
        self.record_send(account.id, to, subject)

        # Invalidate inbox cache (new sent mail)
        self._cache.invalidate(account.id)

        return {
            "message_id": message_id,
            "from": account.email,
            "to": to,
            "subject": subject,
        }

    async def reply_message(
        self,
        message_id: str,
        body: str,
        reply_all: bool = False,
    ) -> dict:
        """Reply to a message using the original account."""
        # Get original message to determine account
        msg = await self.get_message(message_id)
        account = self.db.get_account(msg.account_id)
        if not account:
            raise ValueError(f"Account not found for message: {message_id}")

        connector = self._get_connector(account.id)

        # Check rate limit
        is_allowed, current_count, limit = self.check_rate_limit(account.id)
        if not is_allowed:
            raise ValueError(
                f"Daily send limit reached ({current_count}/{limit}) for {account.email}"
            )

        # Determine recipients
        to = [msg.from_contact.email]
        cc = None
        if reply_all:
            cc = [c.email for c in msg.to + msg.cc if c.email != account.email]

        body_html = markdown.markdown(body)

        logger.info(
            f"Replying to message {message_id}: to={to}",
            extra={"account_id": account.id, "action": "reply"},
        )

        result_id = await connector.send_message(
            to=to,
            subject=f"Re: {msg.subject}" if not msg.subject.startswith("Re:") else msg.subject,
            body_text=body,
            body_html=body_html,
            cc=cc,
            reply_to_id=msg.external_id,
        )

        self.record_send(account.id, to, msg.subject)

        return {"message_id": result_id, "from": account.email, "to": to}

    async def search_messages(
        self,
        query: str,
        account: Optional[str] = None,
        from_filter: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        limit: int = 10,
    ) -> list[UnifiedMessage]:
        """Search messages across accounts."""
        # Try local FTS first
        local_results = self.db.search_messages(query, limit)
        if local_results:
            return [self._dict_to_message(r) for r in local_results]

        # Fall through to remote search
        if account:
            acct = self._resolve_account(account)
            connector = self._get_connector(acct.id)
            return await connector.search(query, from_filter, date_from, date_to, limit)
        else:
            results = []
            for connector in self._connectors.values():
                try:
                    msgs = await connector.search(query, from_filter, date_from, date_to, limit)
                    results.extend(msgs)
                except Exception:
                    continue
            results.sort(key=lambda m: m.received_at, reverse=True)
            return results[:limit]

    async def mark_read(self, message_id: str) -> None:
        msg = await self.get_message(message_id)
        connector = self._get_connector(msg.account_id)
        await connector.mark_read(msg.external_id)
        self.db.mark_read(message_id)

    async def archive_messages(self, message_ids: list[str]) -> None:
        for mid in message_ids:
            msg = await self.get_message(mid)
            connector = self._get_connector(msg.account_id)
            await connector.archive(msg.external_id)

    async def trash_messages(self, message_ids: list[str]) -> None:
        for mid in message_ids:
            msg = await self.get_message(mid)
            connector = self._get_connector(msg.account_id)
            await connector.trash(msg.external_id)

    async def download_attachment(
        self, message_id: str, attachment_id: str, save_path: Optional[str] = None
    ) -> str:
        """Download attachment and save to disk."""
        msg = await self.get_message(message_id)
        connector = self._get_connector(msg.account_id)
        content, filename = await connector.download_attachment(msg.external_id, attachment_id)

        if save_path:
            out_path = save_path
        else:
            out_path = f"/tmp/{filename}"

        from pathlib import Path
        Path(out_path).write_bytes(content)
        return out_path

    async def sync_all(self) -> int:
        """Sync all accounts incrementally. Returns number of new messages."""
        total_new = 0
        all_new_messages: list[UnifiedMessage] = []

        for acct_id, connector in self._connectors.items():
            try:
                new_msgs = await connector.sync_incremental()
                if new_msgs:
                    self.db.cache_messages(new_msgs)
                    total_new += len(new_msgs)
                    all_new_messages.extend(new_msgs)
                    # Invalidate cache for this account
                    self._cache.invalidate(acct_id)
                    # Update sync state
                    account = self.db.get_account(acct_id)
                    if account:
                        self.db.update_sync_state(acct_id, connector.account.sync_state)
            except Exception as e:
                logger.error(f"Sync error for {acct_id}: {e}")

        # Notify webhooks about new messages
        if all_new_messages:
            logger.info(f"Synced {total_new} new message(s), notifying webhooks")
            await self._webhook_manager.notify_new_messages(all_new_messages)

        return total_new

    @property
    def webhook_manager(self) -> WebhookManager:
        """Access the webhook manager for registration/management."""
        return self._webhook_manager

    async def shutdown(self) -> None:
        """Disconnect all connectors."""
        logger.info("Shutting down engine")
        for connector in self._connectors.values():
            try:
                await connector.disconnect()
            except Exception:
                pass
        self._connectors.clear()
        self._cache.invalidate_all()

    # === Helpers ===

    def _dict_to_message(self, data: dict) -> UnifiedMessage:
        """Convert DB row dict back to UnifiedMessage."""
        import json
        from datetime import datetime
        from ..models import Contact, Attachment

        return UnifiedMessage(
            id=data["id"],
            account_id=data["account_id"],
            external_id=data["external_id"],
            thread_id=data.get("thread_id"),
            folder=data.get("folder", "inbox"),
            from_=Contact(name=data.get("from_name"), email=data.get("from_email", "")),
            to=([Contact(**c) for c in json.loads(data.get("to_json", "[]"))] if data.get("to_json") else []),
            cc=([Contact(**c) for c in json.loads(data.get("cc_json", "[]"))] if data.get("cc_json") else []),
            subject=data.get("subject", ""),
            snippet=data.get("snippet", ""),
            body_text=data.get("body_text", ""),
            body_html=data.get("body_html"),
            attachments=([Attachment(**a) for a in json.loads(data.get("attachments_json", "[]"))] if data.get("attachments_json") else []),
            received_at=datetime.fromisoformat(data["received_at"]),
            is_read=bool(data.get("is_read", 0)),
            is_starred=bool(data.get("is_starred", 0)),
            labels=json.loads(data.get("labels_json", "[]")) if data.get("labels_json") else [],
        )
