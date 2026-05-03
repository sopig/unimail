"""Microsoft Graph API connector for Outlook/Hotmail."""

from __future__ import annotations

import base64
from datetime import datetime
from pathlib import Path
from typing import Optional

import httpx
from msal import ConfidentialClientApplication, PublicClientApplication

from .base import MailConnector
from ..models import (
    Attachment,
    Contact,
    MailAccount,
    OutlookConfig,
    UnifiedMessage,
)


GRAPH_BASE = "https://graph.microsoft.com/v1.0"
SCOPES = ["https://graph.microsoft.com/Mail.ReadWrite", "https://graph.microsoft.com/Mail.Send"]


class OutlookConnector(MailConnector):
    """
    Outlook/Hotmail connector using Microsoft Graph API.
    Supports OAuth 2.0, delta sync, and full Graph search.
    """

    def __init__(self, account: MailAccount, tokens: dict, token_store=None):
        """
        tokens: {
            "access_token": "...",
            "refresh_token": "...",
        }
        """
        super().__init__(account, token_store=token_store)
        self.config: OutlookConfig = account.config  # type: ignore
        self._tokens = tokens
        self._client: Optional[httpx.AsyncClient] = None
        self._msal_app: Optional[ConfidentialClientApplication] = None

    async def connect(self) -> None:
        """Initialize MSAL and HTTP client."""
        # Use consumers authority for personal accounts when no client_secret
        if self.config.client_secret:
            authority = f"https://login.microsoftonline.com/{self.config.tenant_id}"
            self._msal_app = ConfidentialClientApplication(
                client_id=self.config.client_id,
                client_credential=self.config.client_secret,
                authority=authority,
            )
        else:
            authority = "https://login.microsoftonline.com/consumers"
            self._msal_app = PublicClientApplication(
                client_id=self.config.client_id,
                authority=authority,
            )

        # Try to get token silently using refresh token
        access_token = await self._get_access_token()

        self._client = httpx.AsyncClient(
            base_url=GRAPH_BASE,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=30.0,
        )

    async def disconnect(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def _get_access_token(self) -> str:
        """Get valid access token, refreshing if needed."""
        assert self._msal_app is not None

        # Try acquire by refresh token
        result = self._msal_app.acquire_token_by_refresh_token(
            self._tokens.get("refresh_token", ""),
            scopes=SCOPES,
        )

        if "access_token" in result:
            self._tokens["access_token"] = result["access_token"]
            if "refresh_token" in result:
                self._tokens["refresh_token"] = result["refresh_token"]
            self._persist_tokens()
            return result["access_token"]

        # Fallback to existing token
        return self._tokens.get("access_token", "")

    async def _request(self, method: str, url: str, **kwargs) -> dict:
        """Make authenticated Graph API request with auto-retry on 401."""
        assert self._client is not None
        resp = await self._client.request(method, url, **kwargs)

        if resp.status_code == 401:
            # Refresh token and retry
            token = await self._get_access_token()
            self._client.headers["Authorization"] = f"Bearer {token}"
            resp = await self._client.request(method, url, **kwargs)

        resp.raise_for_status()
        return resp.json() if resp.content else {}

    async def list_messages(
        self,
        folder: str = "inbox",
        limit: int = 20,
        unread_only: bool = False,
        since: Optional[str] = None,
    ) -> list[UnifiedMessage]:
        """List messages from Graph API."""
        # Map folder
        folder_id = self._map_folder(folder)
        url = f"/me/mailFolders/{folder_id}/messages"

        params = {
            "$top": str(limit),
            "$orderby": "receivedDateTime desc",
            "$select": "id,subject,from,toRecipients,ccRecipients,receivedDateTime,"
                       "isRead,flag,bodyPreview,hasAttachments,body,conversationId",
        }

        # Filters
        filters = []
        if unread_only:
            filters.append("isRead eq false")
        if since:
            filters.append(f"receivedDateTime ge {since}T00:00:00Z")
        if filters:
            params["$filter"] = " and ".join(filters)

        data = await self._request("GET", url, params=params)

        messages = []
        for item in data.get("value", []):
            messages.append(self._parse_graph_message(item))

        return messages

    async def get_message(self, external_id: str) -> UnifiedMessage:
        """Get full message by ID."""
        url = f"/me/messages/{external_id}"
        params = {"$select": "id,subject,from,toRecipients,ccRecipients,bccRecipients,"
                             "receivedDateTime,isRead,flag,body,bodyPreview,"
                             "hasAttachments,attachments,conversationId"}
        
        data = await self._request("GET", url, params=params)
        return self._parse_graph_message(data)

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
        """Send email via Graph API."""
        # Build message object
        message = {
            "subject": subject,
            "body": {
                "contentType": "HTML" if body_html else "Text",
                "content": body_html or body_text,
            },
            "toRecipients": [{"emailAddress": {"address": addr}} for addr in to],
        }

        if cc:
            message["ccRecipients"] = [{"emailAddress": {"address": addr}} for addr in cc]
        if bcc:
            message["bccRecipients"] = [{"emailAddress": {"address": addr}} for addr in bcc]

        # Handle attachments
        if attachments:
            message["attachments"] = []
            for file_path in attachments:
                p = Path(file_path)
                if p.exists():
                    content = base64.b64encode(p.read_bytes()).decode("ascii")
                    message["attachments"].append({
                        "@odata.type": "#microsoft.graph.fileAttachment",
                        "name": p.name,
                        "contentBytes": content,
                    })

        # Reply or new message
        if reply_to_id:
            url = f"/me/messages/{reply_to_id}/reply"
            data = await self._request("POST", url, json={"message": message, "comment": body_text})
            return reply_to_id
        else:
            url = "/me/sendMail"
            await self._request("POST", url, json={"message": message})
            return "sent"

    async def mark_read(self, external_id: str) -> None:
        await self._request("PATCH", f"/me/messages/{external_id}", json={"isRead": True})

    async def mark_unread(self, external_id: str) -> None:
        await self._request("PATCH", f"/me/messages/{external_id}", json={"isRead": False})

    async def archive(self, external_id: str) -> None:
        await self._request(
            "POST", f"/me/messages/{external_id}/move",
            json={"destinationId": "archive"}
        )

    async def trash(self, external_id: str) -> None:
        await self._request(
            "POST", f"/me/messages/{external_id}/move",
            json={"destinationId": "deleteditems"}
        )

    async def search(
        self,
        query: str,
        from_filter: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        limit: int = 10,
    ) -> list[UnifiedMessage]:
        """Search using Graph $search or $filter."""
        url = "/me/messages"
        params = {
            "$top": str(limit),
            "$orderby": "receivedDateTime desc",
            "$search": f'"{query}"',
        }

        # Additional filters
        filters = []
        if from_filter:
            filters.append(f"from/emailAddress/address eq '{from_filter}'")
        if date_from:
            filters.append(f"receivedDateTime ge {date_from}T00:00:00Z")
        if date_to:
            filters.append(f"receivedDateTime le {date_to}T23:59:59Z")
        if filters:
            # Note: $search and $filter can't always combine in Graph API
            # Fallback to just $filter if needed
            del params["$search"]
            all_filters = filters
            if query:
                all_filters.append(f"contains(subject, '{query}')")
            params["$filter"] = " and ".join(all_filters)

        data = await self._request("GET", url, params=params)
        return [self._parse_graph_message(item) for item in data.get("value", [])]

    async def download_attachment(
        self, message_id: str, attachment_id: str
    ) -> tuple[bytes, str]:
        """Download attachment from Graph API."""
        url = f"/me/messages/{message_id}/attachments/{attachment_id}"
        data = await self._request("GET", url)

        content = base64.b64decode(data.get("contentBytes", ""))
        filename = data.get("name", "attachment")
        return content, filename

    async def sync_incremental(self) -> list[UnifiedMessage]:
        """Delta sync using Graph delta links."""
        delta_link = self.account.sync_state.outlook_delta_link

        if delta_link:
            url = delta_link
            params = {}
        else:
            url = "/me/mailFolders/inbox/messages/delta"
            params = {"$top": "50", "$orderby": "receivedDateTime desc"}

        try:
            data = await self._request("GET", url, params=params)
        except Exception:
            # Delta link expired, start fresh
            return await self.list_messages(limit=50)

        messages = []
        for item in data.get("value", []):
            if "@removed" not in item:
                messages.append(self._parse_graph_message(item))

        # Save next delta link
        next_link = data.get("@odata.deltaLink") or data.get("@odata.nextLink")
        if next_link:
            self.account.sync_state.outlook_delta_link = next_link

        return messages

    # === Helpers ===

    def _map_folder(self, folder: str) -> str:
        mapping = {
            "inbox": "inbox",
            "sent": "sentitems",
            "drafts": "drafts",
            "trash": "deleteditems",
            "archive": "archive",
            "spam": "junkemail",
            "all": "allmails",
        }
        return mapping.get(folder, folder)

    def _parse_graph_message(self, data: dict) -> UnifiedMessage:
        """Parse Graph API message into UnifiedMessage."""
        # From
        from_data = data.get("from", {}).get("emailAddress", {})
        from_contact = Contact(
            name=from_data.get("name"),
            email=from_data.get("address", ""),
        )

        # To
        to_contacts = [
            Contact(
                name=r.get("emailAddress", {}).get("name"),
                email=r.get("emailAddress", {}).get("address", ""),
            )
            for r in data.get("toRecipients", [])
        ]

        # Cc
        cc_contacts = [
            Contact(
                name=r.get("emailAddress", {}).get("name"),
                email=r.get("emailAddress", {}).get("address", ""),
            )
            for r in data.get("ccRecipients", [])
        ]

        # Body
        body = data.get("body", {})
        body_content = body.get("content", "")
        is_html = body.get("contentType", "").lower() == "html"

        # Attachments
        attachments = []
        for att in data.get("attachments", []):
            if att.get("@odata.type") == "#microsoft.graph.fileAttachment":
                attachments.append(
                    Attachment(
                        id=att.get("id", ""),
                        filename=att.get("name", "attachment"),
                        mime_type=att.get("contentType", "application/octet-stream"),
                        size=att.get("size", 0),
                    )
                )

        # Date
        received_str = data.get("receivedDateTime", "")
        try:
            received_at = datetime.fromisoformat(received_str.replace("Z", "+00:00"))
        except Exception:
            received_at = datetime.now()

        return UnifiedMessage(
            id=f"outlook_{data['id']}",
            account_id=self.account.id,
            external_id=data["id"],
            thread_id=data.get("conversationId"),
            folder="inbox",
            from_=from_contact,
            to=to_contacts,
            cc=cc_contacts,
            subject=data.get("subject", ""),
            snippet=data.get("bodyPreview", "")[:200],
            body_text=body_content if not is_html else "",
            body_html=body_content if is_html else None,
            attachments=attachments,
            received_at=received_at,
            is_read=data.get("isRead", False),
            is_starred=data.get("flag", {}).get("flagStatus") == "flagged",
            labels=[],
        )
