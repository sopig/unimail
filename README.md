# 📮 UniMail - Unified Email Gateway for AI Agents

A self-hosted MCP (Model Context Protocol) server that gives AI agents the ability to read, send, search, and manage emails across multiple providers.

## Features

- **Multi-provider**: Gmail (REST API) + Outlook/Hotmail (Graph API) + IMAP/SMTP (163, QQ, Yahoo, etc.)
- **Unified API**: One set of tools, transparent to the AI agent regardless of provider
- **MCP Server**: Standard MCP protocol, compatible with Claude, OpenClaw, and any MCP client
- **Secure**: Tokens encrypted at rest (Fernet/AES), daily send limits, audit logging
- **CLI**: Simple account management from the command line
- **Local-first**: SQLite cache, no cloud dependency, full data ownership

## Access Methods

UniMail provides **three** parallel access methods:

| Method | Use Case | Protocol |
|--------|----------|----------|
| **MCP Server** | AI agent integration (Claude, OpenClaw) | MCP over stdio |
| **REST API** | Web apps, scripts, any HTTP client | HTTP/JSON |
| **CLI** | Terminal power users, quick operations | Command line |

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

### 4. Start Server

```bash
# MCP Server (stdio, recommended for AI agent integration)
unimail serve --mode mcp

# REST API server
unimail serve --mode api --port 8765

# Both MCP + REST API simultaneously
unimail serve --mode all --port 8765

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

## REST API

Start the API server:

```bash
unimail serve --mode api --port 8765
```

API docs are auto-generated at `http://localhost:8765/docs` (Swagger UI).

### Authentication

Set `UNIMAIL_API_TOKEN` environment variable to enable Bearer token authentication:

```bash
export UNIMAIL_API_TOKEN=your-secret-token
```

If not set, the API is unauthenticated (suitable for local use).

### Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/mail` | 查看邮件列表（?folder=inbox&limit=20&unread_only=false&account=xxx） |
| `GET` | `/api/mail/{message_id}` | 读取邮件详情 |
| `POST` | `/api/mail/send` | 发送邮件 |
| `POST` | `/api/mail/{message_id}/reply` | 回复邮件 |
| `GET` | `/api/mail/search?q=keyword` | 搜索邮件 |
| `GET` | `/api/accounts` | 查看已连接账户 |
| `POST` | `/api/mail/{message_id}/archive` | 归档邮件 |
| `GET` | `/api/mail/{message_id}/attachments/{filename}` | 下载附件 |

### Examples

```bash
# List inbox
curl http://localhost:8765/api/mail

# Read a message
curl http://localhost:8765/api/mail/gmail_abc123

# Send an email
curl -X POST http://localhost:8765/api/mail/send \
  -H "Content-Type: application/json" \
  -d '{"to": ["user@example.com"], "subject": "Hello", "body": "# Hi\n\nThis is **markdown**."}'

# Search
curl "http://localhost:8765/api/mail/search?q=invoice"

# With auth token
curl -H "Authorization: Bearer your-secret-token" http://localhost:8765/api/mail
```

## CLI Mail Operations

Beyond account management, UniMail CLI provides direct mail operations:

```bash
# View inbox
unimail inbox
unimail inbox --limit 5 --unread
unimail inbox --account your@163.com

# Read a message
unimail read <message_id>

# Send an email
unimail send user@example.com --subject "Hello" --body "Message body"
unimail send user@example.com -s "With CC" -b "Body" --cc other@example.com
unimail send user@example.com -s "Files" -b "See attached" --attachment /path/to/file.pdf

# Reply to a message
unimail reply <message_id> --body "Thanks!"
unimail reply <message_id> -b "Got it" --reply-all

# Search emails
unimail search "invoice"
unimail search "meeting" --account your@gmail.com --limit 5
```

## Agent Integration

UniMail supports all major AI agent frameworks through multiple integration methods:

| Agent / Framework | Integration Method | Config File |
|---|---|---|
| **Claude Code** | MCP Server (stdio) | `agent-configs/claude-code.json` |
| **Cursor** | MCP Server (stdio) | `agent-configs/cursor-mcp.json` |
| **OpenCode** | MCP Server (stdio) | `agent-configs/opencode-mcp.json` |
| **Codex (OpenAI)** | OpenAI Function Calling | `unimail schema openai` |
| **LangChain / LangGraph** | LangChain Tools | `agent-configs/langchain-example.py` |
| **Dify** | OpenAPI Plugin | `agent-configs/dify-openapi.yaml` |
| **Coze** | OpenAPI Plugin | `agent-configs/dify-openapi.yaml` |
| **AutoGPT** | Plugin Manifest | `agent-configs/autogpt-plugin.json` |
| **Custom Agent** | REST API / OpenAI Schema | `/openapi.json` or `unimail schema openai` |

### Quick Setup by Agent

#### Claude Code / Cursor / OpenCode (MCP)

Copy the config to your agent's MCP configuration:

```json
{
  "mcpServers": {
    "unimail": {
      "command": "unimail",
      "args": ["serve", "--mode", "mcp"],
      "env": {"UNIMAIL_PASSPHRASE": "your-passphrase"}
    }
  }
}
```

#### OpenAI Function Calling (Codex, GPT-4, etc.)

```python
from src.schemas.openai_functions import TOOLS, dispatch

# Pass TOOLS to OpenAI API
response = client.chat.completions.create(
    model="gpt-4o",
    messages=messages,
    tools=TOOLS,
)

# Handle function calls
for tool_call in response.choices[0].message.tool_calls:
    result = await dispatch(tool_call.function.name, json.loads(tool_call.function.arguments))
```

#### LangChain

```bash
pip install unimail[langchain]
```

```python
from src.integrations.langchain_tools import get_all_tools

tools = get_all_tools()  # Returns 8 LangChain tools
agent = create_react_agent(llm, tools)
```

#### Dify / Coze (OpenAPI Plugin)

1. Start the REST API: `unimail serve --mode api --port 8765`
2. In Dify/Coze, create an OpenAPI plugin pointing to: `http://localhost:8765/openapi.json`

#### Schema Export CLI

```bash
# OpenAI function calling format
unimail schema openai

# OpenAPI spec (for REST API integrations)
unimail schema openapi

# MCP tool definitions
unimail schema mcp
```

## Architecture

```
                   ┌─────────────────────────────┐
                   │       Access Methods         │
                   ├──────────┬──────────┬────────┤
                   │ MCP      │ REST API │  CLI   │
                   │ (stdio)  │ (HTTP)   │(click) │
                   └────┬─────┴────┬─────┴───┬────┘
                        │          │         │
                        └──────────┼─────────┘
                                   │
                           ┌───────┴────────┐
                           │  Mail Engine   │
                           │ (core logic)   │
                           └───────┬────────┘
                                   │
                    ┌──────────────┼──────────────┐
                    │              │              │
              Gmail API     Graph API      IMAP/SMTP
              (OAuth2)      (OAuth2)       (AuthCode)
                    │              │              │
                Gmail          Outlook      163/QQ/Yahoo
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
│   ├── api.py             # REST API (FastAPI)
│   ├── schemas/           # Schema exports for various formats
│   │   └── openai_functions.py  # OpenAI function calling TOOLS + dispatch
│   ├── integrations/      # Framework-specific wrappers
│   │   └── langchain_tools.py   # LangChain @tool wrappers
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
├── agent-configs/         # Ready-to-use configs for each agent
│   ├── claude-code.json
│   ├── cursor-mcp.json
│   ├── opencode-mcp.json
│   ├── dify-openapi.yaml
│   ├── autogpt-plugin.json
│   └── langchain-example.py
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
