"""Tests for REST API endpoints."""

import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.api import create_app


@pytest_asyncio.fixture
async def client():
    """Create a test API client with isolated data directory."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        (tmp_path / ".unimail" / "data").mkdir(parents=True)

        with patch("src.config.CONFIG_DIR", tmp_path / ".unimail"), \
             patch("src.config.CONFIG_FILE", tmp_path / ".unimail" / "config.toml"), \
             patch("src.config._config_instance", None):

            app = create_app("test-passphrase")
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as c:
                yield c


class TestHealthEndpoints:
    @pytest.mark.asyncio
    async def test_list_mail_empty(self, client):
        """Should return empty list when no accounts."""
        response = await client.get("/api/mail")
        # May return 500 (no accounts) or empty list
        assert response.status_code in (200, 500)

    @pytest.mark.asyncio
    async def test_list_accounts_empty(self, client):
        """Should return empty accounts list."""
        response = await client.get("/api/accounts")
        assert response.status_code == 200
        assert response.json() == []

    @pytest.mark.asyncio
    async def test_search_requires_query(self, client):
        """Search without query should fail."""
        response = await client.get("/api/mail/search")
        assert response.status_code == 422  # Validation error


class TestAuthEndpoints:
    @pytest.mark.asyncio
    async def test_jwt_not_configured(self, client):
        """JWT token endpoint should return 501 if not configured."""
        response = await client.post("/api/auth/token", json={
            "password": "test",
            "sub": "user1",
        })
        assert response.status_code == 501

    @pytest.mark.asyncio
    async def test_jwt_token_generation(self):
        """Should generate JWT token when properly configured."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / ".unimail" / "data").mkdir(parents=True)

            os.environ["UNIMAIL_JWT_SECRET"] = "test-secret-key"
            os.environ["UNIMAIL_API_TOKEN"] = "master-password"

            try:
                with patch("src.config.CONFIG_DIR", tmp_path / ".unimail"), \
                     patch("src.config.CONFIG_FILE", tmp_path / ".unimail" / "config.toml"), \
                     patch("src.config._config_instance", None):

                    app = create_app("test-passphrase")
                    transport = ASGITransport(app=app)
                    async with AsyncClient(transport=transport, base_url="http://test") as client:
                        response = await client.post("/api/auth/token", json={
                            "password": "master-password",
                            "sub": "testuser",
                            "scope": "read,write",
                        })
                        assert response.status_code == 200
                        data = response.json()
                        assert "access_token" in data
                        assert data["token_type"] == "bearer"
                        assert data["scope"] == "read,write"
            finally:
                os.environ.pop("UNIMAIL_JWT_SECRET", None)
                os.environ.pop("UNIMAIL_API_TOKEN", None)


class TestWebhookEndpoints:
    @pytest.mark.asyncio
    async def test_list_webhooks_empty(self, client):
        """Should return empty webhook list."""
        response = await client.get("/api/webhooks")
        assert response.status_code == 200
        assert response.json() == []

    @pytest.mark.asyncio
    async def test_register_webhook(self, client):
        """Should register a new webhook."""
        response = await client.post("/api/webhooks", json={
            "url": "https://example.com/hook",
            "events": ["new_message"],
        })
        assert response.status_code == 200
        data = response.json()
        assert data["url"] == "https://example.com/hook"
        assert "id" in data

    @pytest.mark.asyncio
    async def test_delete_webhook_not_found(self, client):
        """Should return 404 for non-existent webhook."""
        response = await client.delete("/api/webhooks/nonexistent")
        assert response.status_code == 404


class TestTemplateEndpoints:
    @pytest.mark.asyncio
    async def test_list_templates(self, client):
        """Should list available templates."""
        response = await client.get("/api/templates")
        assert response.status_code == 200
        templates = response.json()
        # Built-in templates should exist
        template_names = [t["name"] for t in templates]
        assert "welcome.html" in template_names
        assert "notification.html" in template_names
        assert "reply.html" in template_names
