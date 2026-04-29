"""Microsoft Outlook/Hotmail OAuth 2.0 authentication flow via MSAL."""

from __future__ import annotations

from urllib.parse import urlencode

from msal import ConfidentialClientApplication

from .oauth_flow import run_local_oauth

OUTLOOK_SCOPES = [
    "https://graph.microsoft.com/Mail.ReadWrite",
    "https://graph.microsoft.com/Mail.Send",
    "offline_access",
]

REDIRECT_URI = "http://localhost:9876/callback"


def outlook_oauth_flow(
    client_id: str,
    client_secret: str,
    tenant_id: str = "common",
) -> dict:
    """
    Run Outlook/Hotmail OAuth flow interactively.
    
    Args:
        client_id: Azure AD application client ID
        client_secret: Azure AD application client secret
        tenant_id: 'common' for personal Microsoft accounts, or specific tenant

    Returns:
        dict with access_token, refresh_token
    """
    app = ConfidentialClientApplication(
        client_id=client_id,
        client_credential=client_secret,
        authority=f"https://login.microsoftonline.com/{tenant_id}",
    )

    # Generate auth URL
    auth_url = app.get_authorization_request_url(
        scopes=OUTLOOK_SCOPES,
        redirect_uri=REDIRECT_URI,
    )

    # Run local server to capture callback
    code = run_local_oauth(auth_url)

    # Exchange code for tokens
    result = app.acquire_token_by_authorization_code(
        code=code,
        scopes=OUTLOOK_SCOPES,
        redirect_uri=REDIRECT_URI,
    )

    if "error" in result:
        raise ValueError(f"Token exchange failed: {result.get('error_description', result['error'])}")

    return {
        "access_token": result["access_token"],
        "refresh_token": result.get("refresh_token", ""),
        "id_token": result.get("id_token", ""),
        "expires_in": result.get("expires_in"),
    }
