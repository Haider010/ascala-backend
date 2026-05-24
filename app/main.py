from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import agents, direct, ghl, health, oauth
from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.db.schema import ensure_installed_locations_table, ensure_n8n_chat_histories_metadata
from app.db.session import db_connection, warm_db_pool


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

    @app.on_event("startup")
    def warm_database_connections() -> None:
        try:
            warm_db_pool()
            with db_connection() as conn:
                cursor = conn.cursor()
                try:
                    ensure_installed_locations_table(cursor)
                    ensure_n8n_chat_histories_metadata(cursor)
                    conn.commit()
                finally:
                    cursor.close()
        except Exception:
            get_logger().exception("Database pool warmup failed.")

    return app


app = create_app()
