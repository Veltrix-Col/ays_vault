from __future__ import annotations

from dataclasses import dataclass

from .client import ZohoClient
from .coql import CoqlService
from .metadata import MetadataService
from .oauth import ZohoOAuthService
from .records import RecordsService
from .settings import ZohoSettings
from .token_store import TokenStore, get_token_store


@dataclass(frozen=True)
class ZohoServices:
    oauth: ZohoOAuthService
    client: ZohoClient
    metadata: MetadataService
    records: RecordsService
    coql: CoqlService

    @classmethod
    def build(
        cls,
        *,
        profile: str | None = None,
        config: ZohoSettings | None = None,
        token_store: TokenStore | None = None,
    ) -> "ZohoServices":
        cfg = config or ZohoSettings.from_django(profile)
        store = token_store or get_token_store(config=cfg)
        oauth = ZohoOAuthService(config=cfg, token_store=store)
        client = ZohoClient(oauth=oauth, config=oauth.config)
        return cls(
            oauth=oauth,
            client=client,
            metadata=MetadataService(client),
            records=RecordsService(client),
            coql=CoqlService(client),
        )
