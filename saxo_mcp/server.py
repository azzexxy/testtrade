"""MCP server exposing Saxo Bank OpenAPI read-only tools to Claude.

Every tool in this module is read-only. Order placement is deliberately
absent until the OAuth refresh flow lands — a 24-hour token is the wrong
credential to hang trade execution on.
"""

from __future__ import annotations

import asyncio
from typing import Any

from mcp.server.mcpserver import MCPServer

from .analysis import observations, parse_snapshot, plan_trade
from .client import SaxoClient, SaxoError
from .config import ConfigError, load_config

# FX majors plus gold — liquid, tradable around the clock, and quotable
# without a market-data subscription.
WATCHLIST: dict[str, int] = {
    "EURUSD": 21,
    "GBPUSD": 31,
    "USDJPY": 42,
    "USDCHF": 39,
    "AUDUSD": 4,
    "USDCAD": 38,
    "NZDUSD": 37,
    "EURGBP": 17,
    "EURJPY": 18,
    "GBPJPY": 26,
    "XAUUSD": 8176,
}

CAVEAT = (
    "This is description, not prediction. The account has no chart service, so "
    "there is no history here — only the current session's range, the cost of "
    "dealing and where price sits between them. None of it is backtested and "
    "none of it implies an edge."
)

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
    stop_loss: float | None = None,
    take_profit: float | None = None,
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

    # A stop on the wrong side of the entry is not protection: it fills at once.
    reference = price if price is not None else None
    if stop_loss is not None and reference is not None:
        if side == "Buy" and stop_loss >= reference:
            return f"A buy's stop must sit below the entry; {stop_loss} is not below {reference}."
        if side == "Sell" and stop_loss <= reference:
            return f"A sell's stop must sit above the entry; {stop_loss} is not above {reference}."

    where = "LIVE (real money)" if client.config.is_live else "SIM (simulated)"
    detail = (
        f"{side} {amount:,.0f} of Uic {uic} ({asset_type}) as {order_type}"
        + (f" @ {price}" if price is not None else " at market")
        + f" on {where}"
    )
    if stop_loss is not None:
        detail += f"\n  stop-loss at {stop_loss}"
    if take_profit is not None:
        detail += f"\n  take-profit at {take_profit}"
    if stop_loss is None:
        detail += "\n  NO STOP-LOSS — this position has nothing limiting its loss."

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
            stop_loss=stop_loss,
            take_profit=take_profit,
        )
    except SaxoError as exc:
        return f"Order rejected: {exc}"

    related = [o.get("OrderId") for o in result.get("Orders", [])]
    extra = f"\nProtective orders: {', '.join(filter(None, related))}" if related else ""
    return f"Placed on {where}: {detail}\nOrderId: {result.get('OrderId')}{extra}"


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
        result, cancelled = await client.close_position(position_id)
    except SaxoError as exc:
        return f"Close failed: {exc}"

    tail = ""
    if cancelled:
        tail = (
            f"\nCancelled {len(cancelled)} leftover order(s) on this instrument: "
            f"{', '.join(cancelled)}\n"
            "Saxo leaves a position's stop working after the position is gone, and "
            "an orphaned stop would open a new position in the opposite direction."
        )
    return (
        f"Closing order sent on {where}.\n{detail}\n"
        f"OrderId: {result.get('OrderId')}{tail}"
    )


@mcp.tool(
    description=(
        "Full analysis of one instrument: session range, where price sits in it, "
        "dealing cost against that range, plus a sized trade plan with a stop and "
        "target for the given direction. Use this before advising on a trade, and "
        "relay its caveats rather than presenting the numbers as a forecast."
    )
)
async def analyze_instrument(
    uic: int,
    asset_type: str = "FxSpot",
    direction: str = "buy",
    risk_pct: float = 1.0,
    stop: float | None = None,
    reward_risk: float = 2.0,
) -> str:
    client = await _get_client()

    try:
        raw = await client.snapshot(uic, asset_type)
        balance = await client.balance()
    except SaxoError as exc:
        return f"Error: {exc}"

    snap = parse_snapshot(raw, amount=10000)
    if snap is None:
        return (
            f"No usable price for Uic {uic}. Equities need a per-exchange market "
            "data subscription; FX does not."
        )

    equity = balance.get("TotalValue") or balance.get("CashBalance") or 0
    currency = balance.get("Currency", "")
    dp = snap.decimals

    lines = [
        f"{snap.symbol} — {snap.description}",
        f"  bid {snap.bid:.{dp}f} / ask {snap.ask:.{dp}f}   spread {snap.spread:.{dp}f}",
        f"  session {snap.low:.{dp}f} – {snap.high:.{dp}f}   change {snap.percent_change:+.2f}%",
        "",
        "What the numbers say:",
    ]
    lines += [f"  - {note}" for note in observations(snap)]

    plan = plan_trade(snap, direction, equity, risk_pct, stop, reward_risk)
    if plan is None:
        lines += ["", "No trade plan: the stop would sit on the entry price."]
    else:
        over = plan.size > client.config.max_order_amount
        lines += [
            "",
            f"Trade plan — {plan.direction}, risking {risk_pct:.2f}% of "
            f"{equity:,.0f} {currency}:",
            f"  entry   {plan.entry:.{dp}f}",
            f"  stop    {plan.stop:.{dp}f}   ({plan.stop_distance:.{dp}f} away)",
            f"  target  {plan.target:.{dp}f}   ({plan.reward_risk:.1f}:1 reward to risk)",
            f"  size    {plan.size:,.0f} units",
            f"  risking {plan.risk_cash:,.2f} {currency} if the stop is hit",
            f"  dealing cost {plan.cost:,.2f} — {plan.cost_pct_of_risk:.1f}% of what you risk",
        ]
        if over:
            lines.append(
                f"  NOTE size exceeds the {client.config.max_order_amount:,.0f} cap, "
                "because the stop is close. Widen the stop or lower risk_pct rather "
                "than raising the cap."
            )
        lines.append(
            f"  To place it: place_order(uic={uic}, asset_type='{asset_type}', "
            f"buy_sell='{plan.direction}', amount=<size>, "
            f"stop_loss={plan.stop:.{dp}f}, confirm=True)"
        )

    lines += ["", CAVEAT]
    return "\n".join(lines)


@mcp.tool(
    description=(
        "Scan the FX majors and gold, ranked by how small the spread is against "
        "the session's range — that is, which are cheapest to trade relative to "
        "the movement on offer. This ranks tradability, not attractiveness, and "
        "predicts nothing about direction."
    )
)
async def scan_watchlist(limit: int = 11) -> str:
    client = await _get_client()

    rows = []
    failures = []
    for symbol, uic in WATCHLIST.items():
        try:
            raw = await client.snapshot(uic, "FxSpot")
        except SaxoError as exc:
            failures.append(f"{symbol}: {exc}")
            continue
        snap = parse_snapshot(raw, amount=10000)
        if snap is None:
            failures.append(f"{symbol}: no price")
            continue
        rows.append((symbol, uic, snap))

    if not rows:
        return "Nothing quotable.\n" + "\n".join(failures)

    # Cheapest dealing cost relative to the day's move comes first. Instruments
    # with no range yet sort last: nothing to measure against.
    rows.sort(key=lambda r: r[2].spread_pct_of_range or float("inf"))

    out = [
        f"{'symbol':<8} {'uic':>5} {'chg%':>7} {'range%':>7} {'cost%':>6} {'in range':>9}",
        "-" * 50,
    ]
    for symbol, uic, snap in rows[:limit]:
        cost = snap.spread_pct_of_range
        rng = snap.range_pct
        pos = snap.range_position
        if pos is None:
            where_in_range = "-"
        elif pos > 1:
            where_in_range = "above"
        elif pos < 0:
            where_in_range = "below"
        else:
            where_in_range = f"{pos:.0%}"
        out.append(
            f"{symbol:<8} {uic:>5} {snap.percent_change:>+7.2f} "
            f"{(f'{rng:.2f}' if rng is not None else '   -'):>7} "
            f"{(f'{cost:.1f}' if cost is not None else '  -'):>6} "
            f"{where_in_range:>9}"
        )

    out += [
        "",
        "cost% is the spread as a share of the session range — lower is cheaper "
        "to trade. in range is where price sits between the session low and high.",
        "",
        "Ranked by dealing cost alone. A cheap spread is not a reason to trade, "
        "and nothing here says which way anything is going.",
        "",
        CAVEAT,
    ]
    if failures:
        out += ["", "Not quotable: " + "; ".join(failures)]
    return "\n".join(out)


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
