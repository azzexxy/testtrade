"""Environment selection and credential loading.

Two rails guard the live environment: SAXO_ENV must say "live", and
SAXO_ALLOW_LIVE must be exactly "yes". Defaulting either one wrong lands
you in SIM, which is the safe direction to fail.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()

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
        raise ConfigError("SAXO_TOKEN is empty. Paste a 24-hour token into .env.")

    return Config(
        env=env,
        token=token,
        gateway=GATEWAYS[env],
        auth_host=AUTH_HOSTS[env],
    )
