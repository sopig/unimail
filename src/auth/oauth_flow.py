"""Local OAuth flow - starts a temporary HTTP server for callback."""

from __future__ import annotations

import asyncio
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse
from threading import Thread
from typing import Optional


class OAuthCallbackHandler(BaseHTTPRequestHandler):
    """Handles the OAuth callback on localhost."""

    code: Optional[str] = None
    error: Optional[str] = None

    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        if "code" in params:
            OAuthCallbackHandler.code = params["code"][0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                b"<html><body><h1>&#x2705; Authorization successful!</h1>"
                b"<p>You can close this window now.</p></body></html>"
            )
        elif "error" in params:
            OAuthCallbackHandler.error = params.get("error_description", params["error"])[0]
            self.send_response(400)
            self.end_headers()
            self.wfile.write(f"Error: {OAuthCallbackHandler.error}".encode())
        else:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"Missing code parameter")

    def log_message(self, format, *args):
        pass  # Silence logs


def run_local_oauth(auth_url: str, port: int = 9876, timeout: int = 300) -> str:
    """
    Run local OAuth flow:
    1. Start HTTP server on localhost:port
    2. Open browser with auth URL
    3. Wait for callback with authorization code
    4. Return the code

    Args:
        auth_url: The authorization URL to open in browser
        port: Local port for callback (must match redirect_uri)
        timeout: Timeout in seconds

    Returns:
        Authorization code

    Raises:
        TimeoutError: If user doesn't complete auth in time
        ValueError: If OAuth returns an error
    """
    OAuthCallbackHandler.code = None
    OAuthCallbackHandler.error = None

    server = HTTPServer(("127.0.0.1", port), OAuthCallbackHandler)
    server.timeout = timeout

    # Open browser
    print(f"\n🌐 Opening browser for authorization...")
    print(f"   If browser doesn't open, visit: {auth_url}\n")
    webbrowser.open(auth_url)

    # Wait for callback
    while OAuthCallbackHandler.code is None and OAuthCallbackHandler.error is None:
        server.handle_request()

    server.server_close()

    if OAuthCallbackHandler.error:
        raise ValueError(f"OAuth error: {OAuthCallbackHandler.error}")

    if OAuthCallbackHandler.code is None:
        raise TimeoutError("OAuth flow timed out")

    return OAuthCallbackHandler.code


async def run_local_oauth_async(auth_url: str, port: int = 9876, timeout: int = 300) -> str:
    """Async wrapper for OAuth flow."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, run_local_oauth, auth_url, port, timeout)
