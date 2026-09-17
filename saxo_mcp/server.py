"""MCP server exposing Saxo Bank OpenAPI read-only tools to Claude.

Every tool in this module is read-only. Order placement is deliberately
absent until the OAuth refresh flow lands — a 24-hour token is the wrong
credential to hang trade execution on.
"""

from __future__ import annotations

import asyncio
from typing import Any

from mcp.server.mcpserver import MCPServer

from .client import SaxoClient, SaxoError
from .config import ConfigError, load_config

mcp = MCPServer(
    name="saxo",
    version="0.1.0",
    instructions=(
        "Read-only access to a Saxo Bank account via the OpenAPI gateway. "
        "Check connection_status first to confirm which environment (sim or live) "
        "is active before reporting any figure to the user."
    ),
)

_client: SaxoClient | None = None
_lock = asyncio.Lock()


async def _get_client() -> SaxoClient:
    global _client
    async with _lock:
        if _client is None:
            _client = SaxoClient()
        return _client


def _fmt_money(value: Any, currency: str = "") -> str:
    try:
        return f"{float(value):,.2f} {currency}".strip()
    except (TypeError, ValueError):
        return f"{value} {currency}".strip()


@mcp.tool(description="Show which Saxo environment is active and whether the token works.")
async def connection_status() -> str:
    try:
        client = await _get_client()
    except ConfigError as exc:
        return f"Not configured: {exc}"

    try:
        user = await client.user_info()
    except SaxoError as exc:
        return f"Configured for {client.config.env.upper()} but the call failed: {exc}"

    marker = "LIVE — real money" if client.config.is_live else "SIM — simulated money"
    return (
        f"Environment: {marker}\n"
        f"Gateway: {client.config.gateway}\n"
        f"User: {user.get('Name')} (id {user.get('UserId')})\n"
        f"Market data terms accepted: {user.get('MarketDataViaOpenApiTermsAccepted')}"
    )


@mcp.tool(description="Get cash balance, margin available, and unrealized P/L.")
async def get_balance() -> str:
    client = await _get_client()
    try:
        data = await client.balance()
    except SaxoError as exc:
        return f"Error: {exc}"

    ccy = data.get("Currency", "")
    lines = [
        f"Environment: {client.config.env.upper()}",
        f"Cash balance: {_fmt_money(data.get('CashBalance'), ccy)}",
        f"Total value: {_fmt_money(data.get('TotalValue'), ccy)}",
        f"Available for trading: {_fmt_money(data.get('MarginAvailableForTrading'), ccy)}",
        f"Margin used: {_fmt_money(data.get('MarginUsedByCurrentPositions'), ccy)}",
        f"Unrealized P/L: {_fmt_money(data.get('UnrealizedPositionsValue'), ccy)}",
        f"Open positions: {data.get('OpenPositionsCount', 0)}",
    ]
    return "\n".join(lines)


@mcp.tool(description="List the accounts belonging to this Saxo client.")
async def list_accounts() -> str:
    client = await _get_client()
    try:
        data = await client.accounts()
    except SaxoError as exc:
        return f"Error: {exc}"

    rows = data.get("Data", [])
    if not rows:
        return "No accounts found."

    out = []
    for account in rows:
        trial = " [trial]" if account.get("IsTrialAccount") else ""
        out.append(
            f"{account.get('AccountId')} — {account.get('Currency')} "
            f"({account.get('AccountType')}){trial}\n"
            f"  key: {account.get('AccountKey')}"
        )
    return "\n".join(out)


@mcp.tool(description="List all open positions with entry price and unrealized P/L.")
async def get_positions() -> str:
    client = await _get_client()
    try:
        data = await client.positions()
    except SaxoError as exc:
        return f"Error: {exc}"

    rows = data.get("Data", [])
    if not rows:
        return "No open positions."

    out = []
    for position in rows:
        base = position.get("PositionBase", {})
        view = position.get("PositionView", {})
        fmt = position.get("DisplayAndFormat", {})
        out.append(
            f"{fmt.get('Symbol', base.get('Uic'))} — {fmt.get('Description', '')}\n"
            f"  amount {base.get('Amount')} @ {base.get('OpenPrice')}\n"
            f"  current {view.get('CurrentPrice')}  "
            f"P/L {_fmt_money(view.get('ProfitLossOnTrade'), fmt.get('Currency', ''))}"
        )
    return "\n".join(out)


@mcp.tool(description="List working (not yet filled) orders.")
async def get_orders() -> str:
    client = await _get_client()
    try:
        data = await client.orders()
    except SaxoError as exc:
        return f"Error: {exc}"

    rows = data.get("Data", [])
    if not rows:
        return "No working orders."

    out = []
    for order in rows:
        fmt = order.get("DisplayAndFormat", {})
        out.append(
            f"{order.get('OrderId')}: {order.get('BuySell')} {order.get('Amount')} "
            f"{fmt.get('Symbol', order.get('Uic'))} "
            f"{order.get('OpenOrderType')} @ {order.get('Price')} "
            f"[{order.get('Status')}]"
        )
    return "\n".join(out)


@mcp.tool(
    description=(
        "Search tradable instruments by name or ticker. Returns the Uic and "
        "AssetType needed by get_quote."
    )
)
async def search_instruments(
    keywords: str, asset_types: str = "Stock,FxSpot,Etf", limit: int = 10
) -> str:
    client = await _get_client()
    try:
        data = await client.search_instruments(keywords, asset_types, limit)
    except SaxoError as exc:
        return f"Error: {exc}"

    rows = data.get("Data", [])
    if not rows:
        return f"No instruments matched {keywords!r}."

    out = []
    for item in rows:
        out.append(
            f"{item.get('Symbol')} — {item.get('Description')}\n"
            f"  Uic {item.get('Identifier')}  AssetType {item.get('AssetType')}  "
            f"{item.get('ExchangeId')} {item.get('CurrencyCode')}"
        )
    return "\n".join(out)


@mcp.tool(
    description=(
        "Get the current bid/ask quote for an instrument. Needs the Uic and "
        "AssetType from search_instruments."
    )
)
async def get_quote(uic: int, asset_type: str = "Stock", amount: float = 1) -> str:
    client = await _get_client()
    try:
        data = await client.info_price(uic, asset_type, amount)
    except SaxoError as exc:
        return f"Error: {exc}"

    quote = data.get("Quote", {})
    fmt = data.get("DisplayAndFormat", {})
    symbol = fmt.get("Symbol", f"Uic {uic}")

    if quote.get("PriceTypeBid") == "NoAccess":
        return (
            f"{symbol}: no market-data access for this instrument.\n"
            "Accept the market data terms at developer.saxo, or subscribe to the "
            "exchange feed. FX spot generally works without a subscription."
        )

    delay = quote.get("DelayedByMinutes", 0)
    delay_note = f" (delayed {delay}m)" if delay else ""
    return (
        f"{symbol} — {fmt.get('Description', '')}\n"
        f"Bid {quote.get('Bid')} / Ask {quote.get('Ask')} "
        f"(mid {quote.get('Mid')}){delay_note}\n"
        f"Market: {quote.get('MarketState', 'unknown')}  "
        f"Source: {quote.get('PriceSource', 'unknown')}"
    )


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
