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
    note = (
        ""
        if client.auth_source.startswith("oauth")
        else "  (expires daily; run scripts/login.py for auto-refreshing OAuth)"
    )
    return (
        f"Environment: {marker}\n"
        f"Gateway: {client.config.gateway}\n"
        f"User: {user.get('Name')} (id {user.get('UserId')})\n"
        f"Auth: {client.auth_source}{note}\n"
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
            "Equity quotes need a subscription for that specific exchange, set up "
            "under market data on the Saxo portal — accepting the general terms is "
            "not enough on its own. FX spot needs no subscription and works now."
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


@mcp.tool(
    description=(
        "Place an order. Requires confirm=True to actually send it — called "
        "without confirm it returns a preview of what would be sent and places "
        "nothing. Get uic and asset_type from search_instruments first. Amount "
        "is in instrument units (FX: currency units, e.g. 10000; stocks: shares)."
    )
)
async def place_order(
    uic: int,
    asset_type: str,
    buy_sell: str,
    amount: float,
    order_type: str = "Market",
    price: float | None = None,
    confirm: bool = False,
) -> str:
    client = await _get_client()

    side = buy_sell.capitalize()
    if side not in ("Buy", "Sell"):
        return f"buy_sell must be 'Buy' or 'Sell', got {buy_sell!r}."
    if amount <= 0:
        return f"amount must be positive, got {amount}."
    if order_type.lower() == "limit" and price is None:
        return "A Limit order needs a price."

    limit = client.config.max_order_amount
    if amount > limit:
        return (
            f"Refusing: amount {amount:,.0f} exceeds the configured ceiling of "
            f"{limit:,.0f}. Raise SAXO_MAX_ORDER_AMOUNT in .env if this is intended."
        )

    where = "LIVE (real money)" if client.config.is_live else "SIM (simulated)"
    detail = (
        f"{side} {amount:,.0f} of Uic {uic} ({asset_type}) as {order_type}"
        + (f" @ {price}" if price is not None else " at market")
        + f" on {where}"
    )

    if not confirm:
        return (
            f"PREVIEW — nothing was sent.\n{detail}\n\n"
            "Call again with confirm=True to place this order."
        )

    try:
        result = await client.place_order(
            uic=uic,
            asset_type=asset_type,
            buy_sell=side,
            amount=amount,
            order_type=order_type,
            price=price,
        )
    except SaxoError as exc:
        return f"Order rejected: {exc}"

    return f"Placed on {where}: {detail}\nOrderId: {result.get('OrderId')}"


@mcp.tool(description="Cancel a working order by its OrderId.")
async def cancel_order(order_id: str) -> str:
    client = await _get_client()
    try:
        result = await client.cancel_order(order_id)
    except SaxoError as exc:
        return f"Cancel failed: {exc}"

    cancelled = result.get("Orders", []) if isinstance(result, dict) else []
    if not cancelled:
        return f"No order cancelled — is {order_id} still working?"
    return f"Cancelled order {order_id}."


@mcp.tool(
    description=(
        "Close an open position by placing the opposing market order. Requires "
        "confirm=True; without it, returns a preview of what would be closed."
    )
)
async def close_position(position_id: str, confirm: bool = False) -> str:
    client = await _get_client()

    try:
        data = await client.position(position_id)
    except SaxoError as exc:
        return f"Could not read position {position_id}: {exc}"

    base = data.get("PositionBase", {})
    view = data.get("PositionView", {})
    fmt = data.get("DisplayAndFormat", {})
    amount = base.get("Amount")
    if amount is None:
        return f"Position {position_id} has no amount to close."

    where = "LIVE (real money)" if client.config.is_live else "SIM (simulated)"
    side = "Sell" if amount > 0 else "Buy"
    detail = (
        f"{side} {abs(amount):,.0f} {fmt.get('Symbol', base.get('Uic'))} at market "
        f"to close position {position_id} on {where}\n"
        f"  opened at {base.get('OpenPrice')}, current P/L "
        f"{_fmt_money(view.get('ProfitLossOnTrade'), fmt.get('Currency', ''))}"
    )

    if not confirm:
        return (
            f"PREVIEW — nothing was sent.\n{detail}\n\n"
            "Call again with confirm=True to close it."
        )

    try:
        result = await client.close_position(position_id)
    except SaxoError as exc:
        return f"Close failed: {exc}"

    return f"Closing order sent on {where}.\n{detail}\nOrderId: {result.get('OrderId')}"


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
