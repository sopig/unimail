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
    to: list[str] = Field(
        ...,
        description="收件人邮箱列表",
        json_schema_extra={"example": ["user@example.com"]},
    )
    subject: str = Field(
        ...,
        description="邮件主题",
        json_schema_extra={"example": "Meeting Tomorrow"},
    )
    body: str = Field(
        ...,
        description="邮件正文（Markdown 格式，自动转 HTML）",
        json_schema_extra={"example": "# Hello\n\nLet's meet at **3pm**."},
    )
    from_: Optional[str] = Field(
        None,
        alias="from",
        description="发件邮箱地址，不指定则使用默认账号",
        json_schema_extra={"example": "me@gmail.com"},
    )
    cc: list[str] = Field(
        default_factory=list,
        description="抄送收件人列表",
        json_schema_extra={"example": ["cc@example.com"]},
    )
    bcc: list[str] = Field(
        default_factory=list,
        description="密送收件人列表",
        json_schema_extra={"example": []},
    )
    attachments: list[str] = Field(
        default_factory=list,
        description="附件本地文件路径列表",
        json_schema_extra={"example": ["/tmp/report.pdf"]},
    )

    model_config = {"populate_by_name": True, "json_schema_extra": {
        "example": {
            "to": ["user@example.com"],
            "subject": "Meeting Tomorrow",
            "body": "# Hello\n\nLet's meet at **3pm**.",
            "cc": [],
            "bcc": [],
            "attachments": [],
        }
    }}


class ReplyRequest(BaseModel):
    """回复邮件请求体"""
    body: str = Field(
        ...,
        description="回复内容（Markdown 格式）",
        json_schema_extra={"example": "Thanks, I'll be there!"},
    )
    reply_all: bool = Field(
        False,
        description="是否回复所有人（包括 To 和 Cc 中的所有收件人）",
    )

    model_config = {"json_schema_extra": {
        "example": {"body": "Thanks, I'll be there!", "reply_all": False}
    }}


class SendResult(BaseModel):
    """发送结果 - 邮件发送成功后返回"""
    message_id: str = Field(description="新邮件的唯一消息 ID")
    from_: str = Field(alias="from", description="实际发件人邮箱地址")
    to: list[str] = Field(description="收件人列表")
    subject: str = Field(description="邮件主题")

    model_config = {"populate_by_name": True, "json_schema_extra": {
        "example": {
            "message_id": "gmail_abc123",
            "from": "me@gmail.com",
            "to": ["user@example.com"],
            "subject": "Meeting Tomorrow",
        }
    }}


class ReplyResult(BaseModel):
    """回复结果 - 回复邮件成功后返回"""
    message_id: str = Field(description="回复邮件的消息 ID")
    from_: str = Field(alias="from", description="发件人邮箱地址")
    to: list[str] = Field(description="回复的收件人列表")

    model_config = {"populate_by_name": True, "json_schema_extra": {
        "example": {
            "message_id": "gmail_reply_456",
            "from": "me@gmail.com",
            "to": ["sender@example.com"],
        }
    }}


class AccountInfo(BaseModel):
    """已连接的邮箱账户信息"""
    id: str = Field(description="账户唯一 ID")
    provider: str = Field(description="邮箱提供商: gmail/outlook/imap")
    email: str = Field(description="邮箱地址")
    display_name: str = Field(description="显示名称")
    is_default: bool = Field(description="是否为默认发件账户")

    model_config = {"json_schema_extra": {
        "example": {
            "id": "a1b2c3d4",
            "provider": "gmail",
            "email": "user@gmail.com",
            "display_name": "user",
            "is_default": True,
        }
    }}


class ArchiveResult(BaseModel):
    """归档结果"""
    message_id: str = Field(description="被归档的邮件 ID")
    status: str = Field(description="操作状态: archived")

    model_config = {"json_schema_extra": {
        "example": {"message_id": "gmail_abc123", "status": "archived"}
    }}


class ErrorResponse(BaseModel):
    """错误响应"""
    detail: str = Field(description="错误描述信息")


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
        title="UniMail",
        description=(
            "Unified email gateway for AI agents. "
            "Provides a single REST API to read, send, search, and manage emails "
            "across multiple providers (Gmail, Outlook, IMAP/SMTP). "
            "Supports Markdown email bodies with automatic HTML conversion, "
            "multi-account management, and attachment handling."
        ),
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
        if not hasattr(request.app.state, "engine"):
            # Fallback: lifespan 未触发时（如测试环境），同步初始化
            db = Database(data_dir / "unimail.db")
            token_store = TokenStore(data_dir / "tokens.enc", passphrase)
            eng = MailEngine(db, token_store)
            request.app.state.engine = eng
            request.app.state.db = db
        return request.app.state.engine

    def get_db(request: Request) -> Database:
        if not hasattr(request.app.state, "db"):
            get_engine(request)  # triggers init
        return request.app.state.db

    # === 路由 ===

    @app.get(
        "/api/mail",
        response_model=list[dict],
        summary="列出邮件",
        description="列出收件箱或其他文件夹中的邮件，返回最近的邮件摘要列表（含发件人、主题、时间、已读状态）。AI Agent 调用此端点获取邮件概览。",
        dependencies=[Depends(verify_token)],
    )
    async def list_mail(
        request: Request,
        folder: str = Query(
            "inbox",
            description="邮件文件夹，可选值: inbox(收件箱)/sent(已发送)/drafts(草稿)/archive(归档)/all(全部)",
            examples=["inbox", "sent", "archive"],
        ),
        limit: int = Query(
            20, ge=1, le=50,
            description="返回的最大邮件数量（1-50）",
            examples=[20],
        ),
        unread_only: bool = Query(
            False,
            description="设为 true 时只返回未读邮件",
            examples=[False],
        ),
        account: Optional[str] = Query(
            None,
            description="按邮箱地址过滤，只查询指定账户的邮件",
            examples=["user@gmail.com"],
        ),
    ):
        """列出邮件。返回收件箱/已发送/所有邮件的摘要信息（发件人、主题、时间、已读状态）。"""
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
        description="通过关键词搜索邮件。搜索范围覆盖主题、正文、发件人。返回匹配的邮件摘要列表，按时间倒序排列。",
        dependencies=[Depends(verify_token)],
    )
    async def search_mail(
        request: Request,
        q: str = Query(
            ...,
            description="搜索关键词，匹配邮件主题、正文和发件人",
            examples=["invoice", "meeting notes"],
        ),
        account: Optional[str] = Query(
            None,
            description="限定搜索范围到指定邮箱账户",
            examples=["user@gmail.com"],
        ),
        limit: int = Query(
            10, ge=1, le=50,
            description="返回的最大结果数（1-50）",
            examples=[10],
        ),
    ):
        """搜索邮件。支持关键词匹配主题、正文和发件人。"""
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
        description="读取一封邮件的完整内容，包括正文文本、HTML、附件列表。调用后自动标记为已读。message_id 从 /api/mail 或 /api/mail/search 结果中获取。",
        dependencies=[Depends(verify_token)],
    )
    async def read_mail(request: Request, message_id: str):
        """读取邮件完整内容，包括正文和附件列表。自动标记为已读。"""
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
        description="发送一封新邮件。正文使用 Markdown 格式编写，系统自动转为 HTML。支持多收件人、抄送、密送和附件。如不指定发件账号则使用默认账户。",
        dependencies=[Depends(verify_token)],
    )
    async def send_mail(request: Request, body: SendRequest):
        """发送邮件。正文为 Markdown 格式（自动转 HTML），支持附件。"""
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
        description="回复一封邮件。自动使用原始收件账号作为发件人，引用原始主题。支持回复所有人（reply_all=true 时同时回复 To 和 Cc 中的所有人）。",
        dependencies=[Depends(verify_token)],
    )
    async def reply_mail(request: Request, message_id: str, body: ReplyRequest):
        """回复邮件（自动使用原账号、引用原主题）。"""
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
        description="列出所有已连接的邮箱账户及其状态。返回每个账户的提供商类型、邮箱地址、显示名称，以及哪个是默认发件账户。",
        dependencies=[Depends(verify_token)],
    )
    async def list_accounts(request: Request):
        """列出所有已连接的邮箱账户。"""
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
        description="将指定邮件移至归档文件夹。归档后邮件不再出现在收件箱中，但仍可通过搜索或 folder=archive 访问。",
        dependencies=[Depends(verify_token)],
    )
    async def archive_mail(request: Request, message_id: str):
        """归档邮件 - 移出收件箱但保留可搜索。"""
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
        description="下载邮件中的附件文件。通过 filename 精确匹配附件名称，返回二进制文件内容。filename 可从邮件详情的 attachments 列表中获取。",
        dependencies=[Depends(verify_token)],
    )
    async def download_attachment(request: Request, message_id: str, filename: str):
        """下载邮件附件。通过 filename 匹配，返回文件内容。"""
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
