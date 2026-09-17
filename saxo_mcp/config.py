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


@dataclass(frozen=True)
class Config:
    env: str
    token: str
    gateway: str
    auth_host: str

    @property
    def is_live(self) -> bool:
        return self.env == "live"


def load_config() -> Config:
    env = os.getenv("SAXO_ENV", "sim").strip().lower()
    if env not in GATEWAYS:
        raise ConfigError(f"SAXO_ENV must be 'sim' or 'live', got {env!r}")

    if env == "live" and os.getenv("SAXO_ALLOW_LIVE", "no").strip().lower() != "yes":
        raise ConfigError(
            "Refusing to use the live environment: set SAXO_ALLOW_LIVE=yes to confirm."
        )

    token = os.getenv("SAXO_TOKEN", "").strip()
    if not token:
        hint = "" if _ENV_PATH.exists() else f" (no .env file at {_ENV_PATH})"
        raise ConfigError(
            f"SAXO_TOKEN is empty. Paste a 24-hour token into .env{hint}"
        )

    return Config(
        env=env,
        token=token,
        gateway=GATEWAYS[env],
        auth_host=AUTH_HOSTS[env],
    )
