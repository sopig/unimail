"""MCP Server for UniMail - exposes email operations as tools for AI agents."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool, Resource, LoggingLevel

from .engine.mail_engine import MailEngine
from .storage.database import Database
from .storage.token_store import TokenStore

# Default paths
DATA_DIR = Path.home() / ".unimail" / "data"
DB_PATH = DATA_DIR / "unimail.db"
TOKEN_PATH = DATA_DIR / "tokens.enc"


def get_data_dir() -> Path:
    d = Path.home() / ".unimail" / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d


class UniMailServer:
    """MCP Server that wraps MailEngine and exposes tools."""

    def __init__(self, passphrase: str | None = None):
        self.data_dir = get_data_dir()
        self.db = Database(self.data_dir / "unimail.db")
        self.token_store = TokenStore(self.data_dir / "tokens.enc", passphrase)
        self.engine = MailEngine(self.db, self.token_store)
        self.server = Server("unimail")
        self._request_context = None  # for sending notifications
        self._register_tools()

    def _register_tools(self):
        """Register all MCP tools."""

        @self.server.list_tools()
        async def list_tools() -> list[Tool]:
            return [
                Tool(
                    name="mail_list",
                    description="查看邮件列表。返回收件箱/已发送/所有邮件的摘要信息（发件人、主题、时间、已读状态）。",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "account": {
                                "type": "string",
                                "description": "邮箱地址，不指定则查所有账户",
                            },
                            "folder": {
                                "type": "string",
                                "enum": ["inbox", "sent", "drafts", "archive", "all"],
                                "default": "inbox",
                                "description": "文件夹",
                            },
                            "limit": {
                                "type": "integer",
                                "default": 20,
                                "minimum": 1,
                                "maximum": 50,
                                "description": "返回数量",
                            },
                            "unread_only": {
                                "type": "boolean",
                                "default": False,
                                "description": "只看未读",
                            },
                            "since": {
                                "type": "string",
                                "description": "只返回此日期之后的邮件 (YYYY-MM-DD)",
                            },
                        },
                    },
                ),
                Tool(
                    name="mail_read",
                    description="读取邮件完整内容，包括正文和附件列表。",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "message_id": {
                                "type": "string",
                                "description": "邮件ID（从 mail_list 获取）",
                            },
                            "mark_as_read": {
                                "type": "boolean",
                                "default": True,
                                "description": "是否标记为已读",
                            },
                        },
                        "required": ["message_id"],
                    },
                ),
                Tool(
                    name="mail_send",
                    description="发送邮件。支持指定发件账号、附件、Markdown正文（自动转HTML），或使用模板。",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "to": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "收件人邮箱列表",
                            },
                            "subject": {"type": "string", "description": "邮件主题"},
                            "body": {
                                "type": "string",
                                "description": "邮件正文（Markdown格式，自动转HTML）。使用模板时可留空。",
                                "default": "",
                            },
                            "from": {
                                "type": "string",
                                "description": "发件邮箱地址，不指定用默认账号",
                            },
                            "cc": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "抄送",
                            },
                            "bcc": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "密送",
                            },
                            "attachments": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "附件本地文件路径列表",
                            },
                            "template": {
                                "type": "string",
                                "description": "邮件模板名称（如 welcome.html），使用后 body 作为纯文本 fallback",
                            },
                            "template_context": {
                                "type": "object",
                                "description": "模板渲染变量",
                            },
                        },
                        "required": ["to", "subject"],
                    },
                ),
                Tool(
                    name="mail_reply",
                    description="回复一封邮件（自动使用原账号、引用原主题）。",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "message_id": {
                                "type": "string",
                                "description": "要回复的邮件ID",
                            },
                            "body": {
                                "type": "string",
                                "description": "回复内容（Markdown）",
                            },
                            "reply_all": {
                                "type": "boolean",
                                "default": False,
                                "description": "是否回复所有人",
                            },
                        },
                        "required": ["message_id", "body"],
                    },
                ),
                Tool(
                    name="mail_search",
                    description="搜索邮件。支持关键词、发件人、日期范围等条件。",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "搜索关键词"},
                            "account": {
                                "type": "string",
                                "description": "限定搜索的账户",
                            },
                            "from_filter": {
                                "type": "string",
                                "description": "发件人过滤",
                            },
                            "date_from": {
                                "type": "string",
                                "description": "起始日期 YYYY-MM-DD",
                            },
                            "date_to": {
                                "type": "string",
                                "description": "结束日期 YYYY-MM-DD",
                            },
                            "limit": {
                                "type": "integer",
                                "default": 10,
                                "minimum": 1,
                                "maximum": 50,
                            },
                        },
                        "required": ["query"],
                    },
                ),
                Tool(
                    name="mail_accounts",
                    description="查看已连接的邮箱账户列表及状态。",
                    inputSchema={"type": "object", "properties": {}},
                ),
                Tool(
                    name="mail_archive",
                    description="归档或删除邮件。",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "message_ids": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "邮件ID列表",
                            },
                            "action": {
                                "type": "string",
                                "enum": ["archive", "trash"],
                                "default": "archive",
                            },
                        },
                        "required": ["message_ids"],
                    },
                ),
                Tool(
                    name="mail_attachment",
                    description="下载邮件附件到本地文件。",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "message_id": {"type": "string"},
                            "attachment_id": {"type": "string"},
                            "save_path": {
                                "type": "string",
                                "description": "保存路径，不指定则存到 /tmp/",
                            },
                        },
                        "required": ["message_id", "attachment_id"],
                    },
                ),
                Tool(
                    name="mail_mark",
                    description="标记邮件状态：已读、未读、星标、取消星标。",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "message_ids": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "邮件ID列表",
                            },
                            "action": {
                                "type": "string",
                                "enum": ["read", "unread", "star", "unstar"],
                                "description": "标记操作",
                            },
                        },
                        "required": ["message_ids", "action"],
                    },
                ),
                Tool(
                    name="mail_forward",
                    description="转发邮件给其他人。可添加附言。",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "message_id": {
                                "type": "string",
                                "description": "要转发的邮件ID",
                            },
                            "to": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "收件人邮箱列表",
                            },
                            "comment": {
                                "type": "string",
                                "description": "转发附言",
                            },
                        },
                        "required": ["message_id", "to"],
                    },
                ),
                Tool(
                    name="mail_check_new",
                    description="检查是否有新邮件到达（后台自动同步）。返回上次检查以来的新邮件数量和摘要，并重置计数器。",
                    inputSchema={"type": "object", "properties": {}},
                ),
            ]

        @self.server.call_tool()
        async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
            try:
                result = await self._dispatch(name, arguments)
                return [TextContent(type="text", text=result)]
            except Exception as e:
                return [TextContent(type="text", text=f"❌ Error: {str(e)}")]

        @self.server.list_resources()
        async def list_resources() -> list[Resource]:
            return [
                Resource(
                    uri="unimail://new-messages",
                    name="New Messages",
                    description="New messages since last check (updated by background sync)",
                    mimeType="application/json",
                ),
            ]

        @self.server.read_resource()
        async def read_resource(uri) -> str:
            if str(uri) == "unimail://new-messages":
                result = await self.engine.check_new_messages()
                return json.dumps(result, default=str, ensure_ascii=False)
            raise ValueError(f"Unknown resource: {uri}")

        # Register notification callback for new messages from background sync
        async def _on_new_mail(count: int) -> None:
            """Send MCP log notification when background sync finds new messages."""
            try:
                ctx = self.server.request_context
                if ctx and hasattr(ctx, 'session'):
                    await ctx.session.send_log_message(
                        level="info",
                        data=f"UniMail: {count} new email(s) received. Use mail_check_new to see details.",
                    )
            except Exception:
                pass  # Notification is best-effort

        self.engine._on_new_messages = _on_new_mail

    async def _dispatch(self, tool_name: str, args: dict) -> str:
        """Route tool calls to engine methods."""

        if tool_name == "mail_list":
            messages = await self.engine.list_messages(
                account=args.get("account"),
                folder=args.get("folder", "inbox"),
                limit=args.get("limit", 20),
                unread_only=args.get("unread_only", False),
                since=args.get("since"),
            )
            return self._format_message_list(messages)

        elif tool_name == "mail_read":
            msg = await self.engine.get_message(args["message_id"])
            if args.get("mark_as_read", True):
                await self.engine.mark_read(args["message_id"])
            return self._format_message_detail(msg)

        elif tool_name == "mail_send":
            result = await self.engine.send_message(
                to=args["to"],
                subject=args["subject"],
                body=args.get("body", ""),
                from_=args.get("from"),
                cc=args.get("cc"),
                bcc=args.get("bcc"),
                attachments=args.get("attachments"),
                template=args.get("template"),
                template_context=args.get("template_context"),
            )
            return f"✅ 邮件已发送\nFrom: {result['from']}\nTo: {', '.join(result['to'])}\nSubject: {result['subject']}"

        elif tool_name == "mail_reply":
            result = await self.engine.reply_message(
                message_id=args["message_id"],
                body=args["body"],
                reply_all=args.get("reply_all", False),
            )
            return f"✅ 回复已发送\nFrom: {result['from']}\nTo: {', '.join(result['to'])}"

        elif tool_name == "mail_search":
            messages = await self.engine.search_messages(
                query=args["query"],
                account=args.get("account"),
                from_filter=args.get("from_filter"),
                date_from=args.get("date_from"),
                date_to=args.get("date_to"),
                limit=args.get("limit", 10),
            )
            return self._format_message_list(messages)

        elif tool_name == "mail_accounts":
            accounts = self.db.get_accounts()
            lines = ["📬 已连接的邮箱账户:\n"]
            for a in accounts:
                default = " ★默认" if a.is_default else ""
                lines.append(f"  • {a.email} ({a.provider.value}){default}")
                lines.append(f"    ID: {a.id}")
            if not accounts:
                lines.append("  (无账户，请用 `unimail add` 命令添加)")
            return "\n".join(lines)

        elif tool_name == "mail_archive":
            ids = args["message_ids"]
            action = args.get("action", "archive")
            if action == "archive":
                await self.engine.archive_messages(ids)
            else:
                await self.engine.trash_messages(ids)
            action_cn = "归档" if action == "archive" else "移入回收站"
            return f"✅ {len(ids)} 封邮件已{action_cn}"

        elif tool_name == "mail_attachment":
            path = await self.engine.download_attachment(
                args["message_id"],
                args["attachment_id"],
                args.get("save_path"),
            )
            return f"✅ 附件已保存: {path}"

        elif tool_name == "mail_mark":
            ids = args["message_ids"]
            action = args["action"]
            if action == "read":
                for mid in ids:
                    await self.engine.mark_read(mid)
                return f"✅ {len(ids)} 封邮件已标记为已读"
            elif action == "unread":
                for mid in ids:
                    await self.engine.mark_unread(mid)
                return f"✅ {len(ids)} 封邮件已标记为未读"
            elif action == "star":
                for mid in ids:
                    await self.engine.star_message(mid)
                return f"✅ {len(ids)} 封邮件已加星标"
            elif action == "unstar":
                for mid in ids:
                    await self.engine.unstar_message(mid)
                return f"✅ {len(ids)} 封邮件已取消星标"

        elif tool_name == "mail_forward":
            result = await self.engine.forward_message(
                message_id=args["message_id"],
                to=args["to"],
                comment=args.get("comment", ""),
            )
            return f"✅ 邮件已转发\nFrom: {result['from']}\nTo: {', '.join(result['to'])}"

        elif tool_name == "mail_check_new":
            result = await self.engine.check_new_messages()
            if result["new_count"] == 0:
                sync_info = f" (上次同步: {result['last_sync_at']})" if result.get("last_sync_at") else ""
                return f"📭 没有新邮件{sync_info}"
            messages = result["messages"]
            lines = [f"📬 {result['new_count']} 封新邮件:\n"]
            for i, msg in enumerate(messages, 1):
                from_str = msg.from_contact.name or msg.from_contact.email
                time_str = msg.received_at.strftime("%m-%d %H:%M")
                lines.append(
                    f"  {i}. [{time_str}] {from_str}\n"
                    f"     {msg.subject}\n"
                    f"     ID: {msg.external_id}"
                )
            return "\n".join(lines)

        else:
            raise ValueError(f"Unknown tool: {tool_name}")

    # === Formatters ===

    def _format_message_list(self, messages: list) -> str:
        if not messages:
            return "📭 没有邮件"

        lines = [f"📬 共 {len(messages)} 封邮件:\n"]
        for i, msg in enumerate(messages, 1):
            read_icon = "  " if msg.is_read else "🔵"
            att_icon = " 📎" if msg.attachments else ""
            time_str = msg.received_at.strftime("%m-%d %H:%M")
            from_str = msg.from_contact.name or msg.from_contact.email
            # Use short ID for Agent token efficiency
            short_id = msg.external_id
            lines.append(
                f"{read_icon} {i}. [{time_str}] {from_str}{att_icon}\n"
                f"      {msg.subject}\n"
                f"      ID: {short_id}"
            )
        return "\n".join(lines)

    def _format_message_detail(self, msg) -> str:
        lines = [
            f"━━━ 邮件详情 ━━━",
            f"From: {msg.from_contact.name or ''} <{msg.from_contact.email}>",
            f"To: {', '.join(c.email for c in msg.to)}",
        ]
        if msg.cc:
            lines.append(f"Cc: {', '.join(c.email for c in msg.cc)}")
        lines.extend([
            f"Subject: {msg.subject}",
            f"Date: {msg.received_at.strftime('%Y-%m-%d %H:%M:%S')}",
            f"ID: {msg.external_id}",
            f"━━━━━━━━━━━━━━━",
            "",
        ])

        # Body: prefer plain text, fallback to HTML→text conversion
        body = msg.body_text
        if not body and msg.body_html:
            body = self._html_to_text(msg.body_html)
        lines.append(body or "(no content)")

        if msg.attachments:
            lines.append("\n📎 附件:")
            for att in msg.attachments:
                size_kb = att.size / 1024
                lines.append(f"  • {att.filename} ({size_kb:.1f}KB) [ID: {att.id}]")
        return "\n".join(lines)

    @staticmethod
    def _html_to_text(html: str) -> str:
        """Convert HTML to plain text for Agent readability."""
        try:
            import html2text
            h = html2text.HTML2Text()
            h.ignore_links = False
            h.ignore_images = True
            h.body_width = 0  # no line wrapping
            return h.handle(html).strip()
        except Exception:
            # Fallback: strip tags
            import re
            text = re.sub(r"<[^>]+>", "", html)
            return text.strip()


async def run_server(passphrase: str | None = None):
    """Run the MCP server over stdio."""
    mail_server = UniMailServer(passphrase)
    await mail_server.engine.initialize()

    # Do initial sync in background (don't block server startup)
    async def _initial_sync():
        try:
            count = await mail_server.engine.sync_all()
            if count > 0:
                mail_server.engine._new_since_last_check += count
        except Exception:
            pass

    asyncio.create_task(_initial_sync())

    async with stdio_server() as (read_stream, write_stream):
        await mail_server.server.run(read_stream, write_stream)


def main():
    """Entry point for MCP server."""
    import sys
    passphrase = sys.argv[1] if len(sys.argv) > 1 else None
    asyncio.run(run_server(passphrase))


if __name__ == "__main__":
    main()
