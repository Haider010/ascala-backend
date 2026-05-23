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

        if location_id:
            cursor.execute(
                "SELECT id, company_id, location_id FROM ascala_connections WHERE location_id = %s",
                (location_id,),
            )
        elif company_id:
            cursor.execute(
                "SELECT id, company_id, location_id FROM ascala_connections WHERE company_id = %s AND location_id IS NULL",
                (company_id,),
            )
        else:
            logger.warning("%sCannot lookup install: both activeLocation/locationId and companyId are missing.", log_prefix)
            return None

        row = cursor.fetchone()
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

@app.get("/oauth-callback")
async def oauth_callback(request: Request):
    request_id = str(uuid.uuid4())
    started_at = time.time()
    raw_query_params = dict(request.query_params)

    logger.info(
        "[oauth-callback:%s] Callback received. url=%s query_params=%s client_host=%s user_agent=%s referer=%s",
        request_id,
        str(request.url),
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
    
    api_key = f"ascala_{int(time.time())}_{''.join(random.choices(string.ascii_lowercase + string.digits, k=6))}"
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
