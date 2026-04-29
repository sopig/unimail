"""SQLite storage for mail cache and account metadata."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..models import MailAccount, SyncState, UnifiedMessage

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    display_name TEXT DEFAULT '',
    is_default INTEGER DEFAULT 0,
    config_json TEXT NOT NULL,
    sync_state_json TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    external_id TEXT NOT NULL,
    thread_id TEXT,
    folder TEXT DEFAULT 'inbox',
    from_email TEXT,
    from_name TEXT,
    to_json TEXT,
    cc_json TEXT,
    subject TEXT,
    snippet TEXT,
    body_text TEXT,
    body_html TEXT,
    has_attachments INTEGER DEFAULT 0,
    attachments_json TEXT DEFAULT '[]',
    received_at TEXT NOT NULL,
    is_read INTEGER DEFAULT 0,
    is_starred INTEGER DEFAULT 0,
    labels_json TEXT DEFAULT '[]',
    cached_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (account_id) REFERENCES accounts(id),
    UNIQUE(account_id, external_id)
);

CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    subject, snippet, body_text, from_email, from_name,
    content=messages,
    content_rowid=rowid
);

-- Triggers to keep FTS index in sync with content table
CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(rowid, subject, snippet, body_text, from_email, from_name)
    VALUES (new.rowid, new.subject, new.snippet, new.body_text, new.from_email, new.from_name);
END;

CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, subject, snippet, body_text, from_email, from_name)
    VALUES ('delete', old.rowid, old.subject, old.snippet, old.body_text, old.from_email, old.from_name);
END;

CREATE TRIGGER IF NOT EXISTS messages_au AFTER UPDATE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, subject, snippet, body_text, from_email, from_name)
    VALUES ('delete', old.rowid, old.subject, old.snippet, old.body_text, old.from_email, old.from_name);
    INSERT INTO messages_fts(rowid, subject, snippet, body_text, from_email, from_name)
    VALUES (new.rowid, new.subject, new.snippet, new.body_text, new.from_email, new.from_name);
END;

CREATE TABLE IF NOT EXISTS sync_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id TEXT NOT NULL,
    sync_type TEXT,
    messages_added INTEGER DEFAULT 0,
    started_at TEXT,
    completed_at TEXT,
    error TEXT
);

CREATE TABLE IF NOT EXISTS send_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id TEXT NOT NULL,
    to_emails TEXT,
    subject TEXT,
    sent_at TEXT DEFAULT (datetime('now')),
    status TEXT DEFAULT 'sent'
);

CREATE INDEX IF NOT EXISTS idx_messages_account ON messages(account_id);
CREATE INDEX IF NOT EXISTS idx_messages_received ON messages(received_at DESC);
CREATE INDEX IF NOT EXISTS idx_messages_folder ON messages(folder);
CREATE INDEX IF NOT EXISTS idx_messages_unread ON messages(is_read) WHERE is_read = 0;
"""


class Database:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._init_schema()

    def _init_schema(self):
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    # === Account Operations ===

    def save_account(self, account: MailAccount) -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO accounts 
               (id, provider, email, display_name, is_default, config_json, sync_state_json, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                account.id,
                account.provider.value,
                account.email,
                account.display_name,
                int(account.is_default),
                account.config.model_dump_json(),
                account.sync_state.model_dump_json(),
                datetime.now().isoformat(),
            ),
        )
        self.conn.commit()

    def get_accounts(self) -> list[MailAccount]:
        rows = self.conn.execute("SELECT * FROM accounts ORDER BY is_default DESC").fetchall()
        return [self._row_to_account(row) for row in rows]

    def get_account(self, account_id: str) -> Optional[MailAccount]:
        row = self.conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
        return self._row_to_account(row) if row else None

    def get_account_by_email(self, email: str) -> Optional[MailAccount]:
        row = self.conn.execute("SELECT * FROM accounts WHERE email = ?", (email,)).fetchone()
        return self._row_to_account(row) if row else None

    def get_default_account(self) -> Optional[MailAccount]:
        row = self.conn.execute(
            "SELECT * FROM accounts WHERE is_default = 1 LIMIT 1"
        ).fetchone()
        if not row:
            row = self.conn.execute("SELECT * FROM accounts LIMIT 1").fetchone()
        return self._row_to_account(row) if row else None

    def delete_account(self, account_id: str) -> None:
        self.conn.execute("DELETE FROM messages WHERE account_id = ?", (account_id,))
        self.conn.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
        self.conn.commit()

    def update_sync_state(self, account_id: str, sync_state: SyncState) -> None:
        self.conn.execute(
            "UPDATE accounts SET sync_state_json = ?, updated_at = ? WHERE id = ?",
            (sync_state.model_dump_json(), datetime.now().isoformat(), account_id),
        )
        self.conn.commit()

    # === Message Operations ===

    def cache_message(self, msg: UnifiedMessage) -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO messages
               (id, account_id, external_id, thread_id, folder,
                from_email, from_name, to_json, cc_json,
                subject, snippet, body_text, body_html,
                has_attachments, attachments_json, received_at,
                is_read, is_starred, labels_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                msg.id,
                msg.account_id,
                msg.external_id,
                msg.thread_id,
                msg.folder,
                msg.from_contact.email,
                msg.from_contact.name,
                json.dumps([c.model_dump() for c in msg.to]),
                json.dumps([c.model_dump() for c in msg.cc]),
                msg.subject,
                msg.snippet,
                msg.body_text,
                msg.body_html,
                int(len(msg.attachments) > 0),
                json.dumps([a.model_dump() for a in msg.attachments]),
                msg.received_at.isoformat(),
                int(msg.is_read),
                int(msg.is_starred),
                json.dumps(msg.labels),
            ),
        )
        self.conn.commit()

    def cache_messages(self, messages: list[UnifiedMessage]) -> None:
        for msg in messages:
            self.cache_message(msg)

    def get_messages(
        self,
        account_id: Optional[str] = None,
        folder: str = "inbox",
        limit: int = 20,
        unread_only: bool = False,
        since: Optional[str] = None,
    ) -> list[dict]:
        query = "SELECT * FROM messages WHERE 1=1"
        params: list = []

        if account_id:
            query += " AND account_id = ?"
            params.append(account_id)
        if folder != "all":
            query += " AND folder = ?"
            params.append(folder)
        if unread_only:
            query += " AND is_read = 0"
        if since:
            query += " AND received_at >= ?"
            params.append(since)

        query += " ORDER BY received_at DESC LIMIT ?"
        params.append(limit)

        rows = self.conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def get_message(self, message_id: str) -> Optional[dict]:
        row = self.conn.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
        return dict(row) if row else None

    def search_messages(self, query: str, limit: int = 10) -> list[dict]:
        """Full-text search using FTS5."""
        rows = self.conn.execute(
            """SELECT m.* FROM messages m
               JOIN messages_fts fts ON m.rowid = fts.rowid
               WHERE messages_fts MATCH ?
               ORDER BY m.received_at DESC LIMIT ?""",
            (query, limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def mark_read(self, message_id: str) -> None:
        self.conn.execute("UPDATE messages SET is_read = 1 WHERE id = ?", (message_id,))
        self.conn.commit()

    # === Send Log ===

    def log_send(self, account_id: str, to_emails: list[str], subject: str) -> None:
        self.conn.execute(
            "INSERT INTO send_log (account_id, to_emails, subject) VALUES (?, ?, ?)",
            (account_id, json.dumps(to_emails), subject),
        )
        self.conn.commit()

    def get_send_count_today(self, account_id: str) -> int:
        today = datetime.now().strftime("%Y-%m-%d")
        row = self.conn.execute(
            "SELECT COUNT(*) as cnt FROM send_log WHERE account_id = ? AND sent_at >= ?",
            (account_id, today),
        ).fetchone()
        return row["cnt"] if row else 0

    # === Helpers ===

    def _row_to_account(self, row: sqlite3.Row) -> MailAccount:
        from ..models import GmailConfig, ImapConfig, OutlookConfig, Provider

        provider = Provider(row["provider"])
        config_data = json.loads(row["config_json"])

        if provider == Provider.GMAIL:
            config = GmailConfig(**config_data)
        elif provider == Provider.OUTLOOK:
            config = OutlookConfig(**config_data)
        else:
            config = ImapConfig(**config_data)

        return MailAccount(
            id=row["id"],
            provider=provider,
            email=row["email"],
            display_name=row["display_name"],
            is_default=bool(row["is_default"]),
            config=config,
            sync_state=SyncState(**json.loads(row["sync_state_json"])),
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def close(self):
        self.conn.close()
