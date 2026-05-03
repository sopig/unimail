"""LangChain Tool 封装 - 将 UniMail 操作封装为 LangChain 兼容的 tools。

使用前需安装可选依赖：
    pip install unimail[langchain]

示例：
    from src.integrations.langchain_tools import get_all_tools
    tools = get_all_tools()
    # 传给 LangChain Agent 使用
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Optional

try:
    from langchain_core.tools import tool, BaseTool
except ImportError:
    raise ImportError(
        "langchain-core is required for LangChain integration. "
        "Install it with: pip install unimail[langchain]"
    )

from ..engine.mail_engine import MailEngine
from ..storage.database import Database
from ..storage.token_store import TokenStore


def _get_engine() -> MailEngine:
    """创建 MailEngine 实例。"""
    data_dir = Path.home() / ".unimail" / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    passphrase = os.environ.get("UNIMAIL_PASSPHRASE")
    db = Database(data_dir / "unimail.db")
    token_store = TokenStore(data_dir / "tokens.enc", passphrase)
    return MailEngine(db, token_store)


def _run_async(coro):
    """运行异步协程，兼容已有 event loop 和无 event loop 两种情况。"""
    try:
        loop = asyncio.get_running_loop()
        # 如果已有 loop，用 nest_asyncio 或 run_coroutine_threadsafe
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(asyncio.run, coro)
            return future.result()
    except RuntimeError:
        return asyncio.run(coro)


@tool
def mail_list(
    folder: str = "inbox",
    limit: int = 20,
    unread_only: bool = False,
    account: Optional[str] = None,
    since: Optional[str] = None,
) -> str:
    """List emails from inbox or other folders.

    Returns a summary of recent emails including sender, subject, time, and read status.
    Use this to check for new or unread emails.

    Args:
        folder: Mail folder - one of 'inbox', 'sent', 'drafts', 'archive', 'all'. Defaults to 'inbox'.
        limit: Maximum number of emails to return (1-50). Defaults to 20.
        unread_only: If true, only return unread emails.
        account: Filter by email address. If not specified, queries all accounts.
        since: Only return emails after this date (YYYY-MM-DD format).
    """
    async def _run():
        engine = _get_engine()
        await engine.initialize()
        try:
            messages = await engine.list_messages(
                account=account,
                folder=folder,
                limit=limit,
                unread_only=unread_only,
                since=since,
            )
            result = [
                {
                    "id": m.id,
                    "from": f"{m.from_contact.name or ''} <{m.from_contact.email}>",
                    "subject": m.subject,
                    "received_at": m.received_at.isoformat(),
                    "is_read": m.is_read,
                    "has_attachments": len(m.attachments) > 0,
                }
                for m in messages
            ]
            return json.dumps({"emails": result, "count": len(result)}, ensure_ascii=False, default=str)
        finally:
            await engine.shutdown()

    return _run_async(_run())


@tool
def mail_read(message_id: str, mark_as_read: bool = True) -> str:
    """Read the full content of an email including body and attachments.

    Use the message_id obtained from mail_list or mail_search results.

    Args:
        message_id: The unique message ID from mail_list or mail_search.
        mark_as_read: Whether to mark as read after opening. Defaults to true.
    """
    async def _run():
        engine = _get_engine()
        await engine.initialize()
        try:
            msg = await engine.get_message(message_id)
            if mark_as_read:
                await engine.mark_read(message_id)
            return json.dumps({
                "id": msg.id,
                "from": f"{msg.from_contact.name or ''} <{msg.from_contact.email}>",
                "to": [c.email for c in msg.to],
                "cc": [c.email for c in msg.cc],
                "subject": msg.subject,
                "body": msg.body_text,
                "received_at": msg.received_at.isoformat(),
                "attachments": [
                    {"id": a.id, "filename": a.filename, "size": a.size}
                    for a in msg.attachments
                ],
            }, ensure_ascii=False, default=str)
        finally:
            await engine.shutdown()

    return _run_async(_run())


@tool
def mail_send(
    to: list[str],
    subject: str,
    body: str,
    from_address: Optional[str] = None,
    cc: Optional[list[str]] = None,
    bcc: Optional[list[str]] = None,
    attachments: Optional[list[str]] = None,
) -> str:
    """Send an email with Markdown body (auto-converted to HTML).

    Args:
        to: List of recipient email addresses.
        subject: Email subject line.
        body: Email body in Markdown format (automatically converted to HTML).
        from_address: Sender email address. Uses default account if not specified.
        cc: List of CC recipients.
        bcc: List of BCC recipients.
        attachments: List of local file paths to attach.
    """
    async def _run():
        engine = _get_engine()
        await engine.initialize()
        try:
            result = await engine.send_message(
                to=to,
                subject=subject,
                body=body,
                from_=from_address,
                cc=cc,
                bcc=bcc,
                attachments=attachments,
            )
            return json.dumps(result, ensure_ascii=False)
        finally:
            await engine.shutdown()

    return _run_async(_run())


@tool
def mail_reply(message_id: str, body: str, reply_all: bool = False) -> str:
    """Reply to an email. Automatically uses the original sender account and references original subject.

    Args:
        message_id: The ID of the email to reply to.
        body: Reply content in Markdown format.
        reply_all: Whether to reply to all recipients. Defaults to false.
    """
    async def _run():
        engine = _get_engine()
        await engine.initialize()
        try:
            result = await engine.reply_message(
                message_id=message_id,
                body=body,
                reply_all=reply_all,
            )
            return json.dumps(result, ensure_ascii=False)
        finally:
            await engine.shutdown()

    return _run_async(_run())


@tool
def mail_search(
    query: str,
    account: Optional[str] = None,
    from_filter: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    limit: int = 10,
) -> str:
    """Search emails by keywords, sender, or date range.

    Args:
        query: Search keywords to match against subject, body, and sender.
        account: Limit search to a specific account email address.
        from_filter: Filter by sender email or name.
        date_from: Start date (YYYY-MM-DD).
        date_to: End date (YYYY-MM-DD).
        limit: Maximum results to return (1-50). Defaults to 10.
    """
    async def _run():
        engine = _get_engine()
        await engine.initialize()
        try:
            messages = await engine.search_messages(
                query=query,
                account=account,
                from_filter=from_filter,
                date_from=date_from,
                date_to=date_to,
                limit=limit,
            )
            result = [
                {
                    "id": m.id,
                    "from": f"{m.from_contact.name or ''} <{m.from_contact.email}>",
                    "subject": m.subject,
                    "received_at": m.received_at.isoformat(),
                    "snippet": m.snippet,
                }
                for m in messages
            ]
            return json.dumps({"emails": result, "count": len(result)}, ensure_ascii=False, default=str)
        finally:
            await engine.shutdown()

    return _run_async(_run())


@tool
def mail_accounts() -> str:
    """List all connected email accounts and their status.

    Returns account information including provider type, email address, and which is the default.
    """
    async def _run():
        engine = _get_engine()
        await engine.initialize()
        try:
            accounts = engine.db.get_accounts()
            result = [
                {
                    "id": a.id,
                    "provider": a.provider.value,
                    "email": a.email,
                    "display_name": a.display_name,
                    "is_default": a.is_default,
                }
                for a in accounts
            ]
            return json.dumps({"accounts": result}, ensure_ascii=False)
        finally:
            await engine.shutdown()

    return _run_async(_run())


@tool
def mail_archive(message_ids: list[str], action: str = "archive") -> str:
    """Archive or trash emails by their message IDs.

    Args:
        message_ids: List of message IDs to process.
        action: Either 'archive' (move to archive) or 'trash' (move to trash). Defaults to 'archive'.
    """
    async def _run():
        engine = _get_engine()
        await engine.initialize()
        try:
            if action == "archive":
                await engine.archive_messages(message_ids)
            else:
                await engine.trash_messages(message_ids)
            return json.dumps({"status": "ok", "action": action, "count": len(message_ids)})
        finally:
            await engine.shutdown()

    return _run_async(_run())


@tool
def mail_attachment(
    message_id: str,
    attachment_id: str,
    save_path: Optional[str] = None,
) -> str:
    """Download an email attachment to a local file.

    Args:
        message_id: The ID of the email containing the attachment.
        attachment_id: The attachment ID (from mail_read results).
        save_path: Local path to save to. Defaults to /tmp/{filename}.
    """
    async def _run():
        engine = _get_engine()
        await engine.initialize()
        try:
            path = await engine.download_attachment(
                message_id, attachment_id, save_path
            )
            return json.dumps({"saved_path": path})
        finally:
            await engine.shutdown()

    return _run_async(_run())


def get_all_tools() -> list[BaseTool]:
    """返回所有 UniMail LangChain tools 的列表。

    用法：
        from src.integrations.langchain_tools import get_all_tools
        tools = get_all_tools()
        agent = create_react_agent(llm, tools)
    """
    return [
        mail_list,
        mail_read,
        mail_send,
        mail_reply,
        mail_search,
        mail_accounts,
        mail_archive,
        mail_attachment,
    ]
