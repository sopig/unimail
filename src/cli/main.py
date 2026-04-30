"""UniMail CLI - Account management, mail operations, and server control."""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Optional

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.markdown import Markdown

from ..models import (
    GmailConfig,
    ImapConfig,
    IMAP_PRESETS,
    MailAccount,
    OutlookConfig,
    Provider,
    SyncState,
    detect_preset,
)
from ..storage.database import Database
from ..storage.token_store import TokenStore

console = Console()
DATA_DIR = Path.home() / ".unimail" / "data"


def get_db() -> Database:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return Database(DATA_DIR / "unimail.db")


def get_token_store(passphrase: str = "unimail-default") -> TokenStore:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return TokenStore(DATA_DIR / "tokens.enc", passphrase)


def _run_async(coro):
    """包装异步协程为同步调用。"""
    return asyncio.run(coro)


def _get_engine(passphrase: str):
    """创建并返回 MailEngine 实例。"""
    from ..engine.mail_engine import MailEngine
    db = get_db()
    token_store = get_token_store(passphrase)
    return MailEngine(db, token_store)


@click.group()
@click.option("--passphrase", default="unimail-default", envvar="UNIMAIL_PASSPHRASE",
              help="Passphrase for token encryption")
@click.pass_context
def cli(ctx, passphrase: str):
    """📮 UniMail - Unified email gateway for AI agents."""
    ctx.ensure_object(dict)
    ctx.obj["passphrase"] = passphrase


# ========== Account Management ==========


@cli.group()
def add():
    """Add a mail account."""
    pass


@add.command("gmail")
@click.option("--client-id", required=True, help="Google OAuth Client ID")
@click.option("--client-secret", required=True, help="Google OAuth Client Secret")
@click.option("--set-default", is_flag=True, help="Set as default account")
@click.pass_context
def add_gmail(ctx, client_id: str, client_secret: str, set_default: bool):
    """Add a Gmail account via OAuth."""
    from ..auth.gmail_auth import gmail_oauth_flow

    console.print("\n[bold blue]Adding Gmail account...[/bold blue]")

    # Run OAuth flow
    try:
        tokens = gmail_oauth_flow(client_id, client_secret)
    except Exception as e:
        console.print(f"[red]OAuth failed: {e}[/red]")
        return

    # Get email address from Gmail API
    console.print("[green]✓ Authorization successful![/green]")

    try:
        import httpx
        resp = httpx.get(
            "https://gmail.googleapis.com/gmail/v1/users/me/profile",
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
            timeout=10,
        )
        email_addr = resp.json().get("emailAddress", "")
    except Exception:
        email_addr = ""

    if not email_addr:
        email_addr = click.prompt("Email address")

    # Save account
    account = MailAccount(
        id=str(uuid.uuid4()),
        provider=Provider.GMAIL,
        email=email_addr,
        display_name=email_addr.split("@")[0],
        is_default=set_default,
        config=GmailConfig(
            client_id=client_id,
            client_secret=client_secret,
        ),
        sync_state=SyncState(),
    )

    db = get_db()
    token_store = get_token_store(ctx.obj["passphrase"])

    db.save_account(account)
    token_store.save(account.id, tokens)

    console.print(f"\n[bold green]✅ Gmail account added: {email_addr}[/bold green]")


@add.command("outlook")
@click.option("--client-id", required=True, help="Azure AD App Client ID")
@click.option("--client-secret", required=True, help="Azure AD App Client Secret")
@click.option("--tenant-id", default="common", help="Tenant ID (default: common)")
@click.option("--set-default", is_flag=True, help="Set as default account")
@click.pass_context
def add_outlook(ctx, client_id: str, client_secret: str, tenant_id: str, set_default: bool):
    """Add an Outlook/Hotmail account via OAuth."""
    from ..auth.outlook_auth import outlook_oauth_flow

    console.print("\n[bold blue]Adding Outlook/Hotmail account...[/bold blue]")

    try:
        tokens = outlook_oauth_flow(client_id, client_secret, tenant_id)
    except Exception as e:
        console.print(f"[red]OAuth failed: {e}[/red]")
        return

    console.print("[green]✓ Authorization successful![/green]")
    email_addr = click.prompt("Email address")

    account = MailAccount(
        id=str(uuid.uuid4()),
        provider=Provider.OUTLOOK,
        email=email_addr,
        display_name=email_addr.split("@")[0],
        is_default=set_default,
        config=OutlookConfig(
            client_id=client_id,
            client_secret=client_secret,
            tenant_id=tenant_id,
        ),
        sync_state=SyncState(),
    )

    db = get_db()
    token_store = get_token_store(ctx.obj["passphrase"])

    db.save_account(account)
    token_store.save(account.id, tokens)

    console.print(f"\n[bold green]✅ Outlook account added: {email_addr}[/bold green]")


@add.command("imap")
@click.argument("email")
@click.option("--password", required=True, help="IMAP password or authorization code (授权码)")
@click.option("--imap-host", help="IMAP server hostname")
@click.option("--imap-port", type=int, default=993, help="IMAP port")
@click.option("--smtp-host", help="SMTP server hostname")
@click.option("--smtp-port", type=int, default=465, help="SMTP port")
@click.option("--set-default", is_flag=True, help="Set as default account")
@click.pass_context
def add_imap(ctx, email: str, password: str, imap_host: str, imap_port: int,
             smtp_host: str, smtp_port: int, set_default: bool):
    """Add an IMAP/SMTP account (163, QQ, etc.)."""
    # Auto-detect preset
    preset_name = detect_preset(email)
    if preset_name and not imap_host:
        preset = IMAP_PRESETS[preset_name]
        imap_host = preset["imap_host"]
        imap_port = preset["imap_port"]
        smtp_host = preset["smtp_host"]
        smtp_port = preset["smtp_port"]
        console.print(f"[dim]Auto-detected preset: {preset_name}[/dim]")
    elif not imap_host:
        console.print("[red]Cannot auto-detect server. Please provide --imap-host and --smtp-host[/red]")
        return

    account = MailAccount(
        id=str(uuid.uuid4()),
        provider=Provider.IMAP,
        email=email,
        display_name=email.split("@")[0],
        is_default=set_default,
        config=ImapConfig(
            imap_host=imap_host,
            imap_port=imap_port,
            smtp_host=smtp_host or imap_host.replace("imap", "smtp"),
            smtp_port=smtp_port,
            username=email,
            tls=True,
        ),
        sync_state=SyncState(),
    )

    db = get_db()
    token_store = get_token_store(ctx.obj["passphrase"])

    # Test connection
    console.print(f"[dim]Testing connection to {imap_host}:{imap_port}...[/dim]")
    try:
        _run_async(_test_imap_connection(account, password))
    except Exception as e:
        console.print(f"[red]Connection failed: {e}[/red]")
        if not click.confirm("Save anyway?"):
            return

    db.save_account(account)
    token_store.save(account.id, {"password": password})

    console.print(f"\n[bold green]✅ IMAP account added: {email}[/bold green]")


# Shortcut commands for common providers
@add.command("163")
@click.argument("email")
@click.option("--password", required=True, help="163邮箱授权码")
@click.option("--set-default", is_flag=True)
@click.pass_context
def add_163(ctx, email: str, password: str, set_default: bool):
    """快速添加 163 邮箱。"""
    ctx.invoke(add_imap, email=email, password=password,
               imap_host=None, imap_port=993, smtp_host=None, smtp_port=465,
               set_default=set_default)


@add.command("qq")
@click.argument("email")
@click.option("--password", required=True, help="QQ邮箱授权码")
@click.option("--set-default", is_flag=True)
@click.pass_context
def add_qq(ctx, email: str, password: str, set_default: bool):
    """快速添加 QQ 邮箱。"""
    ctx.invoke(add_imap, email=email, password=password,
               imap_host=None, imap_port=993, smtp_host=None, smtp_port=465,
               set_default=set_default)


# ========== Account Operations ==========


@cli.command("list")
def list_accounts():
    """List all connected accounts."""
    db = get_db()
    accounts = db.get_accounts()

    if not accounts:
        console.print("[dim]No accounts configured. Use `unimail add` to add one.[/dim]")
        return

    table = Table(title="📬 Connected Accounts")
    table.add_column("", width=3)
    table.add_column("Email", style="cyan")
    table.add_column("Provider", style="green")
    table.add_column("ID", style="dim")

    for a in accounts:
        default = "★" if a.is_default else ""
        table.add_row(default, a.email, a.provider.value, a.id[:8])

    console.print(table)


@cli.command("remove")
@click.argument("email")
def remove_account(email: str):
    """Remove an account by email."""
    db = get_db()
    account = db.get_account_by_email(email)
    if not account:
        console.print(f"[red]Account not found: {email}[/red]")
        return

    if click.confirm(f"Remove {email}? This will delete all cached messages."):
        db.delete_account(account.id)
        console.print(f"[green]✅ Removed: {email}[/green]")


@cli.command("default")
@click.argument("email")
def set_default(email: str):
    """Set default account for sending."""
    db = get_db()
    account = db.get_account_by_email(email)
    if not account:
        console.print(f"[red]Account not found: {email}[/red]")
        return

    # Clear all defaults, set this one
    for a in db.get_accounts():
        a.is_default = a.id == account.id
        db.save_account(a)

    console.print(f"[green]✅ Default account set: {email}[/green]")


# ========== Mail Operations (NEW) ==========


@cli.command("inbox")
@click.option("--limit", "-n", default=20, help="返回数量")
@click.option("--unread", "-u", is_flag=True, help="只看未读")
@click.option("--account", "-a", default=None, help="指定邮箱账户")
@click.pass_context
def inbox(ctx, limit: int, unread: bool, account: Optional[str]):
    """📥 查看收件箱。"""
    passphrase = ctx.obj["passphrase"]

    async def _inbox():
        engine = _get_engine(passphrase)
        await engine.initialize()
        try:
            messages = await engine.list_messages(
                account=account,
                folder="inbox",
                limit=limit,
                unread_only=unread,
            )
            return messages
        finally:
            await engine.shutdown()

    try:
        messages = _run_async(_inbox())
    except Exception as e:
        console.print(f"[red]❌ Error: {e}[/red]")
        return

    if not messages:
        console.print("[dim]📭 收件箱为空[/dim]")
        return

    table = Table(title=f"📥 收件箱 ({len(messages)} 封)")
    table.add_column("", width=2)
    table.add_column("#", style="dim", width=3)
    table.add_column("时间", style="cyan", width=12)
    table.add_column("发件人", style="green", max_width=25)
    table.add_column("主题", max_width=50)
    table.add_column("ID", style="dim", max_width=20)

    for i, msg in enumerate(messages, 1):
        read_icon = "" if msg.is_read else "🔵"
        att_icon = " 📎" if msg.attachments else ""
        time_str = msg.received_at.strftime("%m-%d %H:%M")
        from_str = msg.from_contact.name or msg.from_contact.email
        subject = msg.subject + att_icon
        table.add_row(read_icon, str(i), time_str, from_str, subject, msg.id[:16] + "...")

    console.print(table)


@cli.command("read")
@click.argument("message_id")
@click.pass_context
def read_mail(ctx, message_id: str):
    """📖 读取邮件详情。"""
    passphrase = ctx.obj["passphrase"]

    async def _read():
        engine = _get_engine(passphrase)
        await engine.initialize()
        try:
            msg = await engine.get_message(message_id)
            await engine.mark_read(message_id)
            return msg
        finally:
            await engine.shutdown()

    try:
        msg = _run_async(_read())
    except Exception as e:
        console.print(f"[red]❌ Error: {e}[/red]")
        return

    # 构建邮件头部
    header_lines = []
    from_str = f"{msg.from_contact.name or ''} <{msg.from_contact.email}>"
    header_lines.append(f"[bold]From:[/bold]    {from_str}")
    header_lines.append(f"[bold]To:[/bold]      {', '.join(c.email for c in msg.to)}")
    if msg.cc:
        header_lines.append(f"[bold]Cc:[/bold]      {', '.join(c.email for c in msg.cc)}")
    header_lines.append(f"[bold]Subject:[/bold] {msg.subject}")
    header_lines.append(f"[bold]Date:[/bold]    {msg.received_at.strftime('%Y-%m-%d %H:%M:%S')}")
    header_lines.append(f"[bold]ID:[/bold]      {msg.id}")

    console.print(Panel(
        "\n".join(header_lines),
        title="📧 邮件详情",
        border_style="blue",
    ))

    # 正文
    body = msg.body_text or "(HTML only - 无文本内容)"
    console.print(Panel(body, title="正文", border_style="dim"))

    # 附件
    if msg.attachments:
        att_table = Table(title="📎 附件")
        att_table.add_column("文件名", style="cyan")
        att_table.add_column("大小", style="green")
        att_table.add_column("ID", style="dim")
        for att in msg.attachments:
            size_str = f"{att.size / 1024:.1f} KB"
            att_table.add_row(att.filename, size_str, att.id)
        console.print(att_table)


@cli.command("send")
@click.argument("to")
@click.option("--subject", "-s", required=True, help="邮件主题")
@click.option("--body", "-b", required=True, help="邮件正文（Markdown 格式）")
@click.option("--cc", default=None, help="抄送，多个用逗号分隔")
@click.option("--account", "-a", default=None, help="发件邮箱")
@click.option("--attachment", multiple=True, help="附件路径（可多次指定）")
@click.pass_context
def send_mail(ctx, to: str, subject: str, body: str, cc: Optional[str],
              account: Optional[str], attachment: tuple):
    """✉️ 发送邮件。"""
    passphrase = ctx.obj["passphrase"]

    # 解析收件人和抄送
    to_list = [addr.strip() for addr in to.split(",")]
    cc_list = [addr.strip() for addr in cc.split(",")] if cc else None
    att_list = list(attachment) if attachment else None

    async def _send():
        engine = _get_engine(passphrase)
        await engine.initialize()
        try:
            result = await engine.send_message(
                to=to_list,
                subject=subject,
                body=body,
                from_=account,
                cc=cc_list,
                attachments=att_list,
            )
            return result
        finally:
            await engine.shutdown()

    try:
        result = _run_async(_send())
    except Exception as e:
        console.print(f"[red]❌ 发送失败: {e}[/red]")
        return

    console.print(Panel(
        f"[bold]From:[/bold]    {result['from']}\n"
        f"[bold]To:[/bold]      {', '.join(result['to'])}\n"
        f"[bold]Subject:[/bold] {result['subject']}",
        title="[green]✅ 邮件已发送[/green]",
        border_style="green",
    ))


@cli.command("reply")
@click.argument("message_id")
@click.option("--body", "-b", required=True, help="回复内容（Markdown）")
@click.option("--reply-all", is_flag=True, help="回复所有人")
@click.pass_context
def reply_mail(ctx, message_id: str, body: str, reply_all: bool):
    """↩️ 回复邮件。"""
    passphrase = ctx.obj["passphrase"]

    async def _reply():
        engine = _get_engine(passphrase)
        await engine.initialize()
        try:
            result = await engine.reply_message(
                message_id=message_id,
                body=body,
                reply_all=reply_all,
            )
            return result
        finally:
            await engine.shutdown()

    try:
        result = _run_async(_reply())
    except Exception as e:
        console.print(f"[red]❌ 回复失败: {e}[/red]")
        return

    console.print(Panel(
        f"[bold]From:[/bold] {result['from']}\n"
        f"[bold]To:[/bold]   {', '.join(result['to'])}",
        title="[green]✅ 回复已发送[/green]",
        border_style="green",
    ))


@cli.command("search")
@click.argument("query")
@click.option("--account", "-a", default=None, help="限定搜索的账户")
@click.option("--limit", "-n", default=10, help="返回数量")
@click.pass_context
def search_mail(ctx, query: str, account: Optional[str], limit: int):
    """🔍 搜索邮件。"""
    passphrase = ctx.obj["passphrase"]

    async def _search():
        engine = _get_engine(passphrase)
        await engine.initialize()
        try:
            messages = await engine.search_messages(
                query=query,
                account=account,
                limit=limit,
            )
            return messages
        finally:
            await engine.shutdown()

    try:
        messages = _run_async(_search())
    except Exception as e:
        console.print(f"[red]❌ Error: {e}[/red]")
        return

    if not messages:
        console.print(f"[dim]🔍 未找到匹配「{query}」的邮件[/dim]")
        return

    table = Table(title=f"🔍 搜索结果: \"{query}\" ({len(messages)} 封)")
    table.add_column("#", style="dim", width=3)
    table.add_column("时间", style="cyan", width=12)
    table.add_column("发件人", style="green", max_width=25)
    table.add_column("主题", max_width=50)
    table.add_column("ID", style="dim", max_width=20)

    for i, msg in enumerate(messages, 1):
        time_str = msg.received_at.strftime("%m-%d %H:%M")
        from_str = msg.from_contact.name or msg.from_contact.email
        table.add_row(str(i), time_str, from_str, msg.subject, msg.id[:16] + "...")

    console.print(table)


# ========== Server ==========


@cli.command("serve")
@click.option("--mode", type=click.Choice(["mcp", "api", "all"]), default="mcp",
              help="服务模式: mcp (MCP stdin), api (REST API), all (同时启动)")
@click.option("--port", type=int, default=8765, help="REST API 端口")
@click.option("--transport", type=click.Choice(["stdio", "sse"]), default="stdio",
              help="MCP transport 类型（仅 mcp/all 模式）")
@click.pass_context
def serve(ctx, mode: str, port: int, transport: str):
    """🚀 Start UniMail server."""
    passphrase = ctx.obj["passphrase"]

    if mode == "mcp":
        # 原有 MCP Server 行为
        from ..server import run_server
        console.print(f"[bold]🚀 Starting UniMail MCP Server (transport: {transport})[/bold]")
        _run_async(run_server(passphrase))

    elif mode == "api":
        # 启动 REST API
        import uvicorn
        from ..api import create_app

        console.print(f"[bold]🚀 Starting UniMail REST API on port {port}[/bold]")
        console.print(f"[dim]API docs: http://localhost:{port}/docs[/dim]")

        app = create_app(passphrase)
        uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")

    elif mode == "all":
        # 同时启动 MCP + REST API
        console.print(
            f"[bold]🚀 Starting UniMail (MCP + REST API on port {port})[/bold]"
        )
        console.print(f"[dim]API docs: http://localhost:{port}/docs[/dim]")
        _run_async(_run_all(passphrase, port))


async def _run_all(passphrase: str, port: int):
    """同时运行 MCP Server (stdin) 和 REST API。"""
    import uvicorn
    from ..api import create_app
    from ..engine.mail_engine import MailEngine
    from ..server import UniMailServer
    from ..storage.database import Database
    from ..storage.token_store import TokenStore

    # 共享 MailEngine
    data_dir = DATA_DIR
    data_dir.mkdir(parents=True, exist_ok=True)
    db = Database(data_dir / "unimail.db")
    token_store = TokenStore(data_dir / "tokens.enc", passphrase)
    engine = MailEngine(db, token_store)
    await engine.initialize()

    # 创建 MCP Server（使用共享 engine）
    mail_server = UniMailServer(passphrase)
    # 替换 MCP server 的 engine 和 db 为共享实例
    mail_server.engine = engine
    mail_server.db = db

    # 创建 REST API（使用共享 engine）
    app = create_app(passphrase, engine=engine)

    # 启动 REST API（后台任务）
    config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="info")
    server = uvicorn.Server(config)

    # 并发运行 MCP + API
    from mcp.server.stdio import stdio_server

    async def run_mcp():
        async with stdio_server() as (read_stream, write_stream):
            await mail_server.server.run(read_stream, write_stream)

    async def run_api():
        await server.serve()

    try:
        await asyncio.gather(run_mcp(), run_api())
    finally:
        await engine.shutdown()


# ========== Utility ==========


# ========== Schema Export ==========


@cli.group()
def schema():
    """📋 Export tool schemas for various AI agent integrations."""
    pass


@schema.command("openai")
def schema_openai():
    """Output OpenAI function calling JSON schema to stdout."""
    import json as json_mod
    from ..schemas.openai_functions import TOOLS
    click.echo(json_mod.dumps(TOOLS, indent=2, ensure_ascii=False))


@schema.command("openapi")
@click.option("--port", type=int, default=8765, help="API server port (for server URLs)")
@click.pass_context
def schema_openapi(ctx, port: int):
    """Output OpenAPI spec JSON to stdout."""
    import json as json_mod
    from ..api import create_app

    passphrase = ctx.obj["passphrase"]
    app = create_app(passphrase)
    spec = app.openapi()
    click.echo(json_mod.dumps(spec, indent=2, ensure_ascii=False))


@schema.command("mcp")
def schema_mcp():
    """Output MCP tool definitions to stdout."""
    import json as json_mod
    from ..schemas.openai_functions import TOOLS

    # 将 OpenAI function calling 格式转为 MCP tool 格式
    mcp_tools = [
        {
            "name": t["function"]["name"],
            "description": t["function"]["description"],
            "inputSchema": t["function"]["parameters"],
        }
        for t in TOOLS
    ]
    click.echo(json_mod.dumps(mcp_tools, indent=2, ensure_ascii=False))


# ========== Utility ==========


@cli.command("sync")
@click.pass_context
def sync(ctx):
    """Manually sync all accounts."""
    from ..engine.mail_engine import MailEngine

    passphrase = ctx.obj["passphrase"]
    db = get_db()
    token_store = get_token_store(passphrase)
    engine = MailEngine(db, token_store)

    async def _sync():
        await engine.initialize()
        count = await engine.sync_all()
        console.print(f"[green]✅ Synced {count} new messages[/green]")
        await engine.shutdown()

    _run_async(_sync())


@cli.command("test")
@click.argument("email")
@click.pass_context
def test_connection(ctx, email: str):
    """Test connection for an account."""
    db = get_db()
    token_store = get_token_store(ctx.obj["passphrase"])
    account = db.get_account_by_email(email)

    if not account:
        console.print(f"[red]Account not found: {email}[/red]")
        return

    tokens = token_store.get(account.id)
    console.print(f"[dim]Testing {account.provider.value} connection for {email}...[/dim]")

    async def _test():
        from ..connectors.imap_connector import ImapSmtpConnector
        from ..connectors.gmail_connector import GmailConnector
        from ..connectors.outlook_connector import OutlookConnector

        if account.provider == Provider.IMAP:
            connector = ImapSmtpConnector(account, tokens.get("password", ""))
        elif account.provider == Provider.GMAIL:
            connector = GmailConnector(account, tokens)
        else:
            connector = OutlookConnector(account, tokens)

        await connector.connect()
        messages = await connector.list_messages(limit=3)
        await connector.disconnect()
        return messages

    try:
        messages = _run_async(_test())
        console.print(f"[green]✅ Connected! Found {len(messages)} recent messages.[/green]")
        for msg in messages[:3]:
            console.print(f"  • {msg.subject[:50]}")
    except Exception as e:
        console.print(f"[red]❌ Failed: {e}[/red]")


# ========== Helpers ==========


async def _test_imap_connection(account: MailAccount, password: str):
    """Test IMAP connection."""
    from ..connectors.imap_connector import ImapSmtpConnector
    connector = ImapSmtpConnector(account, password)
    await connector.connect()
    await connector.disconnect()


if __name__ == "__main__":
    cli()
