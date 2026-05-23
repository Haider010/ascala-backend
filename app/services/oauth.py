import random
import string
import time

import requests

from app.core.config import get_settings
from app.core.logging import get_logger, safe_log_dict
from app.db.session import db_connection
from app.services.connections import upsert_installed_location

logger = get_logger()


def generate_api_key() -> str:
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
    return f"ascala_{int(time.time())}_{suffix}"


def exchange_authorization_code(code: str, request_id: str) -> dict | None:
    settings = get_settings()
    payload = {
        "client_id": settings.client_id,
        "client_secret": settings.client_secret,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": settings.oauth_callback_redirect_uri,
        "user_type": "Company",
    }

    logger.info(
        "[oauth-callback:%s] Starting token exchange. token_url=%s payload=%s",
        request_id,
        settings.oauth_token_url,
        safe_log_dict(payload),
    )

    response = requests.post(
        settings.oauth_token_url,
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
        timeout=30,
    )
    logger.info(
        "[oauth-callback:%s] Token exchange completed. status_code=%s content_type=%s response_length=%s",
        request_id,
        response.status_code,
        response.headers.get("content-type", "missing"),
        len(response.text or ""),
    )
    if response.status_code != 200:
        logger.error(
            "[oauth-callback:%s] Token exchange failed. status_code=%s response_body=%s",
            request_id,
            response.status_code,
            response.text,
        )
        return None

    return response.json()


def store_oauth_token(data: dict, request_id: str, started_at: float) -> str:
    api_key = generate_api_key()
    location_id = data.get("locationId")
    company_id = data.get("companyId")

    if not company_id:
        raise ValueError("Token response missing companyId.")

    connection_id = None
    with db_connection() as conn:
        cursor = conn.cursor()
        try:
            logger.info("[oauth-callback:%s] Opening database connection for token storage.", request_id)
            logger.info(
                "[oauth-callback:%s] Looking for existing company install. company_id=%s",
                request_id,
                company_id,
            )
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

            result = cursor.fetchone()
            logger.info(
                "[oauth-callback:%s] Existing connection lookup result. found=%s connection_id=%s",
                request_id,
                bool(result),
                result[0] if result else "none",
            )

            if result:
                connection_id = result[0]
                logger.info("[oauth-callback:%s] Updating existing ascala_connections row.", request_id)
                cursor.execute(
                    """
                    UPDATE ascala_connections
                    SET
                        access_token = %s,
                        refresh_token = %s,
                        token_type = %s,
                        expires_in = %s,
                        scope = %s,
                        refresh_token_id = %s,
                        company_id = %s,
                        user_id = %s,
                        user_type = %s,
                        is_bulk_installation = %s,
                        updated_at = NOW(),
                        api_key = %s
                    WHERE id = %s
                    """,
                    (
                        data.get("access_token"),
                        data.get("refresh_token"),
                        data.get("token_type"),
                        data.get("expires_in"),
                        data.get("scope"),
                        data.get("refreshTokenId"),
                        data.get("companyId"),
                        data.get("userId"),
                        data.get("userType"),
                        data.get("isBulkInstallation"),
                        api_key,
                        result[0],
                    ),
                )
            else:
                logger.info("[oauth-callback:%s] Inserting new ascala_connections row.", request_id)
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
                        data.get("access_token"),
                        data.get("refresh_token"),
                        data.get("token_type"),
                        data.get("expires_in"),
                        data.get("scope"),
                        data.get("refreshTokenId"),
                        data.get("companyId"),
                        data.get("userId"),
                        data.get("userType"),
                        data.get("isBulkInstallation"),
                        api_key,
                    ),
                )
                connection_id = cursor.fetchone()[0]

            conn.commit()
            logger.info(
                "[oauth-callback:%s] Token storage committed successfully. company_id=%s location_id=%s duration_ms=%s",
                request_id,
                company_id or "missing",
                location_id or "missing",
                int((time.time() - started_at) * 1000),
            )
        except Exception:
            conn.rollback()
            logger.exception("[oauth-callback:%s] Failed while storing token response in database.", request_id)
            raise
        finally:
            cursor.close()
            logger.info("[oauth-callback:%s] Database connection closed after token storage.", request_id)

    if location_id and connection_id:
        upsert_installed_location(
            {
                "companyId": company_id,
                "activeLocation": location_id,
                "userId": data.get("userId"),
                "userName": data.get("userName"),
                "email": data.get("email"),
                "role": data.get("role"),
                "type": data.get("userType"),
                "isAgencyOwner": data.get("isAgencyOwner"),
                "appStatus": data.get("appStatus"),
                "versionId": data.get("versionId"),
            },
            {"id": connection_id, "companyId": company_id, "locationId": location_id},
            request_id,
        )

    return str(connection_id)
