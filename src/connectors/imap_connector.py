"""Generic IMAP/SMTP connector for 163, QQ, and other standard mail providers."""

from __future__ import annotations

import asyncio
import email
import email.utils
import email.policy
import time
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from pathlib import Path
from typing import Optional
import uuid

import aiosmtplib
from aioimaplib import aioimaplib

from .base import MailConnector
from ..config import get_config
from ..log import get_logger
from ..models import (
    Attachment,
    Contact,
    ImapConfig,
    MailAccount,
    SyncState,
    UnifiedMessage,
)

logger = get_logger(__name__)


class ImapSmtpConnector(MailConnector):
    """
    Connector for any IMAP/SMTP-compatible mail server.
    Works with 163, QQ, 126, Yahoo, and generic providers.

    Supports connection pooling with keep-alive and automatic reconnection.
    """

    def __init__(self, account: MailAccount, password: str):
        super().__init__(account)
        self.config: ImapConfig = account.config  # type: ignore
        self.password = password
        self._connection: Optional[aioimaplib.IMAP4_SSL] = None
        self._last_activity: float = 0.0
        self._connection_timeout: int = get_config().imap.connection_timeout

    @property
    def is_connected(self) -> bool:
        """Check if the IMAP connection is alive and not timed out."""
        if self._connection is None:
            return False
        # Check if connection has timed out due to inactivity
        if self._last_activity > 0:
            elapsed = time.time() - self._last_activity
            if elapsed > self._connection_timeout:
                logger.debug(
                    f"Connection timed out ({elapsed:.0f}s > {self._connection_timeout}s)",
                    extra={"account_id": self.account.id},
                )
                return False
        return True

    @property
    def imap(self) -> Optional[aioimaplib.IMAP4_SSL]:
        """Legacy accessor for backward compatibility."""
        return self._connection

    async def connect(self) -> None:
        """Connect to IMAP server, reusing existing connection if alive."""
        if self.is_connected:
            logger.debug(
                "Reusing existing IMAP connection",
                extra={"account_id": self.account.id},
            )
            self._last_activity = time.time()
            return

        # Close stale connection if any
        if self._connection is not None:
            logger.debug(
                "Closing stale connection before reconnect",
                extra={"account_id": self.account.id},
            )
            try:
                await self._connection.logout()
            except Exception:
                pass
            self._connection = None

        logger.info(
            f"Connecting to IMAP server {self.config.imap_host}:{self.config.imap_port}",
            extra={"account_id": self.account.id, "connector": "imap"},
        )

        self._connection = aioimaplib.IMAP4_SSL(
            host=self.config.imap_host,
            port=self.config.imap_port,
        )
        await self._connection.wait_hello_from_server()
        await self._connection.login(self.config.username, self.password)
        await self._connection.select("INBOX")
        self._last_activity = time.time()

        logger.info(
            f"Connected to {self.config.imap_host}",
            extra={"account_id": self.account.id, "connector": "imap"},
        )

    async def _ensure_connected(self) -> None:
        """Ensure connection is alive, reconnecting if necessary."""
        if not self.is_connected:
            await self.connect()
        self._last_activity = time.time()

    async def disconnect(self) -> None:
        """Disconnect from IMAP."""
        if self._connection:
            logger.info(
                "Disconnecting from IMAP",
                extra={"account_id": self.account.id, "connector": "imap"},
            )
            try:
                await self._connection.logout()
            except Exception:
                pass
            self._connection = None
            self._last_activity = 0.0

    async def list_messages(
        self,
        folder: str = "inbox",
        limit: int = 20,
        unread_only: bool = False,
        since: Optional[str] = None,
    ) -> list[UnifiedMessage]:
        """List messages via IMAP SEARCH + FETCH."""
        await self._ensure_connected()
        assert self._connection is not None

        # Select folder
        folder_name = self._map_folder(folder)
        await self._connection.select(folder_name)

        # Build search criteria
        criteria = "ALL"
        if unread_only:
            criteria = "UNSEEN"
        if since:
            # IMAP date format: DD-Mon-YYYY
            dt = datetime.fromisoformat(since)
            criteria += f' SINCE {dt.strftime("%d-%b-%Y")}'

        # Search
        status, data = await self._connection.search(criteria)
        if status != "OK":
            return []

        # Get sequence numbers (most recent first)
        seq_nums = data[0].split()
        seq_nums = list(reversed(seq_nums))[:limit]

        if not seq_nums:
            return []

        # Fetch each message by RFC822 and parse with email module
        messages = []
        for seq in seq_nums:
            seq_str = seq.decode() if isinstance(seq, bytes) else seq
            try:
                msg = await self.get_message(seq_str)
                messages.append(msg)
            except Exception:
                continue

        self._last_activity = time.time()
        return messages

    async def get_message(self, external_id: str) -> UnifiedMessage:
        """Fetch full message by UID."""
        await self._ensure_connected()
        assert self._connection is not None

        status, data = await self._connection.fetch(external_id, "(RFC822 FLAGS)")
        if status != "OK" or not data:
            raise ValueError(f"Message {external_id} not found")

        # Parse raw email — aioimaplib returns:
        # [0] response line (bytes), [1] RFC822 body (bytearray), [2] ")" , [3] completion msg
        raw_email = None
        for item in data:
            if isinstance(item, (bytes, bytearray)) and not isinstance(item, bytearray) == False:
                # Skip small non-body items (response lines, closing parens)
                if isinstance(item, bytearray) or (isinstance(item, bytes) and b"FETCH" not in item and len(item) > 100):
                    raw_email = bytes(item) if isinstance(item, bytearray) else item
                    break
        if raw_email is None:
            raise ValueError(f"Message {external_id} not found: no RFC822 data")

        msg = email.message_from_bytes(raw_email, policy=email.policy.default)

        self._last_activity = time.time()
        return self._parse_full_email(msg, external_id)

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
        """Send email via SMTP."""
        msg = MIMEMultipart("alternative")
        msg["From"] = self.account.email
        msg["To"] = ", ".join(to)
        if cc:
            msg["Cc"] = ", ".join(cc)
        msg["Subject"] = subject
        msg["Message-ID"] = f"<{uuid.uuid4()}@unimail>"
        msg["Date"] = email.utils.formatdate(localtime=True)

        # Body
        msg.attach(MIMEText(body_text, "plain", "utf-8"))
        if body_html:
            msg.attach(MIMEText(body_html, "html", "utf-8"))

        # Attachments
        if attachments:
            # Convert to multipart/mixed
            mixed = MIMEMultipart("mixed")
            mixed.attach(msg)
            for file_path in attachments:
                p = Path(file_path)
                if p.exists():
                    part = MIMEBase("application", "octet-stream")
                    part.set_payload(p.read_bytes())
                    encoders.encode_base64(part)
                    part.add_header(
                        "Content-Disposition", f"attachment; filename={p.name}"
                    )
                    mixed.attach(part)
            msg = mixed

        # All recipients
        all_recipients = list(to)
        if cc:
            all_recipients.extend(cc)
        if bcc:
            all_recipients.extend(bcc)

        # Send via SMTP
        logger.info(
            f"Sending email to {to} via SMTP {self.config.smtp_host}",
            extra={"account_id": self.account.id, "action": "send"},
        )

        await aiosmtplib.send(
            msg,
            hostname=self.config.smtp_host,
            port=self.config.smtp_port,
            username=self.config.username,
            password=self.password,
            use_tls=self.config.smtp_port == 465,
            start_tls=self.config.smtp_port == 587,
        )

        return msg["Message-ID"]

    async def mark_read(self, external_id: str) -> None:
        await self._ensure_connected()
        assert self._connection is not None
        await self._connection.store(external_id, "+FLAGS", r"(\Seen)")
        self._last_activity = time.time()

    async def mark_unread(self, external_id: str) -> None:
        await self._ensure_connected()
        assert self._connection is not None
        await self._connection.store(external_id, "-FLAGS", r"(\Seen)")
        self._last_activity = time.time()

    async def archive(self, external_id: str) -> None:
        """Move to Archive folder (or All Mail for Gmail-like)."""
        await self._ensure_connected()
        assert self._connection is not None
        # Try common archive folder names
        for folder in ["Archive", "All Mail", "&Xstrg1LZFw-"]:  # 163 uses special encoding
            try:
                await self._connection.copy(external_id, folder)
                await self._connection.store(external_id, "+FLAGS", r"(\Deleted)")
                await self._connection.expunge()
                self._last_activity = time.time()
                return
            except Exception:
                continue
        # Fallback: just remove from inbox (mark deleted)
        await self._connection.store(external_id, "+FLAGS", r"(\Deleted)")
        self._last_activity = time.time()

    async def trash(self, external_id: str) -> None:
        await self._ensure_connected()
        assert self._connection is not None
        for folder in ["Trash", "&XfJT0ZAB-", "Deleted Messages"]:
            try:
                await self._connection.copy(external_id, folder)
                await self._connection.store(external_id, "+FLAGS", r"(\Deleted)")
                await self._connection.expunge()
                self._last_activity = time.time()
                return
            except Exception:
                continue

    async def search(
        self,
        query: str,
        from_filter: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        limit: int = 10,
    ) -> list[UnifiedMessage]:
        """Search via IMAP SEARCH command."""
        await self._ensure_connected()
        assert self._connection is not None

        criteria_parts = []
        if query:
            criteria_parts.append(f'BODY "{query}"')
        if from_filter:
            criteria_parts.append(f'FROM "{from_filter}"')
        if date_from:
            dt = datetime.fromisoformat(date_from)
            criteria_parts.append(f'SINCE {dt.strftime("%d-%b-%Y")}')
        if date_to:
            dt = datetime.fromisoformat(date_to)
            criteria_parts.append(f'BEFORE {dt.strftime("%d-%b-%Y")}')

        criteria = " ".join(criteria_parts) if criteria_parts else "ALL"

        status, data = await self._connection.search(criteria)
        if status != "OK":
            return []

        uids = data[0].split()
        uids = list(reversed(uids))[:limit]

        messages = []
        for uid in uids:
            try:
                msg = await self.get_message(uid.decode() if isinstance(uid, bytes) else uid)
                messages.append(msg)
            except Exception:
                continue

        self._last_activity = time.time()
        return messages

    async def download_attachment(
        self, message_id: str, attachment_id: str
    ) -> tuple[bytes, str]:
        """Download attachment by fetching the full message and extracting the part."""
        await self._ensure_connected()
        assert self._connection is not None

        status, data = await self._connection.fetch(message_id, "(RFC822)")
        if status != "OK":
            raise ValueError(f"Message {message_id} not found")

        raw = data[0][1] if isinstance(data[0], tuple) else data[0]
        msg = email.message_from_bytes(raw, policy=email.policy.default)

        # Walk parts to find attachment
        part_idx = 0
        for part in msg.walk():
            if part.get_content_disposition() == "attachment":
                if str(part_idx) == attachment_id or part.get_filename() == attachment_id:
                    content = part.get_payload(decode=True)
                    filename = part.get_filename() or f"attachment_{part_idx}"
                    return content, filename
                part_idx += 1

        raise ValueError(f"Attachment {attachment_id} not found in message {message_id}")

    async def sync_incremental(self) -> list[UnifiedMessage]:
        """Fetch messages newer than last known UID."""
        await self._ensure_connected()
        assert self._connection is not None

        last_uid = self.account.sync_state.imap_last_uid or 0

        # Search for UIDs > last_uid
        status, data = await self._connection.search(f"UID {last_uid + 1}:*")
        if status != "OK":
            return []

        uids = data[0].split()
        if not uids:
            return []

        messages = []
        for uid in uids:
            uid_str = uid.decode() if isinstance(uid, bytes) else uid
            uid_int = int(uid_str)
            if uid_int <= last_uid:
                continue
            try:
                msg = await self.get_message(uid_str)
                messages.append(msg)
                # Update last UID
                if uid_int > (self.account.sync_state.imap_last_uid or 0):
                    self.account.sync_state.imap_last_uid = uid_int
            except Exception:
                continue

        self._last_activity = time.time()
        return messages

    # === Helpers ===

    def _map_folder(self, folder: str) -> str:
        """Map unified folder names to IMAP folder names."""
        mapping = {
            "inbox": "INBOX",
            "sent": "Sent",
            "drafts": "Drafts",
            "trash": "Trash",
            "archive": "Archive",
            "spam": "Spam",
        }
        return mapping.get(folder, folder)

    def _parse_full_email(
        self, msg: email.message.Message, uid: str
    ) -> UnifiedMessage:
        """Parse a full email.message.Message into UnifiedMessage."""
        # From
        from_header = msg.get("From", "")
        from_contact = self._parse_contact(from_header)

        # To, Cc
        to_contacts = self._parse_contacts(msg.get("To", ""))
        cc_contacts = self._parse_contacts(msg.get("Cc", ""))

        # Body
        body_text = ""
        body_html = None
        attachments = []
        att_idx = 0

        if msg.is_multipart():
            for part in msg.walk():
                content_type = part.get_content_type()
                disposition = part.get_content_disposition()

                if disposition == "attachment":
                    attachments.append(
                        Attachment(
                            id=str(att_idx),
                            filename=part.get_filename() or f"attachment_{att_idx}",
                            mime_type=content_type,
                            size=len(part.get_payload(decode=True) or b""),
                        )
                    )
                    att_idx += 1
                elif content_type == "text/plain" and not body_text:
                    payload = part.get_payload(decode=True)
                    if payload:
                        body_text = payload.decode("utf-8", errors="replace")
                elif content_type == "text/html" and not body_html:
                    payload = part.get_payload(decode=True)
                    if payload:
                        body_html = payload.decode("utf-8", errors="replace")
        else:
            payload = msg.get_payload(decode=True)
            if payload:
                if msg.get_content_type() == "text/html":
                    body_html = payload.decode("utf-8", errors="replace")
                else:
                    body_text = payload.decode("utf-8", errors="replace")

        # Date
        date_str = msg.get("Date", "")
        try:
            received_at = email.utils.parsedate_to_datetime(date_str)
        except Exception:
            received_at = datetime.now()

        # Subject
        subject = msg.get("Subject", "") or ""

        return UnifiedMessage(
            id=f"imap_{self.account.id}_{uid}",
            account_id=self.account.id,
            external_id=uid,
            thread_id=msg.get("In-Reply-To"),
            folder="inbox",
            from_=from_contact,
            to=to_contacts,
            cc=cc_contacts,
            subject=subject,
            snippet=body_text[:200] if body_text else "",
            body_text=body_text,
            body_html=body_html,
            attachments=attachments,
            received_at=received_at,
            is_read=False,  # Would check \Seen flag
            is_starred=False,
            labels=[],
        )

    def _parse_contact(self, header: str) -> Contact:
        """Parse 'Name <email>' format."""
        name, addr = email.utils.parseaddr(header)
        return Contact(name=name or None, email=addr)

    def _parse_contacts(self, header: str) -> list[Contact]:
        """Parse comma-separated contact list."""
        if not header:
            return []
        addrs = email.utils.getaddresses([header])
        return [Contact(name=name or None, email=addr) for name, addr in addrs]
