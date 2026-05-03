"""Generic IMAP/SMTP connector for 163, QQ, and other standard mail providers."""

from __future__ import annotations

import asyncio
import email
import email.header
import email.utils
import email.policy
import re
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

    def __init__(self, account: MailAccount, password: str, token_store=None):
        super().__init__(account, token_store=token_store)
        self.config: ImapConfig = account.config  # type: ignore
        self._password = password  # internal, excluded from repr/logging
        self._connection: Optional[aioimaplib.IMAP4_SSL] = None
        self._last_activity: float = 0.0
        self._connection_timeout: int = get_config().imap.connection_timeout
        self._noop_task: Optional[asyncio.Task] = None

    @property
    def password(self) -> str:
        return self._password

    def __repr__(self) -> str:
        return f"ImapSmtpConnector({self.account.email})"

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

        # Start NOOP keepalive
        self._start_noop_keepalive()

    async def _ensure_connected(self) -> None:
        """Ensure connection is alive, reconnecting if necessary."""
        if not self.is_connected:
            try:
                await self.connect()
            except Exception as e:
                logger.warning(
                    f"Reconnect failed: {e}, retrying once",
                    extra={"account_id": self.account.id},
                )
                # Single retry with fresh connection
                self._connection = None
                await self.connect()
        self._last_activity = time.time()

    def _start_noop_keepalive(self) -> None:
        """Start a background task that sends NOOP to keep the IMAP connection alive."""
        if self._noop_task and not self._noop_task.done():
            return
        interval = max(self._connection_timeout // 2, 60)  # NOOP at half the timeout, min 60s
        self._noop_task = asyncio.create_task(self._noop_loop(interval))

    def _stop_noop_keepalive(self) -> None:
        """Cancel the NOOP keepalive task."""
        if self._noop_task and not self._noop_task.done():
            self._noop_task.cancel()
        self._noop_task = None

    async def _noop_loop(self, interval: int) -> None:
        """Periodically send NOOP to keep the IMAP connection alive."""
        try:
            while True:
                await asyncio.sleep(interval)
                if self._connection and self.is_connected:
                    try:
                        await self._connection.noop()
                        self._last_activity = time.time()
                        logger.debug("NOOP sent", extra={"account_id": self.account.id})
                    except Exception as e:
                        logger.debug(
                            f"NOOP failed: {e}, connection may be stale",
                            extra={"account_id": self.account.id},
                        )
                        # Mark as stale so next _ensure_connected will reconnect
                        self._last_activity = 0.0
        except asyncio.CancelledError:
            pass

    async def disconnect(self) -> None:
        """Disconnect from IMAP."""
        self._stop_noop_keepalive()
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
        """List messages via IMAP SEARCH + FETCH (ENVELOPE FLAGS only — fast)."""
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

        # Fetch ENVELOPE + FLAGS for all messages at once
        seq_set = ",".join(
            seq.decode() if isinstance(seq, bytes) else seq for seq in seq_nums
        )
        status, data = await self._connection.fetch(seq_set, "(ENVELOPE FLAGS)")
        if status != "OK" or not data:
            return []

        # Parse each FETCH response line
        messages = []
        for item in data:
            if isinstance(item, bytes):
                line = item.decode("utf-8", errors="replace")
            elif isinstance(item, str):
                line = item
            else:
                continue

            if "FETCH" not in line:
                continue

            try:
                msg = self._parse_envelope(line)
                messages.append(msg)
            except Exception:
                logger.debug("Failed to parse ENVELOPE response", exc_info=True)
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

        # Parse FLAGS from response line
        is_read = True  # default to read; \Seen presence confirms it
        raw_email = None
        for item in data:
            if isinstance(item, bytes):
                line = item.decode("utf-8", errors="replace")
                if "FETCH" in line:
                    # Parse FLAGS (...) from response line like: "1 FETCH (FLAGS (\Seen) RFC822 {...}"
                    if r"\Seen" not in line:
                        is_read = False
            elif isinstance(item, bytearray):
                raw_email = bytes(item)

        if raw_email is None:
            raise ValueError(f"Message {external_id} not found: no RFC822 data")

        msg = email.message_from_bytes(raw_email, policy=email.policy.default)

        self._last_activity = time.time()
        return self._parse_full_email(msg, external_id, is_read=is_read)

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
            criteria_parts.append(f'BODY "{self._imap_escape(query)}"')
        if from_filter:
            criteria_parts.append(f'FROM "{self._imap_escape(from_filter)}"')
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

    @staticmethod
    def _imap_escape(value: str) -> str:
        """Escape special characters in IMAP quoted strings."""
        return value.replace("\\", "\\\\").replace('"', '\\"')

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

    def _parse_envelope(self, fetch_line: str) -> UnifiedMessage:
        """Parse a FETCH (ENVELOPE FLAGS) response line into a UnifiedMessage.

        Example input:
            * 1 FETCH (ENVELOPE ("date" "subject" (...) (...) (...) (...) ...) FLAGS (\\Seen))
        """
        # Extract sequence number — format: "123 FETCH (...)" or "* 123 FETCH (...)"
        seq_match = re.match(r"(?:\*\s+)?(\d+)\s+FETCH\s*\(", fetch_line)
        if not seq_match:
            raise ValueError("No sequence number in FETCH response")
        seq_num = seq_match.group(1)

        # Extract FLAGS
        flags_match = re.search(r"FLAGS\s*\(([^)]*)\)", fetch_line)
        flags_str = flags_match.group(1) if flags_match else ""
        is_read = r"\Seen" in flags_str

        # Extract ENVELOPE content — everything between ENVELOPE ( and the closing )
        # that matches back to the start of FLAGS or end of line
        env_match = re.search(r"ENVELOPE\s*\(", fetch_line)
        if not env_match:
            raise ValueError("No ENVELOPE in FETCH response")

        # Find the matching closing paren for ENVELOPE
        start = env_match.end()  # position right after "ENVELOPE ("
        depth = 1
        pos = start
        while pos < len(fetch_line) and depth > 0:
            if fetch_line[pos] == "(":
                depth += 1
            elif fetch_line[pos] == ")":
                depth -= 1
            pos += 1
        env_body = fetch_line[start : pos - 1]  # exclude the final closing paren

        # Parse the top-level fields of the ENVELOPE.
        # We split by respecting quoted strings and parenthesized groups.
        fields = self._split_envelope_fields(env_body)

        # ENVELOPE field indices:
        # 0: date, 1: subject, 2: from, 3: sender, 4: reply-to,
        # 5: to, 6: cc, 7: bcc, 8: in-reply-to, 9: message-id
        date_str = self._unquote(fields[0]) if len(fields) > 0 else ""
        subject = self._unquote(fields[1]) if len(fields) > 1 else ""
        from_raw = fields[2] if len(fields) > 2 else ""
        to_raw = fields[5] if len(fields) > 5 else ""
        cc_raw = fields[6] if len(fields) > 6 else ""
        in_reply_to = self._unquote(fields[8]) if len(fields) > 8 else None
        message_id = self._unquote(fields[9]) if len(fields) > 9 else None

        # Parse date
        try:
            received_at = email.utils.parsedate_to_datetime(date_str)
        except Exception:
            received_at = datetime.now()

        # Parse addresses
        from_contact = self._parse_address_struct(from_raw)
        to_contacts = self._parse_address_list(to_raw)
        cc_contacts = self._parse_address_list(cc_raw)

        return UnifiedMessage(
            id=f"imap_{self.account.id}_{seq_num}",
            account_id=self.account.id,
            external_id=seq_num,
            thread_id=in_reply_to,
            folder="inbox",
            from_=from_contact,
            to=to_contacts,
            cc=cc_contacts,
            subject=subject,
            snippet="",
            body_text="",
            body_html=None,
            attachments=[],
            received_at=received_at,
            is_read=is_read,
            is_starred=False,
            labels=[],
        )

    @staticmethod
    def _split_envelope_fields(text: str) -> list[str]:
        """Split ENVELOPE body into top-level fields, respecting quotes and parens."""
        fields = []
        i = 0
        n = len(text)
        while i < n:
            # Skip whitespace and commas
            while i < n and text[i] in " \t,":
                i += 1
            if i >= n:
                break

            if text[i] == '"':
                # Quoted string — find closing quote, respecting escapes
                j = i + 1
                while j < n:
                    if text[j] == "\\" and j + 1 < n:
                        j += 2
                    elif text[j] == '"':
                        j += 1
                        break
                    else:
                        j += 1
                fields.append(text[i:j])
                i = j
            elif text[i] == "(":
                # Parenthesized group — find matching close
                depth = 1
                j = i + 1
                while j < n and depth > 0:
                    if text[j] == "(":
                        depth += 1
                    elif text[j] == ")":
                        depth -= 1
                    elif text[j] == "\\" and j + 1 < n:
                        j += 1  # skip escaped char
                    j += 1
                fields.append(text[i + 1 : j - 1])  # strip outer parens
                i = j
            elif text[i] == "N" and text[i : i + 3] == "NIL":
                fields.append("NIL")
                i += 3
            else:
                # Atom
                j = i
                while j < n and text[j] not in ' \t,()"':
                    j += 1
                fields.append(text[i:j])
                i = j
        return fields

    @staticmethod
    def _unquote(s: str) -> str:
        """Remove surrounding quotes, unescape, and decode MIME encoded-words."""
        if not s or s == "NIL":
            return ""
        if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
            s = s[1:-1].replace('\\"', '"').replace("\\\\", "\\")
        # Decode MIME encoded-words like =?UTF-8?B?...?= or =?UTF-8?Q?...?=
        if "=?" in s:
            decoded_parts = email.header.decode_header(s)
            result = []
            for part, charset in decoded_parts:
                if isinstance(part, bytes):
                    result.append(part.decode(charset or "utf-8", errors="replace"))
                else:
                    result.append(part)
            s = "".join(result)
        return s

    @staticmethod
    def _parse_address_struct(raw: str) -> Contact:
        """Parse a single IMAP address struct: (personal NIL mailbox host) or NIL.

        Input may have outer parens still present, e.g.:
        ("name" NIL "mailbox" "host")
        or without parens:
        "name" NIL "mailbox" "host"
        """
        if not raw or raw == "NIL":
            return Contact(name=None, email="")
        text = raw.strip()
        # Strip outer parens if present
        if text.startswith("(") and text.endswith(")"):
            text = text[1:-1].strip()
        parts = ImapSmtpConnector._split_envelope_fields(text)
        personal = ImapSmtpConnector._unquote(parts[0]) if len(parts) > 0 else ""
        mailbox = ImapSmtpConnector._unquote(parts[2]) if len(parts) > 2 else ""
        host = ImapSmtpConnector._unquote(parts[3]) if len(parts) > 3 else ""
        addr = f"{mailbox}@{host}" if mailbox and host else mailbox
        return Contact(name=personal or None, email=addr)

    @staticmethod
    def _parse_address_list(raw: str) -> list[Contact]:
        """Parse a list of IMAP address structs from ENVELOPE field.

        Input is the content inside the outer parens of the to/cc field,
        which may contain multiple address structs like:
            ("John" NIL john example.com)("Jane" NIL jane example.com)
        or with an extra wrapping layer:
            (("John" NIL john example.com))
        or NIL for empty.
        """
        if not raw or raw == "NIL":
            return []
        # If the content starts with '(' and all content is inside one
        # group, strip the outer layer (IMAP wraps address lists in parens)
        text = raw.strip()
        if text.startswith("(") and text.endswith(")"):
            # Check if it's a single group (no sibling groups at the same level)
            depth = 0
            has_sibling = False
            for idx, ch in enumerate(text):
                if ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                    if depth == 0 and idx < len(text) - 1:
                        has_sibling = True
                        break
            if not has_sibling:
                text = text[1:-1].strip()

        contacts = []
        i = 0
        n = len(text)
        while i < n:
            while i < n and text[i] in " \t":
                i += 1
            if i >= n:
                break
            if text[i] == "(":
                depth = 1
                j = i + 1
                while j < n and depth > 0:
                    if text[j] == "(":
                        depth += 1
                    elif text[j] == ")":
                        depth -= 1
                    elif text[j] == "\\" and j + 1 < n:
                        j += 1
                    j += 1
                inner = text[i + 1 : j - 1]
                contacts.append(ImapSmtpConnector._parse_address_struct(inner))
                i = j
            else:
                i += 1
        return contacts

    def _parse_full_email(
        self, msg: email.message.Message, uid: str, *, is_read: bool = True
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
            is_read=is_read,
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
