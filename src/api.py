"""REST API for UniMail - FastAPI 接口，与 MCP Server 并列的接入方式。"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from .engine.mail_engine import MailEngine
from .models import UnifiedMessage
from .storage.database import Database
from .storage.token_store import TokenStore

# === 请求/响应模型 ===


class SendRequest(BaseModel):
    """发送邮件请求体"""
    to: list[str] = Field(..., description="收件人邮箱列表")
    subject: str = Field(..., description="邮件主题")
    body: str = Field(..., description="邮件正文（Markdown 格式，自动转 HTML）")
    from_: Optional[str] = Field(None, alias="from", description="发件邮箱地址，不指定用默认账号")
    cc: list[str] = Field(default_factory=list, description="抄送")
    bcc: list[str] = Field(default_factory=list, description="密送")
    attachments: list[str] = Field(default_factory=list, description="附件本地文件路径列表")

    class Config:
        populate_by_name = True


class ReplyRequest(BaseModel):
    """回复邮件请求体"""
    body: str = Field(..., description="回复内容（Markdown）")
    reply_all: bool = Field(False, description="是否回复所有人")


class SendResult(BaseModel):
    """发送结果"""
    message_id: str
    from_: str = Field(alias="from")
    to: list[str]
    subject: str

    class Config:
        populate_by_name = True


class ReplyResult(BaseModel):
    """回复结果"""
    message_id: str
    from_: str = Field(alias="from")
    to: list[str]

    class Config:
        populate_by_name = True


class AccountInfo(BaseModel):
    """账户信息"""
    id: str
    provider: str
    email: str
    display_name: str
    is_default: bool


class ArchiveResult(BaseModel):
    """归档结果"""
    message_id: str
    status: str


class ErrorResponse(BaseModel):
    """错误响应"""
    detail: str


# === 应用工厂 ===


def create_app(
    passphrase: str = "unimail-default",
    engine: Optional[MailEngine] = None,
) -> FastAPI:
    """
    创建 FastAPI 应用实例。

    Args:
        passphrase: Token 加密密码
        engine: 可选，传入已有的 MailEngine 实例（用于 mode=all 时共享）
    """
    from pathlib import Path

    data_dir = Path.home() / ".unimail" / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    # 如果没有传入 engine，则自行创建
    _own_engine = engine is None

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """应用生命周期管理：启动时初始化引擎，关闭时清理"""
        nonlocal engine
        if _own_engine:
            db = Database(data_dir / "unimail.db")
            token_store = TokenStore(data_dir / "tokens.enc", passphrase)
            engine = MailEngine(db, token_store)
            await engine.initialize()
            app.state.engine = engine
            app.state.db = db
        else:
            app.state.engine = engine
            # 从 engine 中获取 db
            app.state.db = engine.db  # type: ignore
        yield
        if _own_engine and engine:
            await engine.shutdown()

    app = FastAPI(
        title="UniMail API",
        description="📮 Unified email gateway for AI agents — REST API",
        version="0.1.0",
        lifespan=lifespan,
    )

    # CORS 中间件
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # === 认证依赖 ===

    api_token = os.environ.get("UNIMAIL_API_TOKEN")

    async def verify_token(request: Request) -> None:
        """Bearer token 认证，UNIMAIL_API_TOKEN 未设置则跳过鉴权"""
        if not api_token:
            return
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Missing Bearer token")
        token = auth_header[7:]
        if token != api_token:
            raise HTTPException(status_code=403, detail="Invalid token")

    # === 辅助函数 ===

    def get_engine(request: Request) -> MailEngine:
        return request.app.state.engine

    def get_db(request: Request) -> Database:
        return request.app.state.db

    # === 路由 ===

    @app.get(
        "/api/mail",
        response_model=list[dict],
        summary="查看邮件列表",
        dependencies=[Depends(verify_token)],
    )
    async def list_mail(
        request: Request,
        folder: str = Query("inbox", description="文件夹: inbox/sent/drafts/archive/all"),
        limit: int = Query(20, ge=1, le=50, description="返回数量"),
        unread_only: bool = Query(False, description="只看未读"),
        account: Optional[str] = Query(None, description="邮箱地址过滤"),
    ):
        """查看邮件列表。返回收件箱/已发送/所有邮件的摘要信息。"""
        eng = get_engine(request)
        try:
            messages = await eng.list_messages(
                account=account,
                folder=folder,
                limit=limit,
                unread_only=unread_only,
            )
            return [_serialize_message(m) for m in messages]
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get(
        "/api/mail/search",
        response_model=list[dict],
        summary="搜索邮件",
        dependencies=[Depends(verify_token)],
    )
    async def search_mail(
        request: Request,
        q: str = Query(..., description="搜索关键词"),
        account: Optional[str] = Query(None, description="限定搜索的账户"),
        limit: int = Query(10, ge=1, le=50, description="返回数量"),
    ):
        """搜索邮件。支持关键词、发件人、日期范围等条件。"""
        eng = get_engine(request)
        try:
            messages = await eng.search_messages(
                query=q,
                account=account,
                limit=limit,
            )
            return [_serialize_message(m) for m in messages]
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get(
        "/api/mail/{message_id}",
        summary="读取邮件详情",
        dependencies=[Depends(verify_token)],
    )
    async def read_mail(request: Request, message_id: str):
        """读取邮件完整内容，包括正文和附件列表。"""
        eng = get_engine(request)
        try:
            msg = await eng.get_message(message_id)
            await eng.mark_read(message_id)
            return _serialize_message(msg, detail=True)
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.post(
        "/api/mail/send",
        response_model=SendResult,
        summary="发送邮件",
        dependencies=[Depends(verify_token)],
    )
    async def send_mail(request: Request, body: SendRequest):
        """发送邮件。支持指定发件账号、附件、Markdown 正文（自动转 HTML）。"""
        eng = get_engine(request)
        try:
            result = await eng.send_message(
                to=body.to,
                subject=body.subject,
                body=body.body,
                from_=body.from_,
                cc=body.cc or None,
                bcc=body.bcc or None,
                attachments=body.attachments or None,
            )
            return SendResult(
                message_id=result["message_id"],
                **{"from": result["from"]},
                to=result["to"],
                subject=result["subject"],
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.post(
        "/api/mail/{message_id}/reply",
        response_model=ReplyResult,
        summary="回复邮件",
        dependencies=[Depends(verify_token)],
    )
    async def reply_mail(request: Request, message_id: str, body: ReplyRequest):
        """回复一封邮件（自动使用原账号、引用原主题）。"""
        eng = get_engine(request)
        try:
            result = await eng.reply_message(
                message_id=message_id,
                body=body.body,
                reply_all=body.reply_all,
            )
            return ReplyResult(
                message_id=result["message_id"],
                **{"from": result["from"]},
                to=result["to"],
            )
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get(
        "/api/accounts",
        response_model=list[AccountInfo],
        summary="查看已连接账户",
        dependencies=[Depends(verify_token)],
    )
    async def list_accounts(request: Request):
        """查看已连接的邮箱账户列表及状态。"""
        db = get_db(request)
        accounts = db.get_accounts()
        return [
            AccountInfo(
                id=a.id,
                provider=a.provider.value,
                email=a.email,
                display_name=a.display_name,
                is_default=a.is_default,
            )
            for a in accounts
        ]

    @app.post(
        "/api/mail/{message_id}/archive",
        response_model=ArchiveResult,
        summary="归档邮件",
        dependencies=[Depends(verify_token)],
    )
    async def archive_mail(request: Request, message_id: str):
        """将邮件归档。"""
        eng = get_engine(request)
        try:
            await eng.archive_messages([message_id])
            return ArchiveResult(message_id=message_id, status="archived")
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get(
        "/api/mail/{message_id}/attachments/{filename}",
        summary="下载附件",
        dependencies=[Depends(verify_token)],
    )
    async def download_attachment(request: Request, message_id: str, filename: str):
        """下载邮件附件。通过 filename 匹配附件并返回文件内容。"""
        eng = get_engine(request)
        try:
            # 先获取邮件，找到附件 ID
            msg = await eng.get_message(message_id)
            attachment = None
            for att in msg.attachments:
                if att.filename == filename:
                    attachment = att
                    break

            if not attachment:
                raise HTTPException(
                    status_code=404,
                    detail=f"Attachment not found: {filename}",
                )

            # 获取 connector 下载附件内容
            connector = eng._get_connector(msg.account_id)
            content, _ = await connector.download_attachment(
                msg.external_id, attachment.id
            )

            return Response(
                content=content,
                media_type=attachment.mime_type or "application/octet-stream",
                headers={
                    "Content-Disposition": f'attachment; filename="{filename}"',
                },
            )
        except HTTPException:
            raise
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    return app


# === 序列化辅助 ===


def _serialize_message(msg: UnifiedMessage, detail: bool = False) -> dict:
    """将 UnifiedMessage 序列化为 JSON 友好的字典。

    Args:
        msg: 邮件消息对象
        detail: 是否包含完整正文（列表模式下省略）
    """
    data = {
        "id": msg.id,
        "account_id": msg.account_id,
        "thread_id": msg.thread_id,
        "folder": msg.folder,
        "from": {
            "name": msg.from_contact.name,
            "email": msg.from_contact.email,
        },
        "to": [{"name": c.name, "email": c.email} for c in msg.to],
        "cc": [{"name": c.name, "email": c.email} for c in msg.cc],
        "subject": msg.subject,
        "snippet": msg.snippet,
        "received_at": msg.received_at.isoformat(),
        "is_read": msg.is_read,
        "is_starred": msg.is_starred,
        "labels": msg.labels,
        "attachments": [
            {
                "id": a.id,
                "filename": a.filename,
                "mime_type": a.mime_type,
                "size": a.size,
            }
            for a in msg.attachments
        ],
    }
    if detail:
        data["body_text"] = msg.body_text
        data["body_html"] = msg.body_html
    return data
