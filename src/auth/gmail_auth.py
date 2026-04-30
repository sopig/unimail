"""Gmail OAuth 2.0 authentication flow."""

from __future__ import annotations

from google_auth_oauthlib.flow import Flow

from .oauth_flow import run_local_oauth

# Gmail scopes needed
GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
]

REDIRECT_URI = "http://127.0.0.1:9876/callback"


def gmail_oauth_flow(client_id: str, client_secret: str) -> dict:
    """
    Run Gmail OAuth flow interactively.
    
    Returns:
        dict with access_token, refresh_token, etc.
    """
    # Create OAuth flow
    client_config = {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [REDIRECT_URI],
        }
    }

    flow = Flow.from_client_config(
        client_config,
        scopes=GMAIL_SCOPES,
        redirect_uri=REDIRECT_URI,
    )

    # Generate auth URL
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",  # Force refresh token
    )

    # Run local server to capture callback
    code = run_local_oauth(auth_url)

    # Exchange code for tokens
    flow.fetch_token(code=code)
    credentials = flow.credentials

    return {
        "access_token": credentials.token,
        "refresh_token": credentials.refresh_token,
        "token_uri": credentials.token_uri,
        "client_id": credentials.client_id,
        "client_secret": credentials.client_secret,
        "expiry": credentials.expiry.isoformat() if credentials.expiry else None,
    }
