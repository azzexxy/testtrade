"""OAuth authorization-code flow with automatic refresh.

Saxo access tokens last about 20 minutes and refresh tokens about an hour,
so anything long-running has to refresh on the fly. Tokens are persisted
per environment, and a SIM login is therefore never readable as a live one.

PKCE is used whenever no app secret is configured, which keeps the secret
off disk entirely.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import time
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import urlencode

import httpx

from .config import Config, load_config

# Refresh this many seconds before actual expiry, so a call never departs
# with a token that dies in flight.
REFRESH_MARGIN = 90


class AuthError(RuntimeError):
    pass


@dataclass
class Tokens:
    access_token: str
    refresh_token: str
    expires_at: float
    refresh_expires_at: float

    @property
    def access_expired(self) -> bool:
        return time.time() >= self.expires_at - REFRESH_MARGIN

    @property
    def refresh_expired(self) -> bool:
        return time.time() >= self.refresh_expires_at

    @classmethod
    def from_response(cls, data: dict[str, Any]) -> Tokens:
        now = time.time()
        try:
            return cls(
                access_token=data["access_token"],
                refresh_token=data.get("refresh_token", ""),
                expires_at=now + float(data.get("expires_in", 1200)),
                refresh_expires_at=now
                + float(data.get("refresh_token_expires_in", 3600)),
            )
        except KeyError as exc:
            raise AuthError(f"Token response missing {exc}: {data}") from None


def make_pkce() -> tuple[str, str]:
    """Return (verifier, challenge) for PKCE S256."""
    verifier = secrets.token_urlsafe(64)[:128]
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return verifier, challenge


def build_authorize_url(
    config: Config, state: str, challenge: str | None = None
) -> str:
    params = {
        "response_type": "code",
        "client_id": config.app_key,
        "redirect_uri": config.redirect_uri,
        "state": state,
    }
    if challenge:
        params["code_challenge"] = challenge
        params["code_challenge_method"] = "S256"
    return f"{config.auth_host}/authorize?{urlencode(params)}"


def _token_request(config: Config, form: dict[str, str]) -> dict[str, Any]:
    auth = None
    if config.app_secret:
        auth = (config.app_key, config.app_secret)
    else:
        form["client_id"] = config.app_key

    try:
        response = httpx.post(
            f"{config.auth_host}/token", data=form, auth=auth, timeout=30.0
        )
    except httpx.RequestError as exc:
        raise AuthError(f"Could not reach the token endpoint: {exc}") from exc

    if response.status_code >= 400:
        raise AuthError(f"{response.status_code} from token endpoint: {response.text[:400]}")
    return response.json()


def exchange_code(config: Config, code: str, verifier: str | None = None) -> Tokens:
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": config.redirect_uri,
    }
    if verifier:
        form["code_verifier"] = verifier
    return Tokens.from_response(_token_request(config, form))


def refresh_tokens(config: Config, tokens: Tokens) -> Tokens:
    if not tokens.refresh_token:
        raise AuthError("No refresh token stored; run scripts/login.py again.")
    if tokens.refresh_expired:
        raise AuthError(
            "The refresh token has expired. Run scripts/login.py to sign in again."
        )
    return Tokens.from_response(
        _token_request(
            config,
            {"grant_type": "refresh_token", "refresh_token": tokens.refresh_token},
        )
    )


def save_tokens(config: Config, tokens: Tokens) -> None:
    path = config.token_store_path
    path.write_text(json.dumps(asdict(tokens), indent=2))
    # Tokens are bearer credentials: keep them off other users' eyes.
    os.chmod(path, 0o600)


def load_tokens(config: Config) -> Tokens | None:
    path = config.token_store_path
    if not path.exists():
        return None
    try:
        return Tokens(**json.loads(path.read_text()))
    except (ValueError, TypeError) as exc:
        raise AuthError(
            f"Token store at {path} is unreadable ({exc}). Delete it and run "
            "scripts/login.py again."
        ) from None


def current_access_token(config: Config | None = None) -> tuple[str, str]:
    """Return (token, source), refreshing if needed.

    Prefers a stored OAuth token, falls back to the 24-hour token in .env.
    """
    config = config or load_config()

    if config.can_oauth:
        tokens = load_tokens(config)
        if tokens is not None:
            if tokens.access_expired:
                tokens = refresh_tokens(config, tokens)
                save_tokens(config, tokens)
                return tokens.access_token, "oauth (refreshed)"
            return tokens.access_token, "oauth"

    if config.token:
        return config.token, "24-hour token"

    raise AuthError(
        "No usable credentials. Run scripts/login.py to sign in, or set "
        "SAXO_TOKEN in .env."
    )
