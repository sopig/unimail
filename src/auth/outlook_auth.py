"""Microsoft Outlook/Hotmail OAuth 2.0 authentication flow via MSAL.

Microsoft requires a registered Azure AD application for personal account OAuth.
Public client IDs (Thunderbird, Azure CLI, etc.) are blocked at token exchange
for the /consumers tenant. Users must register their own app at:
  https://entra.microsoft.com → App registrations → New registration
  → Select "Personal Microsoft accounts only" → Add redirect URI http://127.0.0.1:9876/callback
"""

from __future__ import annotations

from msal import ConfidentialClientApplication

OUTLOOK_SCOPES = [
    "https://graph.microsoft.com/Mail.ReadWrite",
    "https://graph.microsoft.com/Mail.Send",
    "offline_access",
]

REDIRECT_URI = "http://127.0.0.1:9876/callback"


def outlook_oauth_flow(
    client_id: str,
    client_secret: str,
    tenant_id: str = "consumers",
) -> dict:
    """
    Run Outlook/Hotmail OAuth via authorization code flow.

    Requires an Azure AD app registered with "Personal Microsoft accounts" support.
    Use tenant_id="consumers" for personal accounts, "common" for mixed, or a
    specific tenant GUID for organizational accounts.

    Returns:
        dict with access_token, refresh_token, expires_in
    """
    from .oauth_flow import run_local_oauth

    authority = f"https://login.microsoftonline.com/{tenant_id}"

    app = ConfidentialClientApplication(
        client_id=client_id,
        client_credential=client_secret,
        authority=authority,
    )

    auth_url = app.get_authorization_request_url(
        scopes=OUTLOOK_SCOPES,
        redirect_uri=REDIRECT_URI,
    )

    print(f"\n🌐 Opening browser for Microsoft login...")
    print(f"   If the browser doesn't open, visit: {auth_url}\n")

    code = run_local_oauth(auth_url)

    result = app.acquire_token_by_authorization_code(
        code=code,
        scopes=OUTLOOK_SCOPES,
        redirect_uri=REDIRECT_URI,
    )

    if "error" in result:
        error_desc = result.get("error_description", result["error"])
        if "unauthorized_client" in error_desc.lower():
            raise ValueError(
                "OAuth failed: your Azure AD app is not authorized for personal Microsoft accounts.\n"
                "Make sure you selected 'Personal Microsoft accounts only' when registering the app,\n"
                "and that the redirect URI http://127.0.0.1:9876/callback is configured.\n"
                f"Details: {error_desc}"
            )
        raise ValueError(f"Token exchange failed: {error_desc}")

    return {
        "access_token": result["access_token"],
        "refresh_token": result.get("refresh_token", ""),
        "id_token": result.get("id_token", ""),
        "expires_in": result.get("expires_in"),
    }


def print_azure_setup_guide() -> None:
    """Print step-by-step guide for setting up Azure AD app registration."""
    guide = """
╔══════════════════════════════════════════════════════════════════╗
║           Outlook/Hotmail OAuth Setup Guide                     ║
╠══════════════════════════════════════════════════════════════════╣
║                                                                  ║
║  Microsoft requires an Azure AD app registration for personal    ║
║  account OAuth. Follow these steps:                              ║
║                                                                  ║
║  1. Go to: https://entra.microsoft.com                          ║
║     (Sign in with your Microsoft account)                        ║
║                                                                  ║
║  2. Navigate to: Identity → Applications → App registrations     ║
║     → New registration                                           ║
║                                                                  ║
║  3. Fill in:                                                     ║
║     - Name: UniMail (or any name you like)                       ║
║     - Supported account types:                                   ║
║       ✅ "Personal Microsoft accounts only"                      ║
║       (NOT "Accounts in any organizational directory")           ║
║     - Redirect URI:                                              ║
║       Platform: Web                                              ║
║       URI: http://127.0.0.1:9876/callback                        ║
║                                                                  ║
║  4. After creation, copy:                                        ║
║     - Application (client) ID  → use as --client-id             ║
║     - Certificates & secrets → New client secret → copy value    ║
║       → use as --client-secret                                   ║
║                                                                  ║
║  5. Run: unimail add outlook --client-id YOUR_ID                 ║
║                          --client-secret YOUR_SECRET              ║
║                                                                  ║
║  Note: A free Azure account is NOT required. The Entra ID       ║
║  portal works with any personal Microsoft account.               ║
╚══════════════════════════════════════════════════════════════════╝
"""
    print(guide)
