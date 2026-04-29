"""Generic IMAP/SMTP connector for 163, QQ, and other standard mail providers."""

from __future__ import annotations

import asyncio
import email
import email.utils
import email.policy
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
from ..models import (
    Attachment,
    Contact,
    ImapConfig,
    MailAccount,
    SyncState,
    UnifiedMessage,
)


class ImapSmtpConnector(MailConnector):
    """
    Connector for any IMAP/SMTP-compatible mail server.
    Works with 163, QQ, 126, Yahoo, and generic providers.
    """

    def __init__(self, account: MailAccount, password: str):
        super().__init__(account)
        self.config: ImapConfig = account.config  # type: ignore
        self.password = password
        self.imap: Optional[aioimaplib.IMAP4_SSL] = None

    async def connect(self) -> None:
        """Connect to IMAP server."""
        self.imap = aioimaplib.IMAP4_SSL(
            host=self.config.imap_host,
            port=self.config.imap_port,
        )
        await self.imap.wait_hello_from_server()
        await self.imap.login(self.config.username, self.password)
        await self.imap.select("INBOX")

    async def disconnect(self) -> None:
        """Disconnect from IMAP."""
        if self.imap:
            try:
                await self.imap.logout()
            except Exception:
                pass
            self.imap = None

    async def list_messages(
        self,
        folder: str = "inbox",
        limit: int = 20,
        unread_only: bool = False,
        since: Optional[str] = None,
    ) -> list[UnifiedMessage]:
        """List messages via IMAP SEARCH + FETCH."""
        assert self.imap is not None

        # Select folder
        folder_name = self._map_folder(folder)
        await self.imap.select(folder_name)

        # Build search criteria
        criteria = "ALL"
        if unread_only:
            criteria = "UNSEEN"
        if since:
            # IMAP date format: DD-Mon-YYYY
            dt = datetime.fromisoformat(since)
            criteria += f' SINCE {dt.strftime("%d-%b-%Y")}'

        # Search
        status, data = await self.imap.search(criteria)
        if status != "OK":
            return []

        # Get UIDs (most recent first)
        uids = data[0].split()
        uids = list(reversed(uids))[:limit]

        if not uids:
            return []

        # Fetch headers for these UIDs
        messages = []
        uid_str = ",".join(u.decode() if isinstance(u, bytes) else u for u in uids)
        status, fetch_data = await self.imap.fetch(
            uid_str, "(UID FLAGS ENVELOPE BODYSTRUCTURE RFC822.SIZE)"
        )

        for i in range(0, len(fetch_data), 2):
            if i < len(fetch_data):
                msg = self._parse_fetch_response(fetch_data[i])
                if msg:
                    messages.append(msg)

        return messages

    async def get_message(self, external_id: str) -> UnifiedMessage:
        """Fetch full message by UID."""
        assert self.imap is not None

        status, data = await self.imap.fetch(external_id, "(RFC822 FLAGS)")
        if status != "OK" or not data:
            raise ValueError(f"Message {external_id} not found")

        # Parse raw email
        raw_email = data[0]
        if isinstance(raw_email, tuple):
            raw_email = raw_email[1]
        if isinstance(raw_email, bytes):
            msg = email.message_from_bytes(raw_email, policy=email.policy.default)
        else:
            msg = email.message_from_string(raw_email, policy=email.policy.default)

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
        assert self.imap is not None
        await self.imap.store(external_id, "+FLAGS", r"(\Seen)")

    async def mark_unread(self, external_id: str) -> None:
        assert self.imap is not None
        await self.imap.store(external_id, "-FLAGS", r"(\Seen)")

    async def archive(self, external_id: str) -> None:
        """Move to Archive folder (or All Mail for Gmail-like)."""
        assert self.imap is not None
        # Try common archive folder names
        for folder in ["Archive", "All Mail", "&Xstrg1LZFw-"]:  # 163 uses special encoding
            try:
                await self.imap.copy(external_id, folder)
                await self.imap.store(external_id, "+FLAGS", r"(\Deleted)")
                await self.imap.expunge()
                return
            except Exception:
                continue
        # Fallback: just remove from inbox (mark deleted)
        await self.imap.store(external_id, "+FLAGS", r"(\Deleted)")

    async def trash(self, external_id: str) -> None:
        assert self.imap is not None
        for folder in ["Trash", "&XfJT0ZAB-", "Deleted Messages"]:
            try:
                await self.imap.copy(external_id, folder)
                await self.imap.store(external_id, "+FLAGS", r"(\Deleted)")
                await self.imap.expunge()
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
        assert self.imap is not None

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

        status, data = await self.imap.search(criteria)
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

        return messages

    async def download_attachment(
        self, message_id: str, attachment_id: str
    ) -> tuple[bytes, str]:
        """Download attachment by fetching the full message and extracting the part."""
        assert self.imap is not None

        status, data = await self.imap.fetch(message_id, "(RFC822)")
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
        assert self.imap is not None

        last_uid = self.account.sync_state.imap_last_uid or 0
        
        # Search for UIDs > last_uid
        status, data = await self.imap.search(f"UID {last_uid + 1}:*")
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

    def _parse_fetch_response(self, data) -> Optional[UnifiedMessage]:
        """Parse IMAP FETCH response into UnifiedMessage (headers only)."""
        # This is a simplified parser; real implementation would use
        # the envelope data from FETCH response
        try:
            if isinstance(data, bytes):
                data = data.decode("utf-8", errors="replace")
            # ... parse envelope fields
            # For now, return None (full implementation uses RFC822 parsing)
            return None
        except Exception:
            return None

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
