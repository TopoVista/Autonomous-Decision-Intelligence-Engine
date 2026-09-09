from __future__ import annotations

import re
import time
from urllib.parse import urlparse

import httpx
import structlog
from fastapi import HTTPException, Request
from jose import JWTError, jwt

from app.config import get_settings
from app.schemas.auth import AuthenticatedUser

logger = structlog.get_logger()
_JWKS_CACHE: dict[str, tuple[float, dict]] = {}


def _fallback_email(clerk_id: str) -> str:
    safe_clerk_id = re.sub(r"[^a-zA-Z0-9._-]", "-", clerk_id).strip("-") or "unknown"
    return f"{safe_clerk_id}@users.invalid"


def _parse_clerk_payload(payload: dict) -> AuthenticatedUser:
    clerk_id = payload.get("id") or payload.get("sub") or payload.get("user_id") or "unknown"
    email = payload.get("email_address") or payload.get("email")

    if not email and isinstance(payload.get("primary_email_address"), dict):
        email = payload["primary_email_address"].get("email_address")

    if not email and payload.get("email_addresses"):
        email_addresses = payload["email_addresses"]
        if isinstance(email_addresses, list) and email_addresses:
            first_email = email_addresses[0]
            if isinstance(first_email, dict):
                email = first_email.get("email_address") or first_email.get("email")
            elif isinstance(first_email, str):
                email = first_email

    email = str(email).strip() if email else _fallback_email(str(clerk_id))
    name = payload.get("name") or payload.get("first_name") or payload.get("full_name")
    return AuthenticatedUser(clerk_id=str(clerk_id), email=str(email), name=name)


def _trusted_issuer(issuer: str) -> bool:
    parsed = urlparse(issuer)
    host = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and (
        host == "clerk.com" or host.endswith(".clerk.com")
        or host == "clerk.accounts.dev" or host.endswith(".clerk.accounts.dev")
    )


async def _get_jwks(issuer: str) -> dict:
    cached = _JWKS_CACHE.get(issuer)
    if cached and cached[0] > time.monotonic():
        return cached[1]
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(f"{issuer.rstrip('/')}/.well-known/jwks.json")
        response.raise_for_status()
    jwks = response.json()
    if not isinstance(jwks, dict) or not isinstance(jwks.get("keys"), list):
        raise ValueError("Malformed Clerk JWKS response")
    if len(_JWKS_CACHE) >= 4:
        _JWKS_CACHE.pop(next(iter(_JWKS_CACHE)))
    _JWKS_CACHE[issuer] = (time.monotonic() + 3600, jwks)
    return jwks


async def _verify_clerk_token(token: str, configured_issuer: str = "") -> AuthenticatedUser:
    try:
        unverified_claims = jwt.get_unverified_claims(token)
        issuer = configured_issuer or str(unverified_claims.get("iss") or "")
        if not _trusted_issuer(issuer):
            raise HTTPException(status_code=401, detail="Invalid token issuer")
        header = jwt.get_unverified_header(token)
        key = next((item for item in (await _get_jwks(issuer))["keys"] if item.get("kid") == header.get("kid")), None)
        if key is None:
            raise HTTPException(status_code=401, detail="Unknown token signing key")
        payload = jwt.decode(token, key, algorithms=["RS256"], issuer=issuer, options={"verify_aud": False})
        return _parse_clerk_payload(payload)
    except HTTPException:
        raise
    except httpx.RequestError as exc:
        raise HTTPException(status_code=503, detail="Auth service unavailable") from exc
    except (JWTError, ValueError, TypeError):
        raise HTTPException(status_code=401, detail="Invalid token")


async def get_current_user(request: Request) -> AuthenticatedUser:
    settings = get_settings()

    auth_header = request.headers.get("Authorization")

    if not auth_header:
        if settings.auth_bypass:
            return AuthenticatedUser(clerk_id="dev-user", email="dev@example.com", name="Dev User")
        raise HTTPException(status_code=401, detail="Not authenticated")

    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid Authorization format")

    token = auth_header.split(" ", 1)[1]

    if settings.auth_bypass or (settings.environment != "production" and not settings.clerk_secret_key):
        return AuthenticatedUser(
            clerk_id=f"local-{abs(hash(token))}",
            email="local@example.com",
            name="Local User",
        )

    return await _verify_clerk_token(token, settings.clerk_issuer)
