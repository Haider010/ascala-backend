from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import agents, direct, ghl, health, oauth
from app.core.config import get_settings
from app.core.logging import configure_logging


def create_app() -> FastAPI:
    configure_logging()
    settings = get_settings()

    app = FastAPI(title="Ascala GHL API")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.allowed_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

    app.include_router(health.router)
    app.include_router(direct.router)
    app.include_router(ghl.router)
    app.include_router(agents.router)
    app.include_router(oauth.router)
    return app


app = create_app()
