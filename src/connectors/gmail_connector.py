"""Gmail REST API connector using Google API client."""

from __future__ import annotations

import base64
import email.mime.multipart
import email.mime.text
import email.mime.base
from datetime import datetime
from email import encoders
from pathlib import Path
from typing import Optional

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

from .base import MailConnector
from ..models import (
    Attachment,
    Contact,
    GmailConfig,
    MailAccount,
    UnifiedMessage,
)


class GmailConnector(MailConnector):
    """
    Gmail connector using REST API.
    Supports OAuth 2.0, incremental sync via historyId, and full Gmail search.
    """

    def __init__(self, account: MailAccount, tokens: dict):
        """
        tokens: {
            "access_token": "...",
            "refresh_token": "...",
            "token_uri": "https://oauth2.googleapis.com/token",
            "client_id": "...",
            "client_secret": "...",
            "expiry": "..."
        }
        """
        super().__init__(account)
        self.config: GmailConfig = account.config  # type: ignore
        self._tokens = tokens
        self._service = None

    async def connect(self) -> None:
        """Initialize Gmail API service."""
        creds = Credentials(
            token=self._tokens.get("access_token"),
            refresh_token=self._tokens.get("refresh_token"),
            token_uri="https://oauth2.googleapis.com/token",
            client_id=self.config.client_id,
            client_secret=self.config.client_secret,
        )
        # Refresh if expired
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            # Update stored tokens
            self._tokens["access_token"] = creds.token
            if creds.expiry:
                self._tokens["expiry"] = creds.expiry.isoformat()

        self._service = build("gmail", "v1", credentials=creds)

    async def disconnect(self) -> None:
        self._service = None

    async def list_messages(
        self,
        folder: str = "inbox",
        limit: int = 20,
        unread_only: bool = False,
        since: Optional[str] = None,
    ) -> list[UnifiedMessage]:
        """List messages using Gmail API."""
        assert self._service is not None

        # Build Gmail search query
        query_parts = []
        if folder == "inbox":
            query_parts.append("in:inbox")
        elif folder == "sent":
            query_parts.append("in:sent")
        elif folder == "drafts":
            query_parts.append("in:drafts")
        if unread_only:
            query_parts.append("is:unread")
        if since:
            query_parts.append(f"after:{since}")

        query = " ".join(query_parts)

        # List message IDs
        result = (
            self._service.users()
            .messages()
            .list(userId="me", q=query, maxResults=limit)
            .execute()
        )

        message_ids = [m["id"] for m in result.get("messages", [])]
        if not message_ids:
            return []

        # Fetch each message (batch would be better for production)
        messages = []
        for msg_id in message_ids:
            try:
                msg = await self.get_message(msg_id)
                messages.append(msg)
            except Exception:
                continue

        return messages

    async def get_message(self, external_id: str) -> UnifiedMessage:
        """Get full message by ID."""
        assert self._service is not None

        result = (
            self._service.users()
            .messages()
            .get(userId="me", id=external_id, format="full")
            .execute()
        )

        return self._parse_gmail_message(result)

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
        """Send email via Gmail API."""
        assert self._service is not None

        # Build MIME message
        if attachments:
            msg = email.mime.multipart.MIMEMultipart("mixed")
            body_part = email.mime.multipart.MIMEMultipart("alternative")
            body_part.attach(email.mime.text.MIMEText(body_text, "plain", "utf-8"))
            if body_html:
                body_part.attach(email.mime.text.MIMEText(body_html, "html", "utf-8"))
            msg.attach(body_part)

            for file_path in attachments:
                p = Path(file_path)
                if p.exists():
                    part = email.mime.base.MIMEBase("application", "octet-stream")
                    part.set_payload(p.read_bytes())
                    encoders.encode_base64(part)
                    part.add_header(
                        "Content-Disposition", f"attachment; filename={p.name}"
                    )
                    msg.attach(part)
        else:
            if body_html:
                msg = email.mime.multipart.MIMEMultipart("alternative")
                msg.attach(email.mime.text.MIMEText(body_text, "plain", "utf-8"))
                msg.attach(email.mime.text.MIMEText(body_html, "html", "utf-8"))
            else:
                msg = email.mime.text.MIMEText(body_text, "plain", "utf-8")

        msg["To"] = ", ".join(to)
        msg["From"] = self.account.email
        msg["Subject"] = subject
        if cc:
            msg["Cc"] = ", ".join(cc)

        # Handle reply
        thread_id = None
        if reply_to_id:
            original = (
                self._service.users()
                .messages()
                .get(userId="me", id=reply_to_id, format="metadata", metadataHeaders=["Message-Id"])
                .execute()
            )
            headers = original.get("payload", {}).get("headers", [])
            for h in headers:
                if h["name"].lower() == "message-id":
                    msg["In-Reply-To"] = h["value"]
                    msg["References"] = h["value"]
            thread_id = original.get("threadId")

        # Encode and send
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
        send_body = {"raw": raw}
        if thread_id:
            send_body["threadId"] = thread_id

        result = (
            self._service.users()
            .messages()
            .send(userId="me", body=send_body)
            .execute()
        )

        return result["id"]

    async def mark_read(self, external_id: str) -> None:
        assert self._service is not None
        self._service.users().messages().modify(
            userId="me", id=external_id, body={"removeLabelIds": ["UNREAD"]}
        ).execute()

    async def mark_unread(self, external_id: str) -> None:
        assert self._service is not None
        self._service.users().messages().modify(
            userId="me", id=external_id, body={"addLabelIds": ["UNREAD"]}
        ).execute()

    async def archive(self, external_id: str) -> None:
        assert self._service is not None
        self._service.users().messages().modify(
            userId="me", id=external_id, body={"removeLabelIds": ["INBOX"]}
        ).execute()

    async def trash(self, external_id: str) -> None:
        assert self._service is not None
        self._service.users().messages().trash(userId="me", id=external_id).execute()

    async def search(
        self,
        query: str,
        from_filter: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        limit: int = 10,
    ) -> list[UnifiedMessage]:
        """Gmail native search (very powerful)."""
        parts = [query]
        if from_filter:
            parts.append(f"from:{from_filter}")
        if date_from:
            parts.append(f"after:{date_from}")
        if date_to:
            parts.append(f"before:{date_to}")

        return await self.list_messages(folder="all", limit=limit, since=None)

    async def download_attachment(
        self, message_id: str, attachment_id: str
    ) -> tuple[bytes, str]:
        """Download Gmail attachment."""
        assert self._service is not None

        # Get attachment data
        att = (
            self._service.users()
            .messages()
            .attachments()
            .get(userId="me", messageId=message_id, id=attachment_id)
            .execute()
        )

        data = base64.urlsafe_b64decode(att["data"])

        # Get filename from message
        msg_result = (
            self._service.users()
            .messages()
            .get(userId="me", id=message_id, format="full")
            .execute()
        )
        filename = self._find_attachment_filename(msg_result, attachment_id)

        return data, filename

    async def sync_incremental(self) -> list[UnifiedMessage]:
        """Incremental sync using Gmail history API."""
        assert self._service is not None

        history_id = self.account.sync_state.gmail_history_id
        if not history_id:
            # First sync: just get recent messages
            return await self.list_messages(limit=50)

        try:
            result = (
                self._service.users()
                .history()
                .list(
                    userId="me",
                    startHistoryId=history_id,
                    historyTypes=["messageAdded"],
                )
                .execute()
            )
        except Exception:
            # History ID too old, do full sync
            return await self.list_messages(limit=50)

        new_message_ids = []
        for history in result.get("history", []):
            for added in history.get("messagesAdded", []):
                new_message_ids.append(added["message"]["id"])

        # Update history ID
        if "historyId" in result:
            self.account.sync_state.gmail_history_id = result["historyId"]

        messages = []
        for msg_id in new_message_ids:
            try:
                msg = await self.get_message(msg_id)
                messages.append(msg)
            except Exception:
                continue

        return messages

    # === Helpers ===

    def _parse_gmail_message(self, data: dict) -> UnifiedMessage:
        """Parse Gmail API message response into UnifiedMessage."""
        headers = data.get("payload", {}).get("headers", [])
        
        def get_header(name: str) -> str:
            for h in headers:
                if h["name"].lower() == name.lower():
                    return h["value"]
            return ""

        # Parse body
        body_text, body_html = self._extract_body(data.get("payload", {}))

        # Parse attachments
        attachments = self._extract_attachments(data.get("payload", {}))

        # Parse date
        internal_date = data.get("internalDate", "0")
        received_at = datetime.fromtimestamp(int(internal_date) / 1000)

        # Labels
        label_ids = data.get("labelIds", [])

        return UnifiedMessage(
            id=f"gmail_{data['id']}",
            account_id=self.account.id,
            external_id=data["id"],
            thread_id=data.get("threadId"),
            folder=self._infer_folder(label_ids),
            from_=self._parse_contact(get_header("From")),
            to=self._parse_contacts(get_header("To")),
            cc=self._parse_contacts(get_header("Cc")),
            subject=get_header("Subject"),
            snippet=data.get("snippet", ""),
            body_text=body_text,
            body_html=body_html,
            attachments=attachments,
            received_at=received_at,
            is_read="UNREAD" not in label_ids,
            is_starred="STARRED" in label_ids,
            labels=label_ids,
        )

    def _extract_body(self, payload: dict) -> tuple[str, Optional[str]]:
        """Recursively extract text and HTML body from Gmail payload."""
        body_text = ""
        body_html = None

        mime_type = payload.get("mimeType", "")
        
        if mime_type == "text/plain":
            data = payload.get("body", {}).get("data", "")
            if data:
                body_text = base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
        elif mime_type == "text/html":
            data = payload.get("body", {}).get("data", "")
            if data:
                body_html = base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
        elif "parts" in payload:
            for part in payload["parts"]:
                t, h = self._extract_body(part)
                if t and not body_text:
                    body_text = t
                if h and not body_html:
                    body_html = h

        return body_text, body_html

    def _extract_attachments(self, payload: dict) -> list[Attachment]:
        """Extract attachment metadata from payload."""
        attachments = []

        def walk(part: dict):
            if part.get("filename"):
                att_id = part.get("body", {}).get("attachmentId", "")
                if att_id:
                    attachments.append(
                        Attachment(
                            id=att_id,
                            filename=part["filename"],
                            mime_type=part.get("mimeType", "application/octet-stream"),
                            size=part.get("body", {}).get("size", 0),
                        )
                    )
            for sub in part.get("parts", []):
                walk(sub)

        walk(payload)
        return attachments

    def _infer_folder(self, labels: list[str]) -> str:
        if "INBOX" in labels:
            return "inbox"
        if "SENT" in labels:
            return "sent"
        if "DRAFT" in labels:
            return "drafts"
        if "TRASH" in labels:
            return "trash"
        if "SPAM" in labels:
            return "spam"
        return "archive"

    def _parse_contact(self, header: str) -> Contact:
        import email.utils
        name, addr = email.utils.parseaddr(header)
        return Contact(name=name or None, email=addr)

    def _parse_contacts(self, header: str) -> list[Contact]:
        import email.utils
        if not header:
            return []
        addrs = email.utils.getaddresses([header])
        return [Contact(name=name or None, email=addr) for name, addr in addrs]

    def _find_attachment_filename(self, msg_data: dict, att_id: str) -> str:
        """Find filename for an attachment ID."""
        def search(part: dict) -> Optional[str]:
            if part.get("body", {}).get("attachmentId") == att_id:
                return part.get("filename", "attachment")
            for sub in part.get("parts", []):
                result = search(sub)
                if result:
                    return result
            return None

        return search(msg_data.get("payload", {})) or "attachment"
