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
    sacha_webhook_url: str
    openai_api_key: str | None
    escouade_model: str
    log_level: str
    db_pool_min_connections: int = 1
    db_pool_max_connections: int = 5
    session_ttl_seconds: int = 60 * 60 * 8
    oauth_redirect_url: str = "https://app.gohighlevel.com"
    oauth_token_url: str = "https://services.leadconnectorhq.com/oauth/token"
    oauth_callback_redirect_uri: str = "http://localhost:8000/oauth-callback"
    direct_dev_session_enabled: bool = False
    direct_dev_company_id: str | None = None
    direct_dev_location_id: str | None = None
    direct_dev_user_id: str | None = None
    direct_dev_user_name: str | None = None
    direct_dev_email: str | None = None
    direct_dev_role: str | None = None
    direct_dev_context_type: str | None = None
    direct_dev_is_agency_owner: bool = False
    direct_dev_app_status: str | None = None
    direct_dev_version_id: str | None = None

    @property
    def agent_endpoints(self) -> dict[str, str]:
        return {
            "molly": self.molly_webhook_url,
            "brandy": self.brandy_webhook_url,
            "sacha": self.sacha_webhook_url,
        }


def _split_origins(value: str) -> tuple[str, ...]:
    origins = tuple(origin.strip() for origin in value.split(",") if origin.strip())
    return origins or ("*",)


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


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
        sacha_webhook_url=os.getenv(
            "sacha_webhook_url",
            "https://primary-production-b3410.up.railway.app/webhook/337039eb-d240-4139-965f-75ef3a625b3b/chat",
        ),
        openai_api_key=os.getenv("OPENAI_API_KEY"),
        escouade_model=os.getenv("ESCOUADE_MODEL", "gpt-4.1-mini"),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        db_pool_min_connections=_env_int("DB_POOL_MIN_CONNECTIONS", 1),
        db_pool_max_connections=_env_int("DB_POOL_MAX_CONNECTIONS", 5),
        direct_dev_session_enabled=_env_bool("DIRECT_DEV_SESSION_ENABLED"),
        direct_dev_company_id=os.getenv("DIRECT_DEV_COMPANY_ID"),
        direct_dev_location_id=os.getenv("DIRECT_DEV_LOCATION_ID"),
        direct_dev_user_id=os.getenv("DIRECT_DEV_USER_ID"),
        direct_dev_user_name=os.getenv("DIRECT_DEV_USER_NAME"),
        direct_dev_email=os.getenv("DIRECT_DEV_EMAIL"),
        direct_dev_role=os.getenv("DIRECT_DEV_ROLE", "admin"),
        direct_dev_context_type=os.getenv("DIRECT_DEV_CONTEXT_TYPE", "agency"),
        direct_dev_is_agency_owner=_env_bool("DIRECT_DEV_IS_AGENCY_OWNER"),
        direct_dev_app_status=os.getenv("DIRECT_DEV_APP_STATUS"),
        direct_dev_version_id=os.getenv("DIRECT_DEV_VERSION_ID"),
    )
