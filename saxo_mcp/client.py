"""Thin async wrapper over the Saxo OpenAPI REST gateway."""

from __future__ import annotations

from typing import Any

import httpx

from .config import Config, load_config

TIMEOUT = httpx.Timeout(20.0, connect=10.0)


class SaxoError(RuntimeError):
    """An API call failed. Carries the status code for callers that care."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class SaxoClient:
    def __init__(self, config: Config | None = None) -> None:
        self.config = config or load_config()
        self._client = httpx.AsyncClient(
            base_url=self.config.gateway,
            headers={
                "Authorization": f"Bearer {self.config.token}",
                "Accept": "application/json",
            },
            timeout=TIMEOUT,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> Any:
        clean = {k: v for k, v in (params or {}).items() if v is not None}
        try:
            response = await self._client.request(
                method, path.lstrip("/"), params=clean, json=json
            )
        except httpx.RequestError as exc:
            raise SaxoError(f"Network error calling {path}: {exc}") from exc

        if response.status_code == 401:
            raise SaxoError(
                "401 Unauthorized. A 24-hour token expires daily — generate a fresh "
                "one at developer.saxo and update SAXO_TOKEN in .env.",
                status=401,
            )
        if response.status_code == 403:
            raise SaxoError(
                f"403 Forbidden on {path}. The app or user lacks permission for this "
                "endpoint (market-data terms and live-app approval are common causes).",
                status=403,
            )
        if response.status_code >= 400:
            raise SaxoError(
                f"{response.status_code} from {path}: {response.text[:400]}",
                status=response.status_code,
            )

        if not response.content:
            return None
        try:
            return response.json()
        except ValueError:
            return response.text

    async def get(self, path: str, **params: Any) -> Any:
        return await self.request("GET", path, params=params)

    async def post(self, path: str, payload: dict[str, Any]) -> Any:
        return await self.request("POST", path, json=payload)

    # --- Portfolio -----------------------------------------------------

    async def user_info(self) -> Any:
        return await self.get("port/v1/users/me")

    async def accounts(self) -> Any:
        return await self.get("port/v1/accounts/me")

    async def balance(self) -> Any:
        return await self.get("port/v1/balances/me")

    async def positions(self) -> Any:
        return await self.get("port/v1/positions/me", FieldGroups="DisplayAndFormat,PositionBase,PositionView")

    async def orders(self) -> Any:
        return await self.get("port/v1/orders/me", FieldGroups="DisplayAndFormat")

    # --- Reference and pricing -----------------------------------------

    async def search_instruments(
        self, keywords: str, asset_types: str = "Stock,FxSpot,Etf", limit: int = 10
    ) -> Any:
        return await self.get(
            "ref/v1/instruments",
            Keywords=keywords,
            AssetTypes=asset_types,
            **{"$top": limit},
        )

    async def info_price(self, uic: int, asset_type: str, amount: float = 1) -> Any:
        return await self.get(
            "trade/v1/infoprices",
            Uic=uic,
            AssetType=asset_type,
            Amount=amount,
            FieldGroups="Quote,PriceInfoDetails,DisplayAndFormat",
        )
