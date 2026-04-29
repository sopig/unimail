# 📮 UniMail - Unified Email Gateway for AI Agents

A self-hosted MCP (Model Context Protocol) server that gives AI agents the ability to read, send, search, and manage emails across multiple providers.

## Features

- **Multi-provider**: Gmail (REST API) + Outlook/Hotmail (Graph API) + IMAP/SMTP (163, QQ, Yahoo, etc.)
- **Unified API**: One set of tools, transparent to the AI agent regardless of provider
- **MCP Server**: Standard MCP protocol, compatible with Claude, OpenClaw, and any MCP client
- **Secure**: Tokens encrypted at rest (Fernet/AES), daily send limits, audit logging
- **CLI**: Simple account management from the command line
- **Local-first**: SQLite cache, no cloud dependency, full data ownership

## Quick Start

### 1. Install

```bash
cd unimail
pip install -e .
```

### 2. Add Accounts

```bash
# 163 邮箱 (最简单，只需授权码)
unimail add 163 your@163.com --password YOUR_AUTH_CODE

# QQ 邮箱
unimail add qq 12345@qq.com --password YOUR_AUTH_CODE

# Gmail (需要 Google Cloud OAuth credentials)
unimail add gmail --client-id YOUR_ID --client-secret YOUR_SECRET

# Outlook/Hotmail (需要 Azure AD 注册应用)
unimail add outlook --client-id YOUR_ID --client-secret YOUR_SECRET

# 任意 IMAP 邮箱
unimail add imap your@email.com --password AUTH_CODE --imap-host imap.example.com --smtp-host smtp.example.com
```

### 3. Test

```bash
unimail test your@163.com
unimail list
```

### 4. Run MCP Server

```bash
# stdio (recommended for OpenClaw integration)
unimail serve

# Or manually test sync
unimail sync
```

### 5. Register in OpenClaw

Add to your OpenClaw MCP config:

```json
{
  "mcpServers": {
    "unimail": {
      "command": "unimail",
      "args": ["serve"],
      "transport": "stdio"
    }
  }
}
```

## MCP Tools

| Tool | Description |
|------|-------------|
| `mail_list` | 查看邮件列表（收件箱/已发送/全部） |
| `mail_read` | 读取邮件完整内容 |
| `mail_send` | 发送邮件（支持 Markdown、附件） |
| `mail_reply` | 回复邮件（自动使用原账号） |
| `mail_search` | 搜索邮件（关键词、发件人、日期） |
| `mail_accounts` | 查看已连接账户 |
| `mail_archive` | 归档/删除邮件 |
| `mail_attachment` | 下载附件 |

## Architecture

```
AI Agent ←→ MCP Protocol ←→ UniMail Server
                                   │
                    ┌───────────────┼───────────────┐
                    │               │               │
              Gmail API      Graph API        IMAP/SMTP
              (OAuth2)       (OAuth2)         (AuthCode)
                    │               │               │
                Gmail          Outlook        163/QQ/Yahoo
```

## Security

- **Token encryption**: All OAuth tokens and passwords encrypted with Fernet (PBKDF2 key derivation)
- **Send limits**: Default 50 emails/day per account
- **Audit log**: Every send operation logged to SQLite
- **Minimal permissions**: Only `mail.modify` + `mail.send` scopes requested
- **Local-first**: No data leaves your machine

## OAuth Setup

### Gmail

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a project → Enable Gmail API
3. Create OAuth 2.0 credentials (Desktop app type)
4. Add `http://localhost:9876/callback` as redirect URI
5. Use the Client ID and Secret with `unimail add gmail`

### Outlook/Hotmail

1. Go to [Azure Portal](https://portal.azure.com/) → App registrations
2. New registration → Accounts in any org + personal Microsoft accounts
3. Add `http://localhost:9876/callback` as redirect URI (Web platform)
4. Create a client secret
5. Add API permissions: `Mail.ReadWrite`, `Mail.Send`
6. Use the Client ID and Secret with `unimail add outlook`

### 163/QQ (IMAP)

1. Login to your email web interface
2. Settings → POP3/SMTP/IMAP → Enable IMAP
3. Generate authorization code (授权码)
4. Use: `unimail add 163 your@163.com --password YOUR_AUTH_CODE`

## Project Structure

```
unimail/
├── src/
│   ├── models.py          # Core data models (Pydantic)
│   ├── server.py          # MCP Server (tools registration)
│   ├── connectors/        # Provider-specific connectors
│   │   ├── base.py        # Abstract interface
│   │   ├── gmail_connector.py
│   │   ├── outlook_connector.py
│   │   └── imap_connector.py
│   ├── engine/            # Core business logic
│   │   └── mail_engine.py # Orchestrator
│   ├── storage/           # Persistence layer
│   │   ├── database.py    # SQLite (cache + metadata)
│   │   └── token_store.py # Encrypted token storage
│   ├── auth/              # OAuth flows
│   │   ├── oauth_flow.py  # Local callback server
│   │   ├── gmail_auth.py
│   │   └── outlook_auth.py
│   └── cli/               # CLI commands
│       └── main.py
├── pyproject.toml
└── README.md
```

## Development

```bash
pip install -e ".[dev]"
pytest tests/
ruff check src/
```

## License

MIT
