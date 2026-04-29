"""Webhook system for UniMail - push notifications on new mail events.

Supports registering multiple webhook URLs that receive POST notifications
when new emails arrive.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

import httpx

from .config import get_config, WebhookEntry
from .log import get_logger
from .models import UnifiedMessage

logger = get_logger(__name__)


@dataclass
class WebhookRegistration:
    """A registered webhook endpoint."""
    id: str
    url: str
    events: list[str] = field(default_factory=lambda: ["new_message"])
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())


class WebhookManager:
    """Manages webhook registrations and dispatches events.

    Features:
    - Register/unregister webhook URLs
    - POST JSON payloads on events
    - Retry with exponential backoff (3 attempts)
    """

    MAX_RETRIES = 3
    BASE_DELAY = 1.0  # seconds

    def __init__(self):
        self._webhooks: dict[str, WebhookRegistration] = {}
        self._load_from_config()

    def _load_from_config(self) -> None:
        """Load webhook registrations from config.toml."""
        config = get_config()
        for entry in config.webhooks:
            wh_id = entry.id or str(uuid.uuid4())[:8]
            self._webhooks[wh_id] = WebhookRegistration(
                id=wh_id,
                url=entry.url,
                events=entry.events,
            )
        if self._webhooks:
            logger.info(f"Loaded {len(self._webhooks)} webhook(s) from config")

    def register(self, url: str, events: Optional[list[str]] = None) -> WebhookRegistration:
        """Register a new webhook URL.

        Args:
            url: The endpoint URL to POST to
            events: List of event types to subscribe to (default: ["new_message"])

        Returns:
            The created WebhookRegistration
        """
        wh_id = str(uuid.uuid4())[:8]
        registration = WebhookRegistration(
            id=wh_id,
            url=url,
            events=events or ["new_message"],
        )
        self._webhooks[wh_id] = registration
        logger.info(f"Registered webhook {wh_id}: {url}")
        return registration

    def unregister(self, webhook_id: str) -> bool:
        """Remove a webhook registration.

        Returns:
            True if found and removed, False otherwise.
        """
        if webhook_id in self._webhooks:
            del self._webhooks[webhook_id]
            logger.info(f"Unregistered webhook {webhook_id}")
            return True
        return False

    def list_webhooks(self) -> list[WebhookRegistration]:
        """List all registered webhooks."""
        return list(self._webhooks.values())

    def get_webhook(self, webhook_id: str) -> Optional[WebhookRegistration]:
        """Get a specific webhook by ID."""
        return self._webhooks.get(webhook_id)

    async def notify_new_messages(self, messages: list[UnifiedMessage]) -> None:
        """Notify all relevant webhooks about new messages.

        Called after sync_inbox discovers new messages.
        """
        if not messages or not self._webhooks:
            return

        # Build payload
        payload = {
            "event": "new_message",
            "timestamp": datetime.now().isoformat(),
            "count": len(messages),
            "messages": [
                {
                    "id": msg.id,
                    "account_id": msg.account_id,
                    "from": {
                        "name": msg.from_contact.name,
                        "email": msg.from_contact.email,
                    },
                    "subject": msg.subject,
                    "snippet": msg.snippet[:200],
                    "received_at": msg.received_at.isoformat(),
                    "has_attachments": len(msg.attachments) > 0,
                }
                for msg in messages
            ],
        }

        # Dispatch to all webhooks subscribed to "new_message"
        tasks = []
        for wh in self._webhooks.values():
            if "new_message" in wh.events:
                tasks.append(self._deliver(wh, payload))

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _deliver(self, webhook: WebhookRegistration, payload: dict) -> None:
        """Deliver payload to a single webhook with retry logic."""
        for attempt in range(self.MAX_RETRIES):
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    response = await client.post(
                        webhook.url,
                        json=payload,
                        headers={
                            "Content-Type": "application/json",
                            "X-UniMail-Event": payload.get("event", "unknown"),
                            "X-UniMail-Webhook-ID": webhook.id,
                        },
                    )
                    if response.status_code < 400:
                        logger.debug(
                            f"Webhook {webhook.id} delivered successfully "
                            f"(status={response.status_code})"
                        )
                        return
                    else:
                        logger.warning(
                            f"Webhook {webhook.id} returned {response.status_code}"
                        )
            except Exception as e:
                logger.warning(
                    f"Webhook {webhook.id} delivery attempt {attempt + 1} failed: {e}"
                )

            # Exponential backoff
            if attempt < self.MAX_RETRIES - 1:
                delay = self.BASE_DELAY * (2 ** attempt)
                await asyncio.sleep(delay)

        logger.error(
            f"Webhook {webhook.id} delivery failed after {self.MAX_RETRIES} attempts"
        )
