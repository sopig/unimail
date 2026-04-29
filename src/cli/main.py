"""UniMail CLI - Account management and server control."""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

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

    # Get email address from tokens
    # We'll get it from the first API call
    console.print("[green]✓ Authorization successful![/green]")

    # Prompt for email (or could extract from token)
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
        asyncio.run(_test_imap_connection(account, password))
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


# ========== Server ==========


@cli.command("serve")
@click.option("--transport", type=click.Choice(["stdio", "sse"]), default="stdio")
@click.option("--port", type=int, default=3100, help="Port for SSE transport")
@click.pass_context
def serve(ctx, transport: str, port: int):
    """Start MCP server."""
    from ..server import run_server

    passphrase = ctx.obj["passphrase"]
    console.print(f"[bold]🚀 Starting UniMail MCP Server (transport: {transport})[/bold]")

    if transport == "stdio":
        asyncio.run(run_server(passphrase))
    else:
        console.print(f"[dim]SSE server on port {port}...[/dim]")
        # TODO: SSE transport
        asyncio.run(run_server(passphrase))


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

    asyncio.run(_sync())


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
        messages = asyncio.run(_test())
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
