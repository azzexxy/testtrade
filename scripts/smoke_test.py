"""Verify the Saxo connection end to end. Run: python scripts/smoke_test.py"""

from __future__ import annotations

import asyncio
import sys

from saxo_mcp.client import SaxoClient, SaxoError
from saxo_mcp.config import ConfigError, load_config


async def main() -> int:
    try:
        config = load_config()
    except ConfigError as exc:
        print(f"config error: {exc}")
        return 1

    print(f"environment : {config.env}")
    print(f"gateway     : {config.gateway}")

    client = SaxoClient(config)
    try:
        user = await client.user_info()
        print(f"user        : {user['Name']} (id {user['UserId']})")
        print(f"market data : terms accepted = {user.get('MarketDataViaOpenApiTermsAccepted')}")

        balance = await client.balance()
        print(
            f"balance     : {balance['CashBalance']:,.2f} {balance['Currency']}"
            f"  (available for trading {balance['MarginAvailableForTrading']:,.2f})"
        )

        positions = await client.positions()
        print(f"positions   : {positions.get('__count', 0)} open")

        orders = await client.orders()
        print(f"orders      : {orders.get('__count', 0)} working")

        quote = await client.info_price(uic=21, asset_type="FxSpot", amount=100_000)
        q = quote.get("Quote", {})
        print(f"EURUSD      : bid {q.get('Bid')} / ask {q.get('Ask')} [{q.get('MarketState')}]")
    except SaxoError as exc:
        print(f"api error: {exc}")
        return 1
    finally:
        await client.aclose()

    print("\nall checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
