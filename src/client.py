"""
UniMail 高层 SDK — 一行初始化，开箱即用。

Usage:
    from unimail import UniMail

    mail = UniMail()
    mails = await mail.inbox(limit=5)
    await mail.send("test@qq.com", subject="周报", body="本周完成了...")
"""

import asyncio
from pathlib import Path
from typing import Optional

from .models import UnifiedMessage, MailSendInput
from .storage.database import Database
from .storage.token_store import TokenStore
from .engine.mail_engine import MailEngine


# 默认数据目录
DEFAULT_DATA_DIR = Path.home() / ".unimail"


class UniMail:
    """统一邮件客户端 — 给脚本和 Agent 用的极简接口。"""

    def __init__(
        self,
        passphrase: Optional[str] = None,
        data_dir: Optional[str] = None,
    ):
        """
        初始化 UniMail 客户端。

        Args:
            passphrase: Token 加密密码。不传则从环境变量 UNIMAIL_PASSPHRASE 读取，
                       再没有则用默认值（仅本地使用可接受）。
            data_dir: 数据目录路径。默认 ~/.unimail/
        """
        import os

        self._data_dir = Path(data_dir) if data_dir else DEFAULT_DATA_DIR
        self._data_dir.mkdir(parents=True, exist_ok=True)

        passphrase = passphrase or os.environ.get("UNIMAIL_PASSPHRASE", "unimail-default")

        self._db = Database(self._data_dir / "unimail.db")
        self._ts = TokenStore(self._data_dir / "tokens.enc", passphrase)
        self._engine = MailEngine(self._db, self._ts)

    # ─── 收件 ───────────────────────────────────────────────

    async def inbox(
        self,
        limit: int = 20,
        unread: bool = False,
        account: Optional[str] = None,
    ) -> list[UnifiedMessage]:
        """获取收件箱邮件列表。"""
        return await self._engine.list_messages(
            folder="inbox", limit=limit, unread_only=unread, account=account
        )

    async def read(self, message_id: str) -> UnifiedMessage:
        """读取邮件详情（自动标记已读）。"""
        return await self._engine.read_message(message_id)

    async def search(
        self,
        query: str,
        limit: int = 20,
        account: Optional[str] = None,
    ) -> list[UnifiedMessage]:
        """全文搜索邮件。"""
        return await self._engine.search(query, limit=limit, account=account)

    # ─── 发件 ───────────────────────────────────────────────

    async def send(
        self,
        to: str | list[str],
        subject: str,
        body: str,
        cc: Optional[list[str]] = None,
        account: Optional[str] = None,
        attachments: Optional[list[str]] = None,
    ) -> str:
        """
        发送邮件。

        Args:
            to: 收件人（单个或列表）
            subject: 主题
            body: 正文（支持 Markdown/HTML）
            cc: 抄送
            account: 指定发件账户（邮箱地址），不传用默认
            attachments: 附件文件路径列表

        Returns:
            发送结果描述
        """
        if isinstance(to, str):
            to = [to]

        return await self._engine.send_message(
            send_input=MailSendInput(
                to=to,
                subject=subject,
                body=body,
                cc=cc or [],
                attachments=attachments or [],
            ),
            account=account,
        )

    async def reply(
        self,
        message_id: str,
        body: str,
        account: Optional[str] = None,
    ) -> str:
        """回复邮件。"""
        return await self._engine.reply_message(
            message_id=message_id, body=body, account=account
        )

    # ─── 操作 ───────────────────────────────────────────────

    async def archive(self, message_id: str) -> str:
        """归档邮件。"""
        return await self._engine.archive_message(message_id)

    async def sync(self, account: Optional[str] = None):
        """手动触发同步。"""
        await self._engine.sync(account=account)

    # ─── 账户 ───────────────────────────────────────────────

    @property
    def accounts(self) -> list[dict]:
        """已连接的账户列表。"""
        accts = self._db.get_accounts()
        return [
            {
                "email": a.email,
                "provider": a.provider.value,
                "is_default": a.is_default,
            }
            for a in accts
        ]

    # ─── 生命周期 ────────────────────────────────────────────

    async def close(self):
        """关闭连接释放资源。"""
        await self._engine.shutdown()
        self._db.close()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.close()

    # ─── 同步便捷方法（给不想写 async 的脚本用）─────────────

    def sync_inbox(self, **kwargs) -> list[UnifiedMessage]:
        """同步版 inbox。"""
        return asyncio.run(self.inbox(**kwargs))

    def sync_send(self, to, subject, body, **kwargs) -> str:
        """同步版 send。"""
        return asyncio.run(self.send(to, subject, body, **kwargs))

    def sync_read(self, message_id: str) -> UnifiedMessage:
        """同步版 read。"""
        return asyncio.run(self.read(message_id))

    def sync_search(self, query: str, **kwargs) -> list[UnifiedMessage]:
        """同步版 search。"""
        return asyncio.run(self.search(query, **kwargs))
