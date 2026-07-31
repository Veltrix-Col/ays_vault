from __future__ import annotations

import time

import httpx

from integrations.zoho.schemas import AccessToken

VALID_SETTINGS = {
    "ZOHO_ACTIVE_PROFILE": "production",
    "ZOHO_ENABLED": True,
    "ZOHO_PUBLIC_SETUP_ENABLED": False,
    "ZOHO_PRODUCTION_CLIENT_ID": "",
    "ZOHO_PRODUCTION_CLIENT_SECRET": "",
    "ZOHO_PRODUCTION_REFRESH_TOKEN": "",
    "ZOHO_PRODUCTION_EXPECTED_ORG_ID": "",
    "ZOHO_SANDBOX_ENABLED": False,
    "ZOHO_SANDBOX_CLIENT_ID": "",
    "ZOHO_SANDBOX_CLIENT_SECRET": "",
    "ZOHO_SANDBOX_REFRESH_TOKEN": "",
    "ZOHO_SANDBOX_EXPECTED_ORG_ID": "",
    "ZOHO_CLIENT_ID": "client-id",
    "ZOHO_CLIENT_SECRET": "client-secret",
    "ZOHO_REDIRECT_URI": "http://localhost:8000/integrations/zoho/callback/",
    "ZOHO_ACCOUNTS_BASE_URL": "https://accounts.zoho.com",
    "ZOHO_API_BASE_URL": "https://www.zohoapis.com",
    "ZOHO_OAUTH_SCOPES": (
        "ZohoCRM.org.READ,ZohoCRM.settings.modules.READ,"
        "ZohoCRM.settings.fields.READ,ZohoCRM.modules.READ,ZohoCRM.coql.READ"
    ),
    "ZOHO_REFRESH_TOKEN": "refresh-secret",
    "ZOHO_REQUEST_TIMEOUT_SECONDS": "15",
    "ZOHO_MAX_RETRIES": "2",
}


def client_factory(handler):
    transport = httpx.MockTransport(handler)

    def factory(**kwargs):
        return httpx.Client(transport=transport, **kwargs)

    return factory


class FakeOAuth:
    def __init__(self, token: str = "access-secret"):
        self.token = AccessToken(
            token,
            expires_at=time.time() + 3600,
            api_domain="https://www.zohoapis.com",
        )
        self.invalidations = 0

    def get_access_token(self):
        return self.token

    def invalidate_access_token(self):
        self.invalidations += 1
