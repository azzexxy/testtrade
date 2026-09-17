"""Environment selection and credential loading.

Two rails guard the live environment: SAXO_ENV must say "live", and
SAXO_ALLOW_LIVE must be exactly "yes". Defaulting either one wrong lands
you in SIM, which is the safe direction to fail.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Anchor to the repo root rather than the working directory: the server may be
# launched from anywhere, and a silently-missing .env reads as "token empty".
_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_ENV_PATH)

GATEWAYS = {
    "sim": "https://gateway.saxobank.com/sim/openapi",
    "live": "https://gateway.saxobank.com/openapi",
}

AUTH_HOSTS = {
    "sim": "https://sim.logonvalidation.net",
    "live": "https://live.logonvalidation.net",
}


class ConfigError(RuntimeError):
    pass


TOKEN_STORE = Path(__file__).resolve().parent.parent / "token_store.json"


@dataclass(frozen=True)
class Config:
    env: str
    token: str
    gateway: str
    auth_host: str
    max_order_amount: float
    app_key: str = ""
    app_secret: str = ""
    redirect_uri: str = "http://localhost:8080/callback"

    @property
    def is_live(self) -> bool:
        return self.env == "live"

    @property
    def can_oauth(self) -> bool:
        return bool(self.app_key)

    @property
    def token_store_path(self) -> Path:
        """Per-environment, so a SIM login can never be read as a live one."""
        return TOKEN_STORE.with_name(f"token_store.{self.env}.json")


def load_config() -> Config:
    env = os.getenv("SAXO_ENV", "sim").strip().lower()
    if env not in GATEWAYS:
        raise ConfigError(f"SAXO_ENV must be 'sim' or 'live', got {env!r}")

    if env == "live" and os.getenv("SAXO_ALLOW_LIVE", "no").strip().lower() != "yes":
        raise ConfigError(
            "Refusing to use the live environment: set SAXO_ALLOW_LIVE=yes to confirm."
        )

    token = os.getenv("SAXO_TOKEN", "").strip()
    app_key = os.getenv("SAXO_APP_KEY", "").strip()

    # Either credential is enough on its own: OAuth is preferred, the 24-hour
    # token is the fallback. Only having neither is fatal.
    if not token and not app_key:
        hint = "" if _ENV_PATH.exists() else f" (no .env file at {_ENV_PATH})"
        raise ConfigError(
            "No credentials. Set SAXO_APP_KEY and run scripts/login.py, or paste "
            f"a 24-hour token into SAXO_TOKEN in .env{hint}"
        )

    raw_max = os.getenv("SAXO_MAX_ORDER_AMOUNT", "100000").strip()
    try:
        max_order_amount = float(raw_max)
    except ValueError:
        raise ConfigError(
            f"SAXO_MAX_ORDER_AMOUNT must be a number, got {raw_max!r}"
        ) from None
    if max_order_amount <= 0:
        raise ConfigError("SAXO_MAX_ORDER_AMOUNT must be greater than zero.")

    return Config(
        env=env,
        token=token,
        gateway=GATEWAYS[env],
        auth_host=AUTH_HOSTS[env],
        max_order_amount=max_order_amount,
        app_key=app_key,
        app_secret=os.getenv("SAXO_APP_SECRET", "").strip(),
        redirect_uri=os.getenv(
            "SAXO_REDIRECT_URI", "http://localhost:8080/callback"
        ).strip(),
    )
