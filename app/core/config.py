import os
from dataclasses import dataclass
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    database_url: str | None
    client_id: str | None
    client_secret: str | None
    ghl_app_shared_secret: str | None
    app_session_secret: str | None
    allowed_origins: tuple[str, ...]
    molly_webhook_url: str
    brandy_webhook_url: str
    log_level: str
    session_ttl_seconds: int = 60 * 60 * 8
    oauth_redirect_url: str = "https://app.gohighlevel.com"
    oauth_token_url: str = "https://services.leadconnectorhq.com/oauth/token"
    oauth_callback_redirect_uri: str = "http://localhost:8000/oauth-callback"

    @property
    def agent_endpoints(self) -> dict[str, str]:
        return {
            "molly": self.molly_webhook_url,
            "brandy": self.brandy_webhook_url,
        }


def _split_origins(value: str) -> tuple[str, ...]:
    origins = tuple(origin.strip() for origin in value.split(",") if origin.strip())
    return origins or ("*",)


@lru_cache
def get_settings() -> Settings:
    client_secret = os.getenv("client_secret")
    ghl_app_shared_secret = (
        os.getenv("ghl_app_shared_secret")
        or os.getenv("GHL_APP_SHARED_SECRET")
        or os.getenv("shared_secret")
    )
    app_session_secret = (
        os.getenv("app_session_secret")
        or os.getenv("APP_SESSION_SECRET")
        or client_secret
        or ghl_app_shared_secret
    )

    return Settings(
        database_url=os.getenv("database_url"),
        client_id=os.getenv("client_id"),
        client_secret=client_secret,
        ghl_app_shared_secret=ghl_app_shared_secret,
        app_session_secret=app_session_secret,
        allowed_origins=_split_origins(os.getenv("allowed_origins", "*")),
        molly_webhook_url=os.getenv(
            "molly_webhook_url",
            "https://primary-production-b3410.up.railway.app/webhook/08d8a0f2-afb8-4e80-91d6-0efa25d5f85e/chat",
        ),
        brandy_webhook_url=os.getenv(
            "brandy_webhook_url",
            "https://primary-production-b3410.up.railway.app/webhook/c65bf43d-45d3-42b5-9333-65e02bcd8835/chat",
        ),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
    )
