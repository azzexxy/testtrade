"""Thin async wrapper over the Saxo OpenAPI REST gateway."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from .auth import AuthError, current_access_token
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
        self._account_key: str | None = None
        self._client_key: str | None = None
        self.auth_source = "unknown"
        self._client = httpx.AsyncClient(
            base_url=self.config.gateway,
            headers={"Accept": "application/json"},
            timeout=TIMEOUT,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _auth_header(self) -> dict[str, str]:
        """Fetch a valid token per request, refreshing when it has aged out.

        current_access_token uses a blocking HTTP call on the refresh path, so
        it runs in a worker thread rather than stalling the event loop.
        """
        try:
            token, source = await asyncio.to_thread(current_access_token, self.config)
        except AuthError as exc:
            raise SaxoError(str(exc), status=401) from None
        self.auth_source = source
        return {"Authorization": f"Bearer {token}"}

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> Any:
        clean = {k: v for k, v in (params or {}).items() if v is not None}
        headers = await self._auth_header()
        try:
            response = await self._client.request(
                method, path.lstrip("/"), params=clean, json=json, headers=headers
            )
        except httpx.RequestError as exc:
            raise SaxoError(f"Network error calling {path}: {exc}") from exc

        if response.status_code == 401:
            if self.auth_source.startswith("oauth"):
                advice = "Run scripts/login.py to sign in again."
            else:
                advice = (
                    "A 24-hour token expires daily — generate a fresh one at "
                    "developer.saxo and update SAXO_TOKEN in .env, or switch to "
                    "OAuth with scripts/login.py."
                )
            raise SaxoError(f"401 Unauthorized. {advice}", status=401)
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

    async def delete(self, path: str, **params: Any) -> Any:
        return await self.request("DELETE", path, params=params)

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

    async def snapshot(self, uic: int, asset_type: str, amount: float = 10000) -> Any:
        """Price plus the session's range and dealing costs."""
        return await self.get(
            "trade/v1/infoprices",
            Uic=uic,
            AssetType=asset_type,
            Amount=amount,
            FieldGroups=(
                "Quote,PriceInfo,PriceInfoDetails,DisplayAndFormat,"
                "InstrumentPriceDetails,Commissions"
            ),
        )

    # --- Trading --------------------------------------------------------

    async def _load_keys(self) -> None:
        if self._account_key is not None and self._client_key is not None:
            return
        data = await self.accounts()
        rows = data.get("Data", [])
        if not rows:
            raise SaxoError("No account found to trade on.")
        self._account_key = rows[0]["AccountKey"]
        self._client_key = rows[0]["ClientKey"]

    async def account_key(self) -> str:
        """The default account's key, cached. Orders are rejected without it."""
        await self._load_keys()
        assert self._account_key is not None
        return self._account_key

    async def client_key(self) -> str:
        """The client key, cached. Single-position lookups require it."""
        await self._load_keys()
        assert self._client_key is not None
        return self._client_key

    async def place_order(
        self,
        *,
        uic: int,
        asset_type: str,
        buy_sell: str,
        amount: float,
        order_type: str = "Market",
        price: float | None = None,
        duration: str = "DayOrder",
        stop_loss: float | None = None,
        take_profit: float | None = None,
    ) -> Any:
        account_key = await self.account_key()
        payload: dict[str, Any] = {
            "AccountKey": account_key,
            "Uic": uic,
            "AssetType": asset_type,
            "BuySell": buy_sell,
            "Amount": amount,
            "OrderType": order_type,
            "OrderDuration": {"DurationType": duration},
            "ManualOrder": True,
        }
        if price is not None:
            payload["OrderPrice"] = price

        # Protective orders ride along with the entry so the position is never
        # naked, not even for the moment between two API calls.
        exit_side = "Sell" if buy_sell == "Buy" else "Buy"
        related = []
        for level, kind in ((stop_loss, "Stop"), (take_profit, "Limit")):
            if level is None:
                continue
            related.append(
                {
                    "AccountKey": account_key,
                    "Uic": uic,
                    "AssetType": asset_type,
                    "BuySell": exit_side,
                    "Amount": amount,
                    "OrderType": kind,
                    "OrderPrice": level,
                    "OrderDuration": {"DurationType": "GoodTillCancel"},
                    "ManualOrder": True,
                }
            )
        if related:
            payload["Orders"] = related

        return await self.post("trade/v2/orders", payload)

    async def working_orders_for_uic(self, uic: int) -> list[dict[str, Any]]:
        data = await self.orders()
        return [o for o in data.get("Data", []) if o.get("Uic") == uic]

    async def cancel_order(self, order_id: str) -> Any:
        return await self.delete(
            f"trade/v2/orders/{order_id}", AccountKey=await self.account_key()
        )

    async def position(self, position_id: str) -> Any:
        return await self.get(
            f"port/v1/positions/{position_id}",
            ClientKey=await self.client_key(),
            FieldGroups="DisplayAndFormat,PositionBase,PositionView",
        )

    async def close_position(self, position_id: str) -> tuple[Any, list[str]]:
        """Close at market, then cancel the protective orders left behind.

        Saxo does not retire a position's stop when the position goes, and an
        orphaned stop is not harmless: a sell stop with nothing to sell opens a
        short if it triggers. Returns the closing order and the ids cancelled.
        """
        data = await self.position(position_id)
        base = data.get("PositionBase", {})
        amount = base.get("Amount")
        if amount is None:
            raise SaxoError(f"Position {position_id} has no amount to close.")

        uic = base["Uic"]
        result = await self.place_order(
            uic=uic,
            asset_type=base["AssetType"],
            buy_sell="Sell" if amount > 0 else "Buy",
            amount=abs(amount),
            order_type="Market",
        )

        cancelled: list[str] = []
        for order in await self.working_orders_for_uic(uic):
            order_id = order.get("OrderId")
            if not order_id:
                continue
            try:
                await self.cancel_order(order_id)
                cancelled.append(order_id)
            except SaxoError:
                # Report what did get cancelled rather than failing the close.
                pass
        return result, cancelled
