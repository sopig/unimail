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

from .models import UnifiedMessage
from .storage.database import Database
from .storage.token_store import TokenStore
from .engine.mail_engine import MailEngine


# 默认数据目录（与 CLI/MCP Server 保持一致）
DEFAULT_DATA_DIR = Path.home() / ".unimail" / "data"


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
            data_dir: 数据目录路径。默认 ~/.unimail/data/
        """
        import os

        self._data_dir = Path(data_dir) if data_dir else DEFAULT_DATA_DIR
        self._data_dir.mkdir(parents=True, exist_ok=True)

        passphrase = passphrase or os.environ.get("UNIMAIL_PASSPHRASE", "unimail-default")

        self._db = Database(self._data_dir / "unimail.db")
        self._ts = TokenStore(self._data_dir / "tokens.enc", passphrase)
        self._engine = MailEngine(self._db, self._ts)
        self._initialized = False

    async def _ensure_init(self):
        """懒初始化：首次操作时连接所有 connector。"""
        if not self._initialized:
            await self._engine.initialize()
            self._initialized = True

    # ─── 收件 ───────────────────────────────────────────────

    async def inbox(
        self,
        limit: int = 20,
        unread: bool = False,
        account: Optional[str] = None,
    ) -> list[UnifiedMessage]:
        """获取收件箱邮件列表。"""
        await self._ensure_init()
        return await self._engine.list_messages(
            folder="inbox", limit=limit, unread_only=unread, account=account
        )

    async def read(self, message_id: str) -> UnifiedMessage:
        """读取邮件详情（自动标记已读）。"""
        await self._ensure_init()
        msg = await self._engine.get_message(message_id)
        await self._engine.mark_read(message_id)
        return msg

    async def search(
        self,
        query: str,
        limit: int = 20,
        account: Optional[str] = None,
    ) -> list[UnifiedMessage]:
        """全文搜索邮件。"""
        await self._ensure_init()
        return await self._engine.search_messages(query, limit=limit, account=account)

    # ─── 发件 ───────────────────────────────────────────────

    async def send(
        self,
        to: str | list[str],
        subject: str,
        body: str,
        cc: Optional[list[str]] = None,
        account: Optional[str] = None,
        attachments: Optional[list[str]] = None,
    ) -> dict:
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
            发送结果字典 {"message_id", "from", "to", "subject"}
        """
        if isinstance(to, str):
            to = [to]

        await self._ensure_init()
        return await self._engine.send_message(
            to=to,
            subject=subject,
            body=body,
            from_=account,
            cc=cc,
            attachments=attachments,
        )

    async def reply(
        self,
        message_id: str,
        body: str,
        reply_all: bool = False,
    ) -> dict:
        """回复邮件。"""
        await self._ensure_init()
        return await self._engine.reply_message(
            message_id=message_id, body=body, reply_all=reply_all
        )

    # ─── 操作 ───────────────────────────────────────────────

    async def archive(self, message_id: str) -> None:
        """归档邮件。"""
        await self._ensure_init()
        await self._engine.archive_messages([message_id])

    async def trash(self, message_id: str) -> None:
        """删除邮件（移到回收站）。"""
        await self._ensure_init()
        await self._engine.trash_messages([message_id])

    async def sync(self) -> int:
        """手动触发全部账户同步。返回新邮件数。"""
        await self._ensure_init()
        return await self._engine.sync_all()

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

    def _run(self, coro):
        """运行协程：兼容已有 event loop 和纯同步上下文。"""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            # 已在 async 上下文中（如 Jupyter），创建新线程
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(1) as pool:
                return pool.submit(asyncio.run, coro).result()
        else:
            return asyncio.run(coro)

    def sync_inbox(self, **kwargs) -> list[UnifiedMessage]:
        """同步版 inbox。"""
        return self._run(self.inbox(**kwargs))

    def sync_send(self, to, subject, body, **kwargs) -> dict:
        """同步版 send。"""
        return self._run(self.send(to, subject, body, **kwargs))

    def sync_read(self, message_id: str) -> UnifiedMessage:
        """同步版 read。"""
        return self._run(self.read(message_id))

    def sync_search(self, query: str, **kwargs) -> list[UnifiedMessage]:
        """同步版 search。"""
        return self._run(self.search(query, **kwargs))
