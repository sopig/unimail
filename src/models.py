"""Core data models for UniMail."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class Provider(str, Enum):
    GMAIL = "gmail"
    OUTLOOK = "outlook"
    IMAP = "imap"


class Contact(BaseModel):
    name: Optional[str] = None
    email: str


class Attachment(BaseModel):
    id: str
    filename: str
    mime_type: str
    size: int  # bytes


class UnifiedMessage(BaseModel):
    """Standardized email message format across all providers."""

    id: str  # internal ID: {provider}_{external_id}
    account_id: str
    external_id: str
    thread_id: Optional[str] = None
    folder: str = "inbox"

    from_contact: Contact = Field(alias="from_")
    to: list[Contact] = []
    cc: list[Contact] = []
    bcc: list[Contact] = []

    subject: str = ""
    snippet: str = ""  # first ~200 chars
    body_text: str = ""
    body_html: Optional[str] = None

    attachments: list[Attachment] = []
    received_at: datetime
    is_read: bool = False
    is_starred: bool = False
    labels: list[str] = []

    class Config:
        populate_by_name = True


class GmailConfig(BaseModel):
    client_id: str
    client_secret: str
    redirect_uri: str = "http://localhost:9876/callback"


class OutlookConfig(BaseModel):
    client_id: str
    client_secret: str
    tenant_id: str = "common"  # 'common' for personal accounts
    redirect_uri: str = "http://localhost:9876/callback"


class ImapConfig(BaseModel):
    imap_host: str
    imap_port: int = 993
    smtp_host: str
    smtp_port: int = 465
    username: str
    tls: bool = True


class SyncState(BaseModel):
    last_sync_at: Optional[datetime] = None
    gmail_history_id: Optional[str] = None
    outlook_delta_link: Optional[str] = None
    imap_last_uid: Optional[int] = None


class MailAccount(BaseModel):
    id: str
    provider: Provider
    email: str
    display_name: str = ""
    is_default: bool = False
    config: GmailConfig | OutlookConfig | ImapConfig
    sync_state: SyncState = Field(default_factory=SyncState)
    created_at: datetime = Field(default_factory=datetime.now)


# === MCP Tool Input/Output Schemas ===


class MailListInput(BaseModel):
    account: Optional[str] = None  # email address filter
    folder: str = "inbox"
    limit: int = 20
    unread_only: bool = False
    since: Optional[str] = None  # ISO date


class MailSendInput(BaseModel):
    from_: Optional[str] = Field(None, alias="from")
    to: list[str]
    cc: list[str] = []
    bcc: list[str] = []
    subject: str
    body: str  # Markdown format
    template: Optional[str] = None  # Template name (overrides body)
    template_context: Optional[dict] = None  # Context variables for template
    attachments: list[str] = []  # local file paths
    reply_to_message_id: Optional[str] = None


class RateLimitConfig(BaseModel):
    """Rate limit configuration per account."""
    default_daily: int = 50
    account_overrides: dict[str, int] = {}  # account_id -> daily limit


class MailSearchInput(BaseModel):
    query: str
    account: Optional[str] = None
    from_filter: Optional[str] = None
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    has_attachment: Optional[bool] = None
    limit: int = 10


class MailReplyInput(BaseModel):
    message_id: str
    body: str  # Markdown
    reply_all: bool = False


# === Presets for common providers ===

IMAP_PRESETS: dict[str, dict] = {
    "163": {
        "imap_host": "imap.163.com",
        "imap_port": 993,
        "smtp_host": "smtp.163.com",
        "smtp_port": 465,
        "tls": True,
    },
    "qq": {
        "imap_host": "imap.qq.com",
        "imap_port": 993,
        "smtp_host": "smtp.qq.com",
        "smtp_port": 465,
        "tls": True,
    },
    "126": {
        "imap_host": "imap.126.com",
        "imap_port": 993,
        "smtp_host": "smtp.126.com",
        "smtp_port": 465,
        "tls": True,
    },
    "sina": {
        "imap_host": "imap.sina.com",
        "imap_port": 993,
        "smtp_host": "smtp.sina.com",
        "smtp_port": 465,
        "tls": True,
    },
    "outlook": {
        "imap_host": "outlook.office365.com",
        "imap_port": 993,
        "smtp_host": "smtp-mail.outlook.com",
        "smtp_port": 587,
        "tls": True,
    },
    "yahoo": {
        "imap_host": "imap.mail.yahoo.com",
        "imap_port": 993,
        "smtp_host": "smtp.mail.yahoo.com",
        "smtp_port": 465,
        "tls": True,
    },
}


def detect_preset(email: str) -> Optional[str]:
    """Auto-detect IMAP preset from email domain."""
    domain = email.split("@")[1].lower()
    if "163.com" in domain:
        return "163"
    if "qq.com" in domain:
        return "qq"
    if "126.com" in domain:
        return "126"
    if "sina.com" in domain or "sina.cn" in domain:
        return "sina"
    if any(d in domain for d in ("outlook.com", "hotmail.com", "live.com")):
        return "outlook"
    if "yahoo.com" in domain:
        return "yahoo"
    return None
