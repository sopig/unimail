"""OpenAI Function Calling Schema - 导出符合 OpenAI spec 的 TOOLS 列表。

可独立 import，不依赖服务器启动：
    from src.schemas.openai_functions import TOOLS, dispatch
"""

from __future__ import annotations

import json
from typing import Any

# === OpenAI Function Calling 格式的 TOOLS 列表 ===

TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "mail_list",
            "description": "List emails from inbox or other folders. Returns a summary list of recent emails including sender, subject, time, and read status.",
            "parameters": {
                "type": "object",
                "properties": {
                    "account": {
                        "type": "string",
                        "description": "Email address to filter by. If not specified, queries all connected accounts.",
                    },
                    "folder": {
                        "type": "string",
                        "enum": ["inbox", "sent", "drafts", "archive", "all"],
                        "description": "Mail folder to list. Defaults to 'inbox'.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of emails to return (1-50). Defaults to 20.",
                        "minimum": 1,
                        "maximum": 50,
                    },
                    "unread_only": {
                        "type": "boolean",
                        "description": "If true, only return unread emails. Defaults to false.",
                    },
                    "since": {
                        "type": "string",
                        "description": "Only return emails after this date (ISO format: YYYY-MM-DD).",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mail_read",
            "description": "Read the full content of an email, including body text and attachment list. Use the message_id obtained from mail_list or mail_search.",
            "parameters": {
                "type": "object",
                "properties": {
                    "message_id": {
                        "type": "string",
                        "description": "The unique message ID (obtained from mail_list or mail_search results).",
                    },
                    "mark_as_read": {
                        "type": "boolean",
                        "description": "Whether to mark the email as read after opening. Defaults to true.",
                    },
                },
                "required": ["message_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mail_send",
            "description": "Send an email. Supports specifying sender account, attachments, and Markdown body (automatically converted to HTML).",
            "parameters": {
                "type": "object",
                "properties": {
                    "to": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of recipient email addresses.",
                    },
                    "subject": {
                        "type": "string",
                        "description": "Email subject line.",
                    },
                    "body": {
                        "type": "string",
                        "description": "Email body in Markdown format. Will be automatically converted to HTML.",
                    },
                    "from": {
                        "type": "string",
                        "description": "Sender email address. If not specified, uses the default account.",
                    },
                    "cc": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of CC recipients.",
                    },
                    "bcc": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of BCC recipients.",
                    },
                    "attachments": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of local file paths to attach.",
                    },
                },
                "required": ["to", "subject", "body"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mail_reply",
            "description": "Reply to an email. Automatically uses the original account and references the original subject.",
            "parameters": {
                "type": "object",
                "properties": {
                    "message_id": {
                        "type": "string",
                        "description": "The ID of the email to reply to.",
                    },
                    "body": {
                        "type": "string",
                        "description": "Reply content in Markdown format.",
                    },
                    "reply_all": {
                        "type": "boolean",
                        "description": "Whether to reply to all recipients. Defaults to false.",
                    },
                },
                "required": ["message_id", "body"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mail_search",
            "description": "Search emails by keywords, sender, date range, etc. Returns matching emails sorted by date.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search keywords to match against subject, body, and sender.",
                    },
                    "account": {
                        "type": "string",
                        "description": "Limit search to a specific account (email address).",
                    },
                    "from_filter": {
                        "type": "string",
                        "description": "Filter by sender email address or name.",
                    },
                    "date_from": {
                        "type": "string",
                        "description": "Start date for search range (YYYY-MM-DD).",
                    },
                    "date_to": {
                        "type": "string",
                        "description": "End date for search range (YYYY-MM-DD).",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of results (1-50). Defaults to 10.",
                        "minimum": 1,
                        "maximum": 50,
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mail_accounts",
            "description": "List all connected email accounts and their status (provider, email address, default account).",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mail_archive",
            "description": "Archive or trash emails by their message IDs.",
            "parameters": {
                "type": "object",
                "properties": {
                    "message_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of message IDs to archive or trash.",
                    },
                    "action": {
                        "type": "string",
                        "enum": ["archive", "trash"],
                        "description": "Action to perform. 'archive' moves to archive folder, 'trash' moves to trash. Defaults to 'archive'.",
                    },
                },
                "required": ["message_ids"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mail_attachment",
            "description": "Download an email attachment to a local file path.",
            "parameters": {
                "type": "object",
                "properties": {
                    "message_id": {
                        "type": "string",
                        "description": "The ID of the email containing the attachment.",
                    },
                    "attachment_id": {
                        "type": "string",
                        "description": "The attachment ID (obtained from mail_read results).",
                    },
                    "save_path": {
                        "type": "string",
                        "description": "Local file path to save the attachment. If not specified, saves to /tmp/.",
                    },
                },
                "required": ["message_id", "attachment_id"],
            },
        },
    },
]


async def dispatch(name: str, args: dict[str, Any]) -> str:
    """调度函数 - 接收函数名和参数字典，调用 MailEngine 执行并返回结果字符串。

    用于 OpenAI function calling 集成：
        result = await dispatch("mail_list", {"folder": "inbox", "limit": 5})

    Args:
        name: 工具名称（如 "mail_list", "mail_send" 等）
        args: 参数字典

    Returns:
        执行结果的字符串表示
    """
    from pathlib import Path
    from ..engine.mail_engine import MailEngine
    from ..storage.database import Database
    from ..storage.token_store import TokenStore

    # 初始化引擎
    data_dir = Path.home() / ".unimail" / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    import os
    passphrase = os.environ.get("UNIMAIL_PASSPHRASE", "unimail-default")
    db = Database(data_dir / "unimail.db")
    token_store = TokenStore(data_dir / "tokens.enc", passphrase)
    engine = MailEngine(db, token_store)
    await engine.initialize()

    try:
        result = await _execute(engine, db, name, args)
        return result
    finally:
        await engine.shutdown()


async def _execute(engine, db, name: str, args: dict[str, Any]) -> str:
    """内部执行逻辑 - 路由到对应的 engine 方法。"""

    if name == "mail_list":
        messages = await engine.list_messages(
            account=args.get("account"),
            folder=args.get("folder", "inbox"),
            limit=args.get("limit", 20),
            unread_only=args.get("unread_only", False),
            since=args.get("since"),
        )
        if not messages:
            return json.dumps({"emails": [], "count": 0})
        return json.dumps({
            "emails": [_msg_to_dict(m) for m in messages],
            "count": len(messages),
        }, ensure_ascii=False, default=str)

    elif name == "mail_read":
        msg = await engine.get_message(args["message_id"])
        if args.get("mark_as_read", True):
            await engine.mark_read(args["message_id"])
        return json.dumps(_msg_to_dict(msg, detail=True), ensure_ascii=False, default=str)

    elif name == "mail_send":
        result = await engine.send_message(
            to=args["to"],
            subject=args["subject"],
            body=args["body"],
            from_=args.get("from"),
            cc=args.get("cc"),
            bcc=args.get("bcc"),
            attachments=args.get("attachments"),
        )
        return json.dumps(result, ensure_ascii=False)

    elif name == "mail_reply":
        result = await engine.reply_message(
            message_id=args["message_id"],
            body=args["body"],
            reply_all=args.get("reply_all", False),
        )
        return json.dumps(result, ensure_ascii=False)

    elif name == "mail_search":
        messages = await engine.search_messages(
            query=args["query"],
            account=args.get("account"),
            from_filter=args.get("from_filter"),
            date_from=args.get("date_from"),
            date_to=args.get("date_to"),
            limit=args.get("limit", 10),
        )
        return json.dumps({
            "emails": [_msg_to_dict(m) for m in messages],
            "count": len(messages),
        }, ensure_ascii=False, default=str)

    elif name == "mail_accounts":
        accounts = db.get_accounts()
        return json.dumps({
            "accounts": [
                {
                    "id": a.id,
                    "provider": a.provider.value,
                    "email": a.email,
                    "display_name": a.display_name,
                    "is_default": a.is_default,
                }
                for a in accounts
            ]
        }, ensure_ascii=False)

    elif name == "mail_archive":
        ids = args["message_ids"]
        action = args.get("action", "archive")
        if action == "archive":
            await engine.archive_messages(ids)
        else:
            await engine.trash_messages(ids)
        return json.dumps({"status": "ok", "action": action, "count": len(ids)})

    elif name == "mail_attachment":
        path = await engine.download_attachment(
            args["message_id"],
            args["attachment_id"],
            args.get("save_path"),
        )
        return json.dumps({"saved_path": path})

    else:
        raise ValueError(f"Unknown function: {name}")


def _msg_to_dict(msg, detail: bool = False) -> dict:
    """将 UnifiedMessage 转为简洁字典。"""
    data = {
        "id": msg.id,
        "from": {"name": msg.from_contact.name, "email": msg.from_contact.email},
        "to": [{"name": c.name, "email": c.email} for c in msg.to],
        "subject": msg.subject,
        "snippet": msg.snippet,
        "received_at": msg.received_at.isoformat(),
        "is_read": msg.is_read,
        "is_starred": msg.is_starred,
        "has_attachments": len(msg.attachments) > 0,
    }
    if detail:
        data["body_text"] = msg.body_text
        data["cc"] = [{"name": c.name, "email": c.email} for c in msg.cc]
        data["attachments"] = [
            {"id": a.id, "filename": a.filename, "mime_type": a.mime_type, "size": a.size}
            for a in msg.attachments
        ]
    return data
