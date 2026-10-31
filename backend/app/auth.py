import base64
import binascii
import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import Cookie, Depends, HTTPException, Response

from app.database import connect, utc_now
from app.models import UserResponse


SESSION_COOKIE = "gameionx_session"
SESSION_MAX_AGE_SECONDS = 60 * 60 * 24 * 7


def _session_secret() -> bytes:
    secret = os.getenv("GAMEIONX_SESSION_SECRET", "")
    environment = os.getenv("GAMEIONX_ENV", "development").lower()
    if not secret:
        if environment == "production":
            raise RuntimeError(
                "GAMEIONX_SESSION_SECRET must be configured in production."
            )
        secret = "development-only-session-secret"
    if environment == "production" and len(secret.encode()) < 32:
        raise RuntimeError(
            "GAMEIONX_SESSION_SECRET must contain at least 32 bytes in production."
        )
    return secret.encode()


def validate_session_configuration() -> None:
    _session_secret()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32
    )
    return "scrypt$" + base64.urlsafe_b64encode(salt + digest).decode()


def verify_password(password: str, encoded: str) -> bool:
    try:
        prefix, value = encoded.split("$", 1)
        if prefix != "scrypt":
            return False
        raw = base64.urlsafe_b64decode(value.encode())
        salt, expected = raw[:16], raw[16:]
        actual = hashlib.scrypt(
            password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32
        )
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError, binascii.Error):
        return False


def _token_hash(token: str) -> str:
    return hmac.new(
        _session_secret(), token.encode(), hashlib.sha256
    ).hexdigest()


def create_session(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    created_at = utc_now()
    expires_at = (
        datetime.now(timezone.utc) + timedelta(seconds=SESSION_MAX_AGE_SECONDS)
    ).isoformat()
    with connect() as connection:
        connection.execute(
            """
            INSERT INTO sessions(token_hash, user_id, created_at, expires_at)
            VALUES (?, ?, ?, ?)
            """,
            (_token_hash(token), user_id, created_at, expires_at),
        )
    return token


def delete_session(token: str | None) -> int | None:
    if not token:
        return None
    with connect() as connection:
        token_hash = _token_hash(token)
        row = connection.execute(
            "SELECT user_id FROM sessions WHERE token_hash = ?", (token_hash,)
        ).fetchone()
        connection.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))
        if row is not None:
            connection.execute(
                """
                INSERT INTO activities(user_id, action, detail, created_at)
                VALUES (?, 'logout', 'User logged out.', ?)
                """,
                (row["user_id"], utc_now()),
            )
            return row["user_id"]
    return None


def current_user(session: str | None = Cookie(default=None, alias=SESSION_COOKIE)) -> UserResponse:
    if not session:
        raise HTTPException(status_code=401, detail="Authentication required.")
    with connect() as connection:
        row = connection.execute(
            """
            SELECT users.id, users.username, users.email FROM users
            JOIN sessions ON sessions.user_id = users.id
            WHERE sessions.token_hash = ? AND sessions.expires_at > ?
            """,
            (_token_hash(session), datetime.now(timezone.utc).isoformat()),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=401, detail="Authentication required.")
    return UserResponse(
        id=row["id"],
        username=row["username"],
        email=row["email"],
    )


def set_session_cookie(response: Response, token: str) -> None:
    secure_cookie = os.getenv("GAMEIONX_COOKIE_SECURE", "").strip()
    secure = (
        secure_cookie == "1"
        if secure_cookie in {"0", "1"}
        else os.getenv("GAMEIONX_ENV", "development").lower() == "production"
    )
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        samesite="lax",
        secure=secure,
        max_age=SESSION_MAX_AGE_SECONDS,
    )
