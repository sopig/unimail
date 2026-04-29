"""REST API for UniMail - FastAPI 接口，与 MCP Server 并列的接入方式。"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from .config import get_config
from .engine.mail_engine import MailEngine
from .log import get_logger
from .models import UnifiedMessage
from .storage.database import Database
from .storage.token_store import TokenStore
from .templates import get_template_engine

logger = get_logger(__name__)

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
        "",
        description="邮件正文（Markdown 格式，自动转 HTML）。使用 template 时可留空。",
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
    template: Optional[str] = Field(
        None,
        description="邮件模板名称（使用后 body 作为纯文本 fallback）",
        json_schema_extra={"example": "welcome.html"},
    )
    template_context: Optional[dict] = Field(
        None,
        description="模板渲染上下文变量",
        json_schema_extra={"example": {"name": "Alice", "message": "Welcome aboard!"}},
    )

    model_config = {"populate_by_name": True}


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

    model_config = {"populate_by_name": True}


class ReplyResult(BaseModel):
    """回复结果 - 回复邮件成功后返回"""
    message_id: str = Field(description="回复邮件的消息 ID")
    from_: str = Field(alias="from", description="发件人邮箱地址")
    to: list[str] = Field(description="回复的收件人列表")

    model_config = {"populate_by_name": True}


class AccountInfo(BaseModel):
    """已连接的邮箱账户信息"""
    id: str = Field(description="账户唯一 ID")
    provider: str = Field(description="邮箱提供商: gmail/outlook/imap")
    email: str = Field(description="邮箱地址")
    display_name: str = Field(description="显示名称")
    is_default: bool = Field(description="是否为默认发件账户")


class ArchiveResult(BaseModel):
    """归档结果"""
    message_id: str = Field(description="被归档的邮件 ID")
    status: str = Field(description="操作状态: archived")


class ErrorResponse(BaseModel):
    """错误响应"""
    detail: str = Field(description="错误描述信息")


class TokenRequest(BaseModel):
    """JWT token 请求"""
    password: str = Field(description="Master password (UNIMAIL_API_TOKEN)")
    sub: str = Field(default="default", description="用户 ID (subject)")
    scope: str = Field(default="read,write", description="权限范围: read/write/admin")
    expire_hours: Optional[int] = Field(None, description="Token 有效期（小时），默认从配置读取")


class TokenResponse(BaseModel):
    """JWT token 响应"""
    access_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds
    scope: str


class WebhookRequest(BaseModel):
    """Webhook 注册请求"""
    url: str = Field(description="Webhook 回调 URL")
    events: list[str] = Field(
        default_factory=lambda: ["new_message"],
        description="订阅的事件类型",
    )


class WebhookResponse(BaseModel):
    """Webhook 注册响应"""
    id: str
    url: str
    events: list[str]
    created_at: str


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

    config = get_config()
    data_dir = Path.home() / ".unimail" / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    # If没有传入 engine，则自行创建
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
            "JWT authentication, webhook notifications, and email templates."
        ),
        version="0.2.0",
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

    api_token = config.security.api_token or os.environ.get("UNIMAIL_API_TOKEN")
    jwt_secret = config.security.jwt_secret or os.environ.get("UNIMAIL_JWT_SECRET")

    async def verify_token(request: Request) -> Optional[dict]:
        """Dual-mode authentication: JWT (preferred) + Bearer token (fallback).

        Returns:
            None if no auth required, or dict with user info from JWT payload.
        """
        if not api_token and not jwt_secret:
            return None

        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            if api_token or jwt_secret:
                raise HTTPException(status_code=401, detail="Missing Bearer token")
            return None

        token = auth_header[7:]

        # Try JWT first if secret is configured
        if jwt_secret:
            try:
                import jwt
                payload = jwt.decode(token, jwt_secret, algorithms=["HS256"])
                logger.debug(f"JWT auth passed for sub={payload.get('sub')}")
                return payload
            except jwt.ExpiredSignatureError:
                raise HTTPException(status_code=401, detail="Token expired")
            except jwt.InvalidTokenError:
                # Fall through to simple token check
                pass

        # Fallback: simple Bearer token comparison
        if api_token and token == api_token:
            logger.debug("Bearer token auth passed")
            return {"sub": "api_token_user", "scope": "read,write,admin"}

        raise HTTPException(status_code=403, detail="Invalid token")

    # === 辅助函数 ===

    def get_engine(request: Request) -> MailEngine:
        if not hasattr(request.app.state, "engine"):
            db = Database(data_dir / "unimail.db")
            token_store = TokenStore(data_dir / "tokens.enc", passphrase)
            eng = MailEngine(db, token_store)
            request.app.state.engine = eng
            request.app.state.db = db
        return request.app.state.engine

    def get_db(request: Request) -> Database:
        if not hasattr(request.app.state, "db"):
            get_engine(request)
        return request.app.state.db

    # === Auth Routes ===

    @app.post(
        "/api/auth/token",
        response_model=TokenResponse,
        summary="生成 JWT Token",
        description="使用 master password 生成 JWT access token。需要 UNIMAIL_JWT_SECRET 环境变量已设置。",
    )
    async def create_token(body: TokenRequest):
        """Generate a JWT token for API authentication."""
        if not jwt_secret:
            raise HTTPException(
                status_code=501,
                detail="JWT not configured. Set UNIMAIL_JWT_SECRET environment variable.",
            )

        # Verify master password
        if not api_token:
            raise HTTPException(
                status_code=501,
                detail="Master password not set. Set UNIMAIL_API_TOKEN environment variable.",
            )
        if body.password != api_token:
            logger.warning("JWT token generation failed: invalid password")
            raise HTTPException(status_code=403, detail="Invalid password")

        import jwt

        expire_hours = body.expire_hours or config.security.jwt_expire_hours
        expires_at = datetime.now(timezone.utc) + timedelta(hours=expire_hours)

        payload = {
            "sub": body.sub,
            "scope": body.scope,
            "exp": expires_at,
            "iat": datetime.now(timezone.utc),
        }

        access_token = jwt.encode(payload, jwt_secret, algorithm="HS256")
        logger.info(f"JWT token generated for sub={body.sub}, scope={body.scope}")

        return TokenResponse(
            access_token=access_token,
            token_type="bearer",
            expires_in=expire_hours * 3600,
            scope=body.scope,
        )

    # === Mail Routes ===

    @app.get(
        "/api/mail",
        response_model=list[dict],
        summary="列出邮件",
        description="列出收件箱或其他文件夹中的邮件。",
        dependencies=[Depends(verify_token)],
    )
    async def list_mail(
        request: Request,
        folder: str = Query("inbox", description="邮件文件夹"),
        limit: int = Query(20, ge=1, le=50, description="返回的最大邮件数量"),
        unread_only: bool = Query(False, description="只返回未读邮件"),
        account: Optional[str] = Query(None, description="按邮箱地址过滤"),
    ):
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
        description="通过关键词搜索邮件。",
        dependencies=[Depends(verify_token)],
    )
    async def search_mail(
        request: Request,
        q: str = Query(..., description="搜索关键词"),
        account: Optional[str] = Query(None, description="限定搜索范围"),
        limit: int = Query(10, ge=1, le=50, description="返回的最大结果数"),
    ):
        eng = get_engine(request)
        try:
            messages = await eng.search_messages(query=q, account=account, limit=limit)
            return [_serialize_message(m) for m in messages]
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get(
        "/api/mail/{message_id}",
        summary="读取邮件详情",
        description="读取一封邮件的完整内容。",
        dependencies=[Depends(verify_token)],
    )
    async def read_mail(request: Request, message_id: str):
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
        description="发送一封新邮件。支持 Markdown 正文和模板。",
        dependencies=[Depends(verify_token)],
    )
    async def send_mail(request: Request, body: SendRequest):
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
                template=body.template,
                template_context=body.template_context,
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
        eng = get_engine(request)
        try:
            msg = await eng.get_message(message_id)
            attachment = None
            for att in msg.attachments:
                if att.filename == filename:
                    attachment = att
                    break

            if not attachment:
                raise HTTPException(status_code=404, detail=f"Attachment not found: {filename}")

            connector = eng._get_connector(msg.account_id)
            content, _ = await connector.download_attachment(msg.external_id, attachment.id)

            return Response(
                content=content,
                media_type=attachment.mime_type or "application/octet-stream",
                headers={"Content-Disposition": f'attachment; filename="{filename}"'},
            )
        except HTTPException:
            raise
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    # === Webhook Routes ===

    @app.post(
        "/api/webhooks",
        response_model=WebhookResponse,
        summary="注册 Webhook",
        description="注册一个新的 webhook URL，在新邮件到达时接收通知。",
        dependencies=[Depends(verify_token)],
    )
    async def register_webhook(request: Request, body: WebhookRequest):
        eng = get_engine(request)
        registration = eng.webhook_manager.register(url=body.url, events=body.events)
        return WebhookResponse(
            id=registration.id,
            url=registration.url,
            events=registration.events,
            created_at=registration.created_at,
        )

    @app.get(
        "/api/webhooks",
        response_model=list[WebhookResponse],
        summary="列出 Webhooks",
        description="列出所有已注册的 webhook。",
        dependencies=[Depends(verify_token)],
    )
    async def list_webhooks(request: Request):
        eng = get_engine(request)
        webhooks = eng.webhook_manager.list_webhooks()
        return [
            WebhookResponse(
                id=wh.id,
                url=wh.url,
                events=wh.events,
                created_at=wh.created_at,
            )
            for wh in webhooks
        ]

    @app.delete(
        "/api/webhooks/{webhook_id}",
        summary="删除 Webhook",
        description="删除一个已注册的 webhook。",
        dependencies=[Depends(verify_token)],
    )
    async def delete_webhook(request: Request, webhook_id: str):
        eng = get_engine(request)
        if not eng.webhook_manager.unregister(webhook_id):
            raise HTTPException(status_code=404, detail=f"Webhook not found: {webhook_id}")
        return {"status": "deleted", "id": webhook_id}

    # === Template Routes ===

    @app.get(
        "/api/templates",
        summary="列出邮件模板",
        description="列出所有可用的邮件模板。",
        dependencies=[Depends(verify_token)],
    )
    async def list_templates():
        engine = get_template_engine()
        templates = engine.list_templates()
        return [{"name": t, "exists": True} for t in templates]

    return app


# === 序列化辅助 ===


def _serialize_message(msg: UnifiedMessage, detail: bool = False) -> dict:
    """将 UnifiedMessage 序列化为 JSON 友好的字典。"""
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
