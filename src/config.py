"""Configuration management for UniMail.

Supports layered config: environment variables > config.toml > defaults.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

if sys.version_info >= (3, 11):
    import tomllib
else:
    try:
        import tomllib  # type: ignore[import]
    except ImportError:
        import tomli as tomllib  # type: ignore[import,no-redef]


CONFIG_DIR = Path.home() / ".unimail"
CONFIG_FILE = CONFIG_DIR / "config.toml"


@dataclass
class ServerConfig:
    port: int = 8765
    mode: str = "all"  # mcp | api | all


@dataclass
class SecurityConfig:
    api_token: str = ""
    jwt_secret: str = ""
    jwt_expire_hours: int = 24
    cors_origins: list[str] = field(default_factory=lambda: ["http://localhost:*", "http://127.0.0.1:*"])


@dataclass
class RateLimitConfig:
    default_daily: int = 50


@dataclass
class CacheConfig:
    enabled: bool = True
    inbox_ttl: int = 60  # seconds
    message_ttl: int = 300  # seconds


@dataclass
class ImapPoolConfig:
    connection_timeout: int = 30  # seconds
    keepalive: bool = True


@dataclass
class SyncConfig:
    enabled: bool = True
    interval: int = 300  # seconds between periodic syncs (default 5 min)


@dataclass
class LoggingConfig:
    level: str = "INFO"
    format: str = "json"  # json | console


@dataclass
class WebhookEntry:
    id: str = ""
    url: str = ""
    events: list[str] = field(default_factory=lambda: ["new_message"])


@dataclass
class UniMailConfig:
    server: ServerConfig = field(default_factory=ServerConfig)
    security: SecurityConfig = field(default_factory=SecurityConfig)
    rate_limit: RateLimitConfig = field(default_factory=RateLimitConfig)
    cache: CacheConfig = field(default_factory=CacheConfig)
    imap: ImapPoolConfig = field(default_factory=ImapPoolConfig)
    sync: SyncConfig = field(default_factory=SyncConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    webhooks: list[WebhookEntry] = field(default_factory=list)


# Singleton
_config_instance: Optional[UniMailConfig] = None


def _load_toml() -> dict:
    """Load config.toml if it exists."""
    if not CONFIG_FILE.exists():
        return {}
    with open(CONFIG_FILE, "rb") as f:
        return tomllib.load(f)


def _env_override(config: UniMailConfig) -> None:
    """Override config values with environment variables."""
    # Server
    if v := os.environ.get("UNIMAIL_PORT"):
        config.server.port = int(v)
    if v := os.environ.get("UNIMAIL_MODE"):
        config.server.mode = v

    # Security
    if v := os.environ.get("UNIMAIL_API_TOKEN"):
        config.security.api_token = v
    if v := os.environ.get("UNIMAIL_JWT_SECRET"):
        config.security.jwt_secret = v
    if v := os.environ.get("UNIMAIL_JWT_EXPIRE_HOURS"):
        config.security.jwt_expire_hours = int(v)
    if v := os.environ.get("UNIMAIL_CORS_ORIGINS"):
        config.security.cors_origins = [o.strip() for o in v.split(",")]

    # Rate limit
    if v := os.environ.get("UNIMAIL_RATE_LIMIT_DAILY"):
        config.rate_limit.default_daily = int(v)

    # Cache
    if v := os.environ.get("UNIMAIL_CACHE_ENABLED"):
        config.cache.enabled = v.lower() in ("true", "1", "yes")
    if v := os.environ.get("UNIMAIL_CACHE_INBOX_TTL"):
        config.cache.inbox_ttl = int(v)
    if v := os.environ.get("UNIMAIL_CACHE_MESSAGE_TTL"):
        config.cache.message_ttl = int(v)

    # IMAP
    if v := os.environ.get("UNIMAIL_IMAP_TIMEOUT"):
        config.imap.connection_timeout = int(v)
    if v := os.environ.get("UNIMAIL_IMAP_KEEPALIVE"):
        config.imap.keepalive = v.lower() in ("true", "1", "yes")

    # Sync
    if v := os.environ.get("UNIMAIL_SYNC_ENABLED"):
        config.sync.enabled = v.lower() in ("true", "1", "yes")
    if v := os.environ.get("UNIMAIL_SYNC_INTERVAL"):
        config.sync.interval = int(v)

    # Logging
    if v := os.environ.get("UNIMAIL_LOG_LEVEL"):
        config.logging.level = v.upper()
    if v := os.environ.get("UNIMAIL_LOG_FORMAT"):
        config.logging.format = v


def get_config(reload: bool = False) -> UniMailConfig:
    """Get the singleton config instance.

    Config priority: environment variables > config.toml > defaults.
    """
    global _config_instance
    if _config_instance is not None and not reload:
        return _config_instance

    data = _load_toml()
    config = UniMailConfig()

    # Apply TOML values
    if "server" in data:
        s = data["server"]
        if "port" in s:
            config.server.port = s["port"]
        if "mode" in s:
            config.server.mode = s["mode"]

    if "security" in data:
        s = data["security"]
        if "api_token" in s:
            config.security.api_token = s["api_token"]
        if "jwt_secret" in s:
            config.security.jwt_secret = s["jwt_secret"]
        if "jwt_expire_hours" in s:
            config.security.jwt_expire_hours = s["jwt_expire_hours"]
        if "cors_origins" in s:
            config.security.cors_origins = s["cors_origins"]

    if "rate_limit" in data:
        s = data["rate_limit"]
        if "default_daily" in s:
            config.rate_limit.default_daily = s["default_daily"]

    if "cache" in data:
        s = data["cache"]
        if "enabled" in s:
            config.cache.enabled = s["enabled"]
        if "inbox_ttl" in s:
            config.cache.inbox_ttl = s["inbox_ttl"]
        if "message_ttl" in s:
            config.cache.message_ttl = s["message_ttl"]

    if "imap" in data:
        s = data["imap"]
        if "connection_timeout" in s:
            config.imap.connection_timeout = s["connection_timeout"]
        if "keepalive" in s:
            config.imap.keepalive = s["keepalive"]

    if "sync" in data:
        s = data["sync"]
        if "enabled" in s:
            config.sync.enabled = s["enabled"]
        if "interval" in s:
            config.sync.interval = s["interval"]

    if "logging" in data:
        s = data["logging"]
        if "level" in s:
            config.logging.level = s["level"]
        if "format" in s:
            config.logging.format = s["format"]

    # Webhooks
    if "webhooks" in data:
        for wh in data["webhooks"]:
            config.webhooks.append(WebhookEntry(
                id=wh.get("id", ""),
                url=wh.get("url", ""),
                events=wh.get("events", ["new_message"]),
            ))

    # Environment overrides (highest priority)
    _env_override(config)

    _config_instance = config
    return config


def get_config_dir() -> Path:
    """Return the config directory, creating it if needed."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    return CONFIG_DIR
