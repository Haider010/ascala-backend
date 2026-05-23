from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from Crypto.Cipher import AES
import requests
import time
import random
import string
import psycopg2
import os
import base64
import hashlib
import hmac
import json
import logging
import uuid
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("ascala")

SENSITIVE_LOG_KEYS = {
    "access_token",
    "refresh_token",
    "client_secret",
    "code",
    "refreshTokenId",
    "authorization",
    "secret",
}


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

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        origin.strip()
        for origin in os.getenv("allowed_origins", "*").split(",")
        if origin.strip()
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

database_url = os.getenv("database_url")
client_id = os.getenv("client_id")
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
ghl_app_webhook_secret = (
    os.getenv("ghl_app_webhook_secret")
    or os.getenv("GHL_APP_WEBHOOK_SECRET")
)

AGENT_ENDPOINTS = {
    "molly": os.getenv(
        "molly_webhook_url",
        "https://primary-production-b3410.up.railway.app/webhook/08d8a0f2-afb8-4e80-91d6-0efa25d5f85e/chat",
    ),
    "brandy": os.getenv(
        "brandy_webhook_url",
        "https://primary-production-b3410.up.railway.app/webhook/c65bf43d-45d3-42b5-9333-65e02bcd8835/chat",
    ),
}

SESSION_TTL_SECONDS = 60 * 60 * 8


def generate_api_key() -> str:
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
    return f"ascala_{int(time.time())}_{suffix}"


def b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("utf-8").rstrip("=")


def b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def evp_bytes_to_key(password: bytes, salt: bytes, key_len: int, iv_len: int) -> tuple[bytes, bytes]:
    derived = b""
    block = b""

    while len(derived) < key_len + iv_len:
        block = hashlib.md5(block + password + salt).digest()
        derived += block

    return derived[:key_len], derived[key_len : key_len + iv_len]


def decrypt_cryptojs_aes(encrypted_value: str, passphrase: str) -> dict:
    encrypted_bytes = base64.b64decode(encrypted_value)

    if not encrypted_bytes.startswith(b"Salted__"):
        raise ValueError("Unsupported encrypted payload format.")

    salt = encrypted_bytes[8:16]
    ciphertext = encrypted_bytes[16:]
    key, iv = evp_bytes_to_key(passphrase.encode("utf-8"), salt, 32, 16)
    cipher = AES.new(key, AES.MODE_CBC, iv)
    plaintext = cipher.decrypt(ciphertext)
    padding_length = plaintext[-1]

    if padding_length < 1 or padding_length > AES.block_size:
        raise ValueError("Invalid encrypted payload padding.")

    plaintext = plaintext[:-padding_length]
    return json.loads(plaintext.decode("utf-8"))


def create_app_session(context: dict) -> str:
    if not app_session_secret:
        raise ValueError("App session secret is not configured.")

    now = int(time.time())
    payload = {
        "userId": context.get("userId"),
        "companyId": context.get("companyId"),
        "role": context.get("role"),
        "type": context.get("type"),
        "activeLocation": context.get("activeLocation"),
        "email": context.get("email"),
        "iat": now,
        "exp": now + SESSION_TTL_SECONDS,
    }
    payload_json = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    payload_part = b64url_encode(payload_json)
    signature = hmac.new(
        app_session_secret.encode("utf-8"),
        payload_part.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return f"{payload_part}.{b64url_encode(signature)}"


def verify_app_session(token: str) -> dict:
    if not app_session_secret:
        raise HTTPException(status_code=500, detail="App session secret is not configured.")

    try:
        payload_part, signature_part = token.split(".", 1)
        expected_signature = hmac.new(
            app_session_secret.encode("utf-8"),
            payload_part.encode("utf-8"),
            hashlib.sha256,
        ).digest()
        provided_signature = b64url_decode(signature_part)

        if not hmac.compare_digest(expected_signature, provided_signature):
            raise ValueError("Invalid signature.")

        payload = json.loads(b64url_decode(payload_part).decode("utf-8"))
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid app session.")

    if int(payload.get("exp", 0)) < int(time.time()):
        raise HTTPException(status_code=401, detail="App session expired.")

    return payload


def get_authorization_token(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing app session.")

    return authorization.split(" ", 1)[1].strip()


def ensure_app_install_events_table(cursor) -> None:
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ascala_app_install_events (
            id UUID PRIMARY KEY,
            event_type TEXT,
            install_type TEXT,
            app_id TEXT,
            version_id TEXT,
            company_id TEXT,
            location_id TEXT,
            user_id TEXT,
            webhook_id TEXT,
            payload JSONB NOT NULL,
            received_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)


def store_app_install_event(cursor, payload: dict, request_id: str) -> None:
    ensure_app_install_events_table(cursor)
    cursor.execute("""
        INSERT INTO ascala_app_install_events (
            id,
            event_type,
            install_type,
            app_id,
            version_id,
            company_id,
            location_id,
            user_id,
            webhook_id,
            payload
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, (
        str(uuid.uuid4()),
        payload.get("type"),
        payload.get("installType"),
        payload.get("appId"),
        payload.get("versionId"),
        payload.get("companyId"),
        payload.get("locationId"),
        payload.get("userId"),
        payload.get("webhookId"),
        json.dumps(payload),
    ))
    logger.info(
        "[ghl-app-webhook:%s] App install event saved. event_type=%s install_type=%s company_id=%s location_id=%s webhook_id=%s",
        request_id,
        payload.get("type") or "missing",
        payload.get("installType") or "missing",
        payload.get("companyId") or "missing",
        payload.get("locationId") or "missing",
        payload.get("webhookId") or "missing",
    )


def exchange_location_token(
    company_access_token: str,
    company_id: str,
    location_id: str,
    request_id: str,
    log_scope: str = "ghl-app-webhook",
) -> dict | None:
    token_url = "https://services.leadconnectorhq.com/oauth/locationToken"
    payload = {
        "companyId": company_id,
        "locationId": location_id,
    }
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
        "Version": "2021-07-28",
        "Authorization": f"Bearer {company_access_token}",
    }

    logger.info(
        "[%s:%s] Starting company-to-location token exchange. token_url=%s payload=%s",
        log_scope,
        request_id,
        token_url,
        payload,
    )

    response = requests.post(token_url, data=payload, headers=headers, timeout=30)
    logger.info(
        "[%s:%s] Location token exchange completed. status_code=%s content_type=%s response_length=%s",
        log_scope,
        request_id,
        response.status_code,
        response.headers.get("content-type", "missing"),
        len(response.text or ""),
    )

    if not response.ok:
        logger.error(
            "[%s:%s] Location token exchange failed. status_code=%s response_body=%s",
            log_scope,
            request_id,
            response.status_code,
            response.text,
        )
        return None

    data = response.json()
    logger.info(
        "[%s:%s] Location token response parsed. keys=%s safe_response=%s",
        log_scope,
        request_id,
        sorted(data.keys()),
        safe_log_dict(data),
    )
    return data


def upsert_location_connection(cursor, token_data: dict, request_id: str, log_scope: str = "ghl-app-webhook") -> None:
    location_id = token_data.get("locationId")
    company_id = token_data.get("companyId")

    if not location_id:
        raise ValueError("Location token response did not include locationId.")

    api_key = generate_api_key()

    cursor.execute("SELECT id FROM ascala_connections WHERE location_id = %s", (location_id,))
    result = cursor.fetchone()
    logger.info(
        "[%s:%s] Location connection lookup result. found=%s connection_id=%s location_id=%s",
        log_scope,
        request_id,
        bool(result),
        result[0] if result else "none",
        location_id,
    )

    if result:
        cursor.execute("""
            UPDATE ascala_connections
            SET
                access_token = %s,
                refresh_token = %s,
                token_type = %s,
                expires_in = %s,
                scope = %s,
                refresh_token_id = %s,
                company_id = %s,
                location_id = %s,
                user_id = %s,
                user_type = %s,
                is_bulk_installation = %s,
                updated_at = NOW(),
                api_key = %s
            WHERE id = %s
        """, (
            token_data.get("access_token"),
            token_data.get("refresh_token"),
            token_data.get("token_type"),
            token_data.get("expires_in"),
            token_data.get("scope"),
            token_data.get("refreshTokenId"),
            company_id,
            location_id,
            token_data.get("userId"),
            token_data.get("userType"),
            token_data.get("isBulkInstallation", False),
            api_key,
            result[0],
        ))
        logger.info(
            "[%s:%s] Existing location connection updated. connection_id=%s company_id=%s location_id=%s",
            log_scope,
            request_id,
            result[0],
            company_id or "missing",
            location_id,
        )
    else:
        cursor.execute("""
            INSERT INTO ascala_connections (
                access_token,
                refresh_token,
                token_type,
                expires_in,
                scope,
                refresh_token_id,
                company_id,
                location_id,
                user_id,
                user_type,
                is_bulk_installation,
                api_key
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            token_data.get("access_token"),
            token_data.get("refresh_token"),
            token_data.get("token_type"),
            token_data.get("expires_in"),
            token_data.get("scope"),
            token_data.get("refreshTokenId"),
            company_id,
            location_id,
            token_data.get("userId"),
            token_data.get("userType"),
            token_data.get("isBulkInstallation", False),
            api_key,
        ))
        logger.info(
            "[%s:%s] New location connection inserted. company_id=%s location_id=%s",
            log_scope,
            request_id,
            company_id or "missing",
            location_id,
        )


def process_pending_location_installs(company_id: str, company_access_token: str, request_id: str) -> int:
    conn = None
    cursor = None
    processed_count = 0

    try:
        logger.info(
            "[oauth-callback:%s] Checking for pending location install webhook events. company_id=%s",
            request_id,
            company_id,
        )
        conn = psycopg2.connect(database_url)
        cursor = conn.cursor()
        ensure_app_install_events_table(cursor)
        cursor.execute("""
            SELECT DISTINCT location_id
            FROM ascala_app_install_events
            WHERE company_id = %s
              AND location_id IS NOT NULL
              AND UPPER(COALESCE(event_type, '')) = 'INSTALL'
              AND LOWER(COALESCE(install_type, '')) = 'location'
              AND NOT EXISTS (
                  SELECT 1
                  FROM ascala_connections
                  WHERE ascala_connections.location_id = ascala_app_install_events.location_id
              )
            ORDER BY location_id
            LIMIT 25
        """, (company_id,))
        pending_locations = [row[0] for row in cursor.fetchall()]
        conn.commit()

        logger.info(
            "[oauth-callback:%s] Pending location install lookup complete. count=%s locations=%s",
            request_id,
            len(pending_locations),
            pending_locations,
        )
    except Exception:
        if conn:
            conn.rollback()
        logger.exception("[oauth-callback:%s] Failed while checking pending location install events.", request_id)
        return 0
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()
            logger.info("[oauth-callback:%s] Database connection closed after pending install lookup.", request_id)

    for pending_location_id in pending_locations:
        location_token_data = exchange_location_token(
            company_access_token,
            company_id,
            pending_location_id,
            request_id,
            log_scope="oauth-callback",
        )
        if not location_token_data:
            logger.warning(
                "[oauth-callback:%s] Skipping pending location after failed token exchange. company_id=%s location_id=%s",
                request_id,
                company_id,
                pending_location_id,
            )
            continue

        try:
            conn = psycopg2.connect(database_url)
            cursor = conn.cursor()
            upsert_location_connection(cursor, location_token_data, request_id, log_scope="oauth-callback")
            conn.commit()
            processed_count += 1
            logger.info(
                "[oauth-callback:%s] Pending location install processed. company_id=%s location_id=%s processed_count=%s",
                request_id,
                company_id,
                pending_location_id,
                processed_count,
            )
        except Exception:
            if conn:
                conn.rollback()
            logger.exception(
                "[oauth-callback:%s] Failed while storing pending location token. company_id=%s location_id=%s",
                request_id,
                company_id,
                pending_location_id,
            )
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()

    logger.info(
        "[oauth-callback:%s] Pending location install processing finished. processed_count=%s",
        request_id,
        processed_count,
    )
    return processed_count


def find_connection_for_context(context: dict, request_id: str | None = None) -> dict | None:
    location_id = context.get("activeLocation") or context.get("locationId")
    company_id = context.get("companyId")
    log_prefix = f"[ghl-session:{request_id}] " if request_id else ""

    conn = None
    cursor = None

    try:
        logger.info(
            "%sLooking up Ascala connection. lookup_mode=%s location_id=%s company_id=%s",
            log_prefix,
            "location" if location_id else "company" if company_id else "none",
            location_id or "missing",
            company_id or "missing",
        )
        conn = psycopg2.connect(database_url)
        cursor = conn.cursor()
        logger.info("%sDatabase connection opened for install lookup.", log_prefix)

        row = None

        if location_id:
            cursor.execute(
                "SELECT id, company_id, location_id FROM ascala_connections WHERE location_id = %s",
                (location_id,),
            )
            row = cursor.fetchone()

            if not row and company_id:
                logger.info(
                    "%sNo location-specific install found. Trying company/bulk install fallback. company_id=%s active_location=%s",
                    log_prefix,
                    company_id,
                    location_id,
                )
                cursor.execute(
                    "SELECT id, company_id, location_id FROM ascala_connections WHERE company_id = %s AND location_id IS NULL",
                    (company_id,),
                )
                row = cursor.fetchone()
        elif company_id:
            cursor.execute(
                "SELECT id, company_id, location_id FROM ascala_connections WHERE company_id = %s AND location_id IS NULL",
                (company_id,),
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

        connection = {"id": row[0], "companyId": row[1], "locationId": row[2]}
        logger.info(
            "%sAscala install found. connection_id=%s stored_company_id=%s stored_location_id=%s",
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
        if cursor:
            cursor.close()
        if conn:
            conn.close()
            logger.info("%sDatabase connection closed for install lookup.", log_prefix)

@app.get("/")
def root():
    return {"message":"API is working!"}


@app.post("/ghl/session")
async def create_ghl_session(request: Request):
    request_id = str(uuid.uuid4())
    started_at = time.time()
    origin = request.headers.get("origin", "missing")
    referer = request.headers.get("referer", "missing")
    user_agent = request.headers.get("user-agent", "missing")

    logger.info(
        "[ghl-session:%s] Request received. origin=%s referer=%s user_agent=%s",
        request_id,
        origin,
        referer,
        user_agent,
    )

    if not ghl_app_shared_secret:
        logger.error("[ghl-session:%s] Missing GHL app shared secret environment variable.", request_id)
        raise HTTPException(status_code=500, detail="GHL app shared secret is not configured.")

    if not app_session_secret:
        logger.error("[ghl-session:%s] Missing app session secret environment variable.", request_id)
        raise HTTPException(status_code=500, detail="App session secret is not configured.")

    try:
        body = await request.json()
    except Exception:
        logger.exception("[ghl-session:%s] Failed to parse JSON body.", request_id)
        raise HTTPException(status_code=400, detail="Request body must be valid JSON.")

    logger.info(
        "[ghl-session:%s] JSON body parsed. keys=%s has_encryptedData=%s",
        request_id,
        sorted(body.keys()) if isinstance(body, dict) else "non-dict",
        isinstance(body, dict) and bool(body.get("encryptedData")),
    )

    if not isinstance(body, dict):
        logger.warning("[ghl-session:%s] Invalid body type. body_type=%s", request_id, type(body).__name__)
        raise HTTPException(status_code=400, detail="Request body must be a JSON object.")

    encrypted_data = body.get("encryptedData")
    query_params = body.get("queryParams") or {}

    logger.info(
        "[ghl-session:%s] Request payload inspected. encryptedData_length=%s query_param_keys=%s",
        request_id,
        len(encrypted_data) if isinstance(encrypted_data, str) else 0,
        sorted(query_params.keys()) if isinstance(query_params, dict) else "non-dict",
    )

    if not encrypted_data:
        logger.warning("[ghl-session:%s] Missing encryptedData in request body.", request_id)
        raise HTTPException(status_code=400, detail="encryptedData is required.")

    try:
        logger.info("[ghl-session:%s] Starting GHL encrypted context decryption.", request_id)
        context = decrypt_cryptojs_aes(encrypted_data, ghl_app_shared_secret)
        logger.info(
            "[ghl-session:%s] Decryption succeeded. context_keys=%s user_id=%s company_id=%s active_location=%s role=%s type=%s email_present=%s app_status=%s version_id=%s",
            request_id,
            sorted(context.keys()) if isinstance(context, dict) else "non-dict",
            context.get("userId") if isinstance(context, dict) else "missing",
            context.get("companyId") if isinstance(context, dict) else "missing",
            context.get("activeLocation") if isinstance(context, dict) else "missing",
            context.get("role") if isinstance(context, dict) else "missing",
            context.get("type") if isinstance(context, dict) else "missing",
            bool(context.get("email")) if isinstance(context, dict) else False,
            context.get("appStatus") if isinstance(context, dict) else "missing",
            context.get("versionId") if isinstance(context, dict) else "missing",
        )
    except Exception:
        logger.exception("[ghl-session:%s] Unable to decrypt GHL session context.", request_id)
        raise HTTPException(status_code=400, detail="Unable to decrypt GHL session context.")

    if not isinstance(context, dict):
        logger.warning("[ghl-session:%s] Decrypted context is not an object. context_type=%s", request_id, type(context).__name__)
        raise HTTPException(status_code=400, detail="Decrypted GHL session context is invalid.")

    connection = find_connection_for_context(context, request_id=request_id)
    if not connection:
        logger.warning(
            "[ghl-session:%s] Blocking session: no matching Ascala install. user_id=%s company_id=%s active_location=%s",
            request_id,
            context.get("userId") or "missing",
            context.get("companyId") or "missing",
            context.get("activeLocation") or "missing",
        )
        raise HTTPException(status_code=403, detail="This GHL account has not installed Ascala.")

    try:
        logger.info("[ghl-session:%s] Creating signed Ascala app session token.", request_id)
        session_token = create_app_session(context)
    except Exception:
        logger.exception("[ghl-session:%s] Failed to create signed Ascala app session token.", request_id)
        raise HTTPException(status_code=500, detail="Unable to create app session.")

    active_location = context.get("activeLocation") or connection.get("locationId")
    storage_scope_parts = [
        active_location or "agency",
        context.get("userId") or context.get("email") or "unknown-user",
    ]
    storage_scope = ".".join(storage_scope_parts)

    duration_ms = int((time.time() - started_at) * 1000)
    logger.info(
        "[ghl-session:%s] Session created successfully. active_location=%s user_id=%s company_id=%s connection_id=%s storage_scope=%s duration_ms=%s",
        request_id,
        active_location or "missing",
        context.get("userId") or "missing",
        context.get("companyId") or "missing",
        connection.get("id"),
        storage_scope,
        duration_ms,
    )

    return {
        "sessionToken": session_token,
        "userId": context.get("userId"),
        "companyId": context.get("companyId"),
        "activeLocation": active_location,
        "role": context.get("role"),
        "type": context.get("type"),
        "userName": context.get("userName"),
        "email": context.get("email"),
        "isAgencyOwner": context.get("isAgencyOwner"),
        "storageScope": storage_scope,
    }


@app.post("/agent-chat")
async def agent_chat(request: Request, authorization: str | None = Header(default=None)):
    session = verify_app_session(get_authorization_token(authorization))
    body = await request.json()
    agent_id = body.get("agentId")
    message = body.get("message")
    session_id = body.get("sessionId")

    if agent_id not in AGENT_ENDPOINTS:
        raise HTTPException(status_code=400, detail="Unknown agent.")
    if not message or not session_id:
        raise HTTPException(status_code=400, detail="message and sessionId are required.")

    connection = find_connection_for_context(session)
    if not connection:
        raise HTTPException(status_code=403, detail="This app session is not linked to an installed account.")

    response = requests.post(
        AGENT_ENDPOINTS[agent_id],
        json={
            "action": "sendMessage",
            "sessionId": session_id,
            "chatInput": message,
            "message": message,
            "locationId": session.get("activeLocation"),
            "companyId": session.get("companyId"),
            "userId": session.get("userId"),
            "userEmail": session.get("email"),
        },
        timeout=90,
    )

    content_type = response.headers.get("content-type", "")
    payload = response.json() if "application/json" in content_type else response.text

    if not response.ok:
        raise HTTPException(status_code=response.status_code, detail=payload)

    return {"payload": payload}


@app.post("/ghl/app-webhook")
async def ghl_app_webhook(request: Request):
    request_id = str(uuid.uuid4())
    started_at = time.time()
    user_agent = request.headers.get("user-agent", "missing")
    event_type = "missing"
    install_type = "missing"
    company_id = None
    location_id = None

    logger.info(
        "[ghl-app-webhook:%s] Webhook received. url=%s query_param_keys=%s client_host=%s user_agent=%s content_type=%s",
        request_id,
        str(request.url.replace(query="")),
        sorted(request.query_params.keys()),
        request.client.host if request.client else "missing",
        user_agent,
        request.headers.get("content-type", "missing"),
    )

    if ghl_app_webhook_secret:
        provided_secret = (
            request.query_params.get("secret")
            or request.headers.get("x-ascala-webhook-secret")
            or request.headers.get("x-ghl-webhook-secret")
        )
        if not provided_secret or not hmac.compare_digest(provided_secret, ghl_app_webhook_secret):
            logger.warning(
                "[ghl-app-webhook:%s] Webhook secret validation failed. has_secret=%s",
                request_id,
                bool(provided_secret),
            )
            raise HTTPException(status_code=401, detail="Invalid webhook secret.")
        logger.info("[ghl-app-webhook:%s] Webhook secret validation passed.", request_id)

    try:
        payload = await request.json()
    except Exception:
        logger.exception("[ghl-app-webhook:%s] Failed to parse webhook JSON body.", request_id)
        raise HTTPException(status_code=400, detail="Webhook body must be valid JSON.")

    if not isinstance(payload, dict):
        logger.warning("[ghl-app-webhook:%s] Invalid webhook body type. body_type=%s", request_id, type(payload).__name__)
        raise HTTPException(status_code=400, detail="Webhook body must be a JSON object.")

    event_type = payload.get("type") or "missing"
    install_type = payload.get("installType") or "missing"
    company_id = payload.get("companyId")
    location_id = payload.get("locationId")

    logger.info(
        "[ghl-app-webhook:%s] Webhook payload parsed. keys=%s safe_payload=%s",
        request_id,
        sorted(payload.keys()),
        safe_log_dict(payload),
    )
    logger.info(
        "[ghl-app-webhook:%s] Webhook context. event_type=%s install_type=%s company_id=%s location_id=%s user_id=%s app_id=%s version_id=%s webhook_id=%s",
        request_id,
        event_type,
        install_type,
        company_id or "missing",
        location_id or "missing",
        payload.get("userId") or "missing",
        payload.get("appId") or "missing",
        payload.get("versionId") or "missing",
        payload.get("webhookId") or "missing",
    )

    company_connection = None
    conn = None
    cursor = None

    try:
        logger.info("[ghl-app-webhook:%s] Opening database connection for webhook storage.", request_id)
        conn = psycopg2.connect(database_url)
        cursor = conn.cursor()
        store_app_install_event(cursor, payload, request_id)

        if company_id:
            cursor.execute(
                """
                SELECT id, access_token
                FROM ascala_connections
                WHERE company_id = %s AND location_id IS NULL
                ORDER BY updated_at DESC NULLS LAST
                LIMIT 1
                """,
                (company_id,),
            )
            company_connection = cursor.fetchone()
            logger.info(
                "[ghl-app-webhook:%s] Company connection lookup after webhook save. found=%s connection_id=%s",
                request_id,
                bool(company_connection),
                company_connection[0] if company_connection else "none",
            )
        else:
            logger.warning("[ghl-app-webhook:%s] Webhook missing companyId; cannot lookup company token.", request_id)

        conn.commit()
        logger.info("[ghl-app-webhook:%s] Webhook event committed successfully.", request_id)
    except Exception:
        if conn:
            conn.rollback()
        logger.exception("[ghl-app-webhook:%s] Failed while storing webhook event.", request_id)
        raise HTTPException(status_code=500, detail="Unable to store webhook event.")
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()
            logger.info("[ghl-app-webhook:%s] Database connection closed after webhook storage.", request_id)

    should_exchange_location_token = (
        str(event_type).upper() == "INSTALL"
        and str(install_type).lower() == "location"
        and company_id
        and location_id
    )

    if not should_exchange_location_token:
        duration_ms = int((time.time() - started_at) * 1000)
        logger.info(
            "[ghl-app-webhook:%s] Webhook stored without location token exchange. event_type=%s install_type=%s company_id=%s location_id=%s duration_ms=%s",
            request_id,
            event_type,
            install_type,
            company_id or "missing",
            location_id or "missing",
            duration_ms,
        )
        return {
            "status": "stored",
            "requestId": request_id,
            "eventType": event_type,
            "installType": install_type,
            "companyId": company_id,
            "locationId": location_id,
        }

    if not company_connection or not company_connection[1]:
        duration_ms = int((time.time() - started_at) * 1000)
        logger.warning(
            "[ghl-app-webhook:%s] Location install webhook stored, but no company token is available yet. company_id=%s location_id=%s duration_ms=%s",
            request_id,
            company_id,
            location_id,
            duration_ms,
        )
        return {
            "status": "stored_pending_company_token",
            "requestId": request_id,
            "companyId": company_id,
            "locationId": location_id,
        }

    location_token_data = exchange_location_token(company_connection[1], company_id, location_id, request_id)
    if not location_token_data:
        duration_ms = int((time.time() - started_at) * 1000)
        logger.warning(
            "[ghl-app-webhook:%s] Webhook stored, but location token exchange failed. company_id=%s location_id=%s duration_ms=%s",
            request_id,
            company_id,
            location_id,
            duration_ms,
        )
        return {
            "status": "stored_location_token_exchange_failed",
            "requestId": request_id,
            "companyId": company_id,
            "locationId": location_id,
        }

    conn = None
    cursor = None
    try:
        logger.info("[ghl-app-webhook:%s] Opening database connection for location token storage.", request_id)
        conn = psycopg2.connect(database_url)
        cursor = conn.cursor()
        upsert_location_connection(cursor, location_token_data, request_id)
        conn.commit()
        duration_ms = int((time.time() - started_at) * 1000)
        logger.info(
            "[ghl-app-webhook:%s] Location token stored successfully. company_id=%s location_id=%s duration_ms=%s",
            request_id,
            company_id,
            location_id,
            duration_ms,
        )
        return {
            "status": "stored_location_token",
            "requestId": request_id,
            "companyId": company_id,
            "locationId": location_id,
        }
    except Exception:
        if conn:
            conn.rollback()
        logger.exception("[ghl-app-webhook:%s] Failed while storing location token.", request_id)
        raise HTTPException(status_code=500, detail="Unable to store location token.")
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()
            logger.info("[ghl-app-webhook:%s] Database connection closed after location token storage.", request_id)


@app.get("/oauth-callback")
async def oauth_callback(request: Request):
    request_id = str(uuid.uuid4())
    started_at = time.time()
    raw_query_params = dict(request.query_params)

    logger.info(
        "[oauth-callback:%s] Callback received. url=%s query_params=%s client_host=%s user_agent=%s referer=%s",
        request_id,
        str(request.url.replace(query="")),
        safe_log_dict(raw_query_params),
        request.client.host if request.client else "missing",
        request.headers.get("user-agent", "missing"),
        request.headers.get("referer", "missing"),
    )

    code = request.query_params.get("code")
    if not code:
        logger.warning("[oauth-callback:%s] Authorization code missing from callback.", request_id)
        return {"error": "Authorization code not found in the request."}
    
    # Exchange the authorization code for an access token
    token_url = "https://services.leadconnectorhq.com/oauth/token"

    payload = {
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,   
        "grant_type": "authorization_code",
        "redirect_uri": "http://localhost:8000/oauth-callback",
        "user_type": "Company"
    }

    logger.info(
        "[oauth-callback:%s] Starting token exchange. token_url=%s payload=%s",
        request_id,
        token_url,
        safe_log_dict(payload),
    )

    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json"
    }
    response = requests.post(token_url, data=payload, headers=headers)
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
        return {"error": "Failed to obtain access token."}
    
    api_key = generate_api_key()
    data = response.json()
    location_id = data.get("locationId")
    company_id = data.get("companyId")
    is_bulk_installation = data.get("isBulkInstallation")

    logger.info(
        "[oauth-callback:%s] Token response parsed. keys=%s safe_response=%s",
        request_id,
        sorted(data.keys()),
        safe_log_dict(data),
    )
    logger.info(
        "[oauth-callback:%s] Install context from token response. company_id=%s location_id=%s user_id=%s user_type=%s is_bulk_installation=%s scopes_present=%s",
        request_id,
        company_id or "missing",
        location_id or "missing",
        data.get("userId") or "missing",
        data.get("userType") or "missing",
        is_bulk_installation,
        bool(data.get("scope")),
    )

    if not location_id:
        logger.warning(
            "[oauth-callback:%s] No locationId returned by HighLevel token response. This usually means HighLevel treated the install as company/bulk/agency-level, or the OAuth app/install flow did not provide a sub-account location context. company_id=%s is_bulk_installation=%s user_type=%s",
            request_id,
            company_id or "missing",
            is_bulk_installation,
            data.get("userType") or "missing",
        )

    # Store the API key in the database
    conn = None
    cursor = None
    try:
        logger.info("[oauth-callback:%s] Opening database connection for token storage.", request_id)
        conn = psycopg2.connect(database_url)
        cursor = conn.cursor()
        
        # Check for existing connection using company_id and location_id
        # For bulk installations, location_id will be None
        if location_id:
            logger.info(
                "[oauth-callback:%s] Looking for existing location install. location_id=%s",
                request_id,
                location_id,
            )
            cursor.execute("SELECT id from ascala_connections WHERE location_id = %s", (location_id,))
        else:
            # For bulk installations, check by company_id
            logger.info(
                "[oauth-callback:%s] Looking for existing company/bulk install. company_id=%s",
                request_id,
                company_id or "missing",
            )
            cursor.execute("SELECT id from ascala_connections WHERE company_id = %s AND location_id IS NULL", (company_id,))
        
        result = cursor.fetchone()
        logger.info(
            "[oauth-callback:%s] Existing connection lookup result. found=%s connection_id=%s",
            request_id,
            bool(result),
            result[0] if result else "none",
        )

        if result:
            logger.info("[oauth-callback:%s] Updating existing ascala_connections row.", request_id)
            cursor.execute("""
                UPDATE ascala_connections
                SET
                    access_token = %s,
                    refresh_token = %s,
                    token_type = %s,
                    expires_in = %s,
                    scope = %s,
                    refresh_token_id = %s,
                    company_id = %s,
                    location_id = %s,
                    user_id = %s,
                    user_type = %s,
                    is_bulk_installation = %s,
                    updated_at = NOW(),
                    api_key = %s
                WHERE id = %s
            """, (
                data.get("access_token"),
                data.get("refresh_token"),
                data.get("token_type"),
                data.get("expires_in"),
                data.get("scope"),
                data.get("refreshTokenId"),
                data.get("companyId"),
                data.get("locationId"),  
                data.get("userId"),
                data.get("userType"),
                data.get("isBulkInstallation"),
                api_key,
                result[0]
            ))

        else:
            logger.info("[oauth-callback:%s] Inserting new ascala_connections row.", request_id)
            cursor.execute("""
                INSERT INTO ascala_connections (
                    access_token,
                    refresh_token,
                    token_type,
                    expires_in,
                    scope,
                    refresh_token_id,
                    company_id,
                    location_id,
                    user_id,
                    user_type,
                    is_bulk_installation,
                    api_key
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                data.get("access_token"),
                data.get("refresh_token"),
                data.get("token_type"),
                data.get("expires_in"),
                data.get("scope"),
                data.get("refreshTokenId"),
                data.get("companyId"),
                data.get("locationId"),  
                data.get("userId"),
                data.get("userType"),
                data.get("isBulkInstallation"),
                api_key
            ))
        conn.commit()
        logger.info(
            "[oauth-callback:%s] Token storage committed successfully. company_id=%s location_id=%s duration_ms=%s",
            request_id,
            company_id or "missing",
            location_id or "missing",
            int((time.time() - started_at) * 1000),
        )

        if company_id and data.get("access_token") and not location_id:
            processed_count = process_pending_location_installs(company_id, data.get("access_token"), request_id)
            logger.info(
                "[oauth-callback:%s] Pending webhook location install processing result. processed_count=%s",
                request_id,
                processed_count,
            )
    except Exception as e:
        logger.exception("[oauth-callback:%s] Failed while storing token response in database.", request_id)
        return {"error": str(e)}
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()
            logger.info("[oauth-callback:%s] Database connection closed after token storage.", request_id)

    logger.info("[oauth-callback:%s] Redirecting user to HighLevel app.", request_id)
    return RedirectResponse(url="https://app.gohighlevel.com", status_code=302)
