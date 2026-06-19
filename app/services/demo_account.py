import json

from app.core.logging import get_logger
from app.db.schema import ensure_installed_locations_table

logger = get_logger()

DEMO_CONTEXT = {
    "companyId": "ascala-demo-company",
    "activeLocation": "ascala-demo-location",
    "userId": "ascala-demo-user",
    "userName": "Ascala Demo User",
    "email": "demo@ascala.local",
    "role": "admin",
    "type": "agency",
    "isAgencyOwner": False,
    "appStatus": "demo",
    "versionId": "local-demo",
}


def build_demo_context() -> dict:
    return dict(DEMO_CONTEXT)


def ensure_demo_account(cursor, context: dict, request_id: str | None = None) -> None:
    company_id = context["companyId"]
    location_id = context["activeLocation"]
    log_prefix = f"[direct-session:{request_id}] " if request_id else ""

    ensure_installed_locations_table(cursor)
    cursor.execute(
        """
        SELECT id
        FROM ascala_connections
        WHERE company_id = %s
        ORDER BY updated_at DESC NULLS LAST, created_at DESC NULLS LAST
        LIMIT 1
        """,
        (company_id,),
    )
    row = cursor.fetchone()

    if row:
        connection_id = row[0]
        cursor.execute(
            """
            UPDATE ascala_connections
            SET
                access_token = COALESCE(access_token, %s),
                refresh_token = COALESCE(refresh_token, %s),
                token_type = COALESCE(token_type, %s),
                expires_in = COALESCE(expires_in, %s),
                scope = COALESCE(scope, %s),
                refresh_token_id = COALESCE(refresh_token_id, %s),
                user_id = %s,
                user_type = %s,
                is_bulk_installation = %s,
                api_key = COALESCE(api_key, %s),
                updated_at = NOW()
            WHERE id = %s
            """,
            (
                "local-demo-access-token",
                "local-demo-refresh-token",
                "Bearer",
                0,
                "local-demo",
                "local-demo-refresh-token-id",
                context.get("userId"),
                context.get("type"),
                False,
                "ascala_local_demo",
                connection_id,
            ),
        )
    else:
        cursor.execute(
            """
            INSERT INTO ascala_connections (
                access_token,
                refresh_token,
                token_type,
                expires_in,
                scope,
                refresh_token_id,
                company_id,
                user_id,
                user_type,
                is_bulk_installation,
                api_key
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                "local-demo-access-token",
                "local-demo-refresh-token",
                "Bearer",
                0,
                "local-demo",
                "local-demo-refresh-token-id",
                company_id,
                context.get("userId"),
                context.get("type"),
                False,
                "ascala_local_demo",
            ),
        )
        connection_id = cursor.fetchone()[0]

    cursor.execute(
        """
        INSERT INTO ascala_installed_locations (
            connection_id,
            company_id,
            location_id,
            user_id,
            user_name,
            email,
            role,
            context_type,
            is_agency_owner,
            app_status,
            version_id,
            context_payload
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (company_id, location_id)
        DO UPDATE SET
            connection_id = EXCLUDED.connection_id,
            user_id = EXCLUDED.user_id,
            user_name = EXCLUDED.user_name,
            email = EXCLUDED.email,
            role = EXCLUDED.role,
            context_type = EXCLUDED.context_type,
            is_agency_owner = EXCLUDED.is_agency_owner,
            app_status = EXCLUDED.app_status,
            version_id = EXCLUDED.version_id,
            context_payload = EXCLUDED.context_payload,
            last_seen_at = NOW()
        """,
        (
            connection_id,
            company_id,
            location_id,
            context.get("userId"),
            context.get("userName"),
            context.get("email"),
            context.get("role"),
            context.get("type"),
            context.get("isAgencyOwner"),
            context.get("appStatus"),
            context.get("versionId"),
            json.dumps(context),
        ),
    )
    logger.info(
        "%sDemo direct account ready. company_id=%s location_id=%s user_id=%s",
        log_prefix,
        company_id,
        location_id,
        context.get("userId"),
    )
