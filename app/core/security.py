import base64
import hashlib
import hmac
import json
import time

from Crypto.Cipher import AES
from fastapi import HTTPException

from app.core.config import get_settings


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
    settings = get_settings()
    if not settings.app_session_secret:
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
        "exp": now + settings.session_ttl_seconds,
    }
    payload_json = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    payload_part = b64url_encode(payload_json)
    signature = hmac.new(
        settings.app_session_secret.encode("utf-8"),
        payload_part.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return f"{payload_part}.{b64url_encode(signature)}"


def verify_app_session(token: str) -> dict:
    settings = get_settings()
    if not settings.app_session_secret:
        raise HTTPException(status_code=500, detail="App session secret is not configured.")

    try:
        payload_part, signature_part = token.split(".", 1)
        expected_signature = hmac.new(
            settings.app_session_secret.encode("utf-8"),
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
