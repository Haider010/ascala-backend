import logging

from app.core.config import get_settings

SENSITIVE_LOG_KEYS = {
    "access_token",
    "refresh_token",
    "client_secret",
    "code",
    "refreshTokenId",
    "authorization",
    "secret",
}


def configure_logging() -> None:
    logging.basicConfig(
        level=get_settings().log_level,
        format="%(asctime)s %(levelname)s %(message)s",
    )


def get_logger(name: str = "ascala") -> logging.Logger:
    return logging.getLogger(name)


def safe_log_dict(data: dict | None) -> dict:
    if not isinstance(data, dict):
        return {}

    safe = {}
    for key, value in data.items():
        if key in SENSITIVE_LOG_KEYS:
            safe[key] = f"<redacted length={len(str(value)) if value is not None else 0}>"
        else:
            safe[key] = value
    return safe
