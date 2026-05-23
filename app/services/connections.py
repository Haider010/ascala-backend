import json

from app.core.logging import get_logger
from app.db.schema import ensure_installed_locations_table
from app.db.session import db_connection

logger = get_logger()


def find_connection_for_context(context: dict, request_id: str | None = None) -> dict | None:
    location_id = context.get("activeLocation") or context.get("locationId")
    company_id = context.get("companyId")
    log_prefix = f"[ghl-session:{request_id}] " if request_id else ""

    with db_connection() as conn:
        cursor = conn.cursor()
        try:
            logger.info(
                "%sLooking up Ascala connection. lookup_mode=%s location_id=%s company_id=%s",
                log_prefix,
                "location" if location_id else "company" if company_id else "none",
                location_id or "missing",
                company_id or "missing",
            )
            logger.info("%sDatabase connection opened for install lookup.", log_prefix)

            row = None
            if company_id:
                cursor.execute(
                    """
                    SELECT id, company_id
                    FROM ascala_connections
                    WHERE company_id = %s
                    ORDER BY updated_at DESC NULLS LAST, created_at DESC NULLS LAST
                    LIMIT 1
                    """,
                    (company_id,),
                )
                row = cursor.fetchone()
            elif location_id:
                ensure_installed_locations_table(cursor)
                cursor.execute(
                    """
                    SELECT c.id, c.company_id
                    FROM ascala_connections c
                    JOIN ascala_installed_locations l ON l.connection_id = c.id
                    WHERE l.location_id = %s
                    ORDER BY l.last_seen_at DESC
                    LIMIT 1
                    """,
                    (location_id,),
                )
                row = cursor.fetchone()
            else:
                logger.warning("%sCannot lookup install: both activeLocation/locationId and companyId are missing.", log_prefix)
                return None

            if not row:
                logger.warning(
                    "%sNo Ascala install found. location_id=%s company_id=%s",
                    log_prefix,
                    location_id or "missing",
                    company_id or "missing",
                )
                return None

            connection = {"id": row[0], "companyId": row[1], "locationId": location_id}
            logger.info(
                "%sAscala install found. connection_id=%s stored_company_id=%s active_location_id=%s",
                log_prefix,
                connection["id"],
                connection["companyId"] or "missing",
                connection["locationId"] or "missing",
            )
            return connection
        except Exception:
            logger.exception("%sInstall lookup failed unexpectedly.", log_prefix)
            raise
        finally:
            cursor.close()
            logger.info("%sDatabase connection closed for install lookup.", log_prefix)


def upsert_installed_location(context: dict, connection: dict, request_id: str) -> None:
    company_id = context.get("companyId") or connection.get("companyId")
    location_id = context.get("activeLocation") or context.get("locationId") or connection.get("locationId")

    if not company_id or not location_id:
        logger.info(
            "[ghl-session:%s] Installed location not persisted. company_id=%s location_id=%s",
            request_id,
            company_id or "missing",
            location_id or "missing",
        )
        return

    with db_connection() as conn:
        cursor = conn.cursor()
        try:
            logger.info(
                "[ghl-session:%s] Persisting installed location from signed context. company_id=%s location_id=%s user_id=%s connection_id=%s",
                request_id,
                company_id,
                location_id,
                context.get("userId") or "missing",
                connection.get("id") or "missing",
            )
            ensure_installed_locations_table(cursor)
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
                    connection.get("id"),
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
            conn.commit()
            logger.info(
                "[ghl-session:%s] Installed location persisted successfully. company_id=%s location_id=%s",
                request_id,
                company_id,
                location_id,
            )
        except Exception:
            conn.rollback()
            logger.exception(
                "[ghl-session:%s] Failed to persist installed location. company_id=%s location_id=%s",
                request_id,
                company_id or "missing",
                location_id or "missing",
            )
        finally:
            cursor.close()
            logger.info("[ghl-session:%s] Database connection closed after installed location persistence.", request_id)
