"""Descriptive metrics for an instrument, and risk arithmetic for a trade.

Nothing here forecasts anything. Every number is a fact about the current
session or a consequence of the account size and the stop distance you chose.
The judgement stays with the reader; this module only makes the judgement an
informed one.

The metrics available are limited by what the account can reach: with no
chart service there is no history, so this is one session's range, the cost
of dealing, and where price sits between the two.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class Snapshot:
    symbol: str
    description: str
    bid: float
    ask: float
    mid: float
    high: float
    low: float
    last_close: float
    net_change: float
    percent_change: float
    market_open: bool
    cost_buy: float
    cost_sell: float
    decimals: int
    amount: float

    @property
    def spread(self) -> float:
        return self.ask - self.bid

    @property
    def day_range(self) -> float:
        return self.high - self.low

    @property
    def range_position(self) -> float | None:
        """Where the mid sits in the session range: 0.0 at low, 1.0 at high."""
        if self.day_range <= 0:
            return None
        return (self.mid - self.low) / self.day_range

    @property
    def spread_pct_of_range(self) -> float | None:
        """Dealing cost measured against the day's whole move.

        Above roughly 10% the spread is eating the opportunity, whatever the
        chart looks like.
        """
        if self.day_range <= 0:
            return None
        return 100 * self.spread / self.day_range

    @property
    def range_pct(self) -> float | None:
        """Session range as a percentage of price — a crude volatility proxy."""
        if self.mid <= 0:
            return None
        return 100 * self.day_range / self.mid


def parse_snapshot(data: dict[str, Any], amount: float) -> Snapshot | None:
    """Build a Snapshot, or None when the feed withheld a usable price."""
    quote = data.get("Quote", {})
    info = data.get("PriceInfo", {})
    details = data.get("PriceInfoDetails", {})
    fmt = data.get("DisplayAndFormat", {})
    commissions = data.get("Commissions", {})
    instrument = data.get("InstrumentPriceDetails", {})

    bid, ask = quote.get("Bid"), quote.get("Ask")
    if not bid or not ask:
        return None

    mid = quote.get("Mid") or (bid + ask) / 2
    high, low = info.get("High"), info.get("Low")
    if high is None or low is None:
        high = low = mid

    return Snapshot(
        symbol=fmt.get("Symbol", str(data.get("Uic", "?"))),
        description=fmt.get("Description", ""),
        bid=bid,
        ask=ask,
        mid=mid,
        high=high,
        low=low,
        last_close=details.get("LastClose") or mid,
        net_change=info.get("NetChange") or 0.0,
        percent_change=info.get("PercentChange") or 0.0,
        market_open=bool(instrument.get("IsMarketOpen", True)),
        cost_buy=commissions.get("CostBuy") or 0.0,
        cost_sell=commissions.get("CostSell") or 0.0,
        decimals=fmt.get("Decimals", 5),
        amount=amount,
    )


@dataclass
class RiskPlan:
    direction: str
    entry: float
    stop: float
    target: float
    stop_distance: float
    reward_distance: float
    reward_risk: float
    size: float
    risk_cash: float
    cost: float
    cost_pct_of_risk: float


def plan_trade(
    snap: Snapshot,
    direction: str,
    equity: float,
    risk_pct: float = 1.0,
    stop: float | None = None,
    reward_risk: float = 2.0,
) -> RiskPlan | None:
    """Size a trade from the risk budget and the stop distance.

    The stop defaults to the far end of the session range, which is a
    structural level rather than a prediction: it is where the day's move
    would have to be undone for the idea to be wrong.
    """
    long = direction.lower() in ("buy", "long")
    entry = snap.ask if long else snap.bid

    if stop is None:
        stop = snap.low if long else snap.high

    stop_distance = abs(entry - stop)
    if stop_distance <= 0:
        return None

    reward_distance = stop_distance * reward_risk
    target = entry + reward_distance if long else entry - reward_distance

    risk_cash = equity * (risk_pct / 100)
    size = risk_cash / stop_distance
    cost = snap.cost_buy + snap.cost_sell

    return RiskPlan(
        direction="Buy" if long else "Sell",
        entry=entry,
        stop=stop,
        target=target,
        stop_distance=stop_distance,
        reward_distance=reward_distance,
        reward_risk=reward_risk,
        size=size,
        risk_cash=risk_cash,
        cost=cost,
        cost_pct_of_risk=(100 * cost / risk_cash) if risk_cash else 0.0,
    )


def observations(snap: Snapshot) -> list[str]:
    """Plain statements of what the numbers say — and what they do not."""
    notes: list[str] = []

    if not snap.market_open:
        notes.append("Market is closed; the quote is stale and not tradable.")

    position = snap.range_position
    if position is None:
        notes.append("No session range yet, so there is nothing to place price against.")
    elif position > 1:
        notes.append(
            "Price is above the session high — the range is being extended as you "
            "read this, so every level below is already out of date."
        )
    elif position < 0:
        notes.append(
            "Price is below the session low — the range is being extended as you "
            "read this, so every level above is already out of date."
        )
    elif position >= 0.8:
        notes.append(
            f"Trading at {position:.0%} of the session range — near the high. "
            "Buying here pays the day's premium; it is not evidence of strength."
        )
    elif position <= 0.2:
        notes.append(
            f"Trading at {position:.0%} of the session range — near the low. "
            "Cheap relative to the day, which says nothing about direction."
        )
    else:
        notes.append(
            f"Mid-range at {position:.0%} of the session — no positional edge either way."
        )

    cost_ratio = snap.spread_pct_of_range
    if cost_ratio is not None:
        if cost_ratio > 10:
            notes.append(
                f"Spread is {cost_ratio:.1f}% of the entire day's range. The cost of "
                "dealing is large next to the available move."
            )
        else:
            notes.append(f"Spread is {cost_ratio:.1f}% of the day's range.")

    volatility = snap.range_pct
    if volatility is not None:
        if volatility < 0.3:
            notes.append(
                f"Session range is {volatility:.2f}% of price — quiet. Fixed costs "
                "weigh more when there is less movement to capture."
            )
        elif volatility > 1.5:
            notes.append(
                f"Session range is {volatility:.2f}% of price — unusually wide. "
                "Stops need more room, so the same cash risk buys a smaller position."
            )
        else:
            notes.append(f"Session range is {volatility:.2f}% of price.")

    notes.append(
        f"Change on the session: {snap.percent_change:+.2f}%. One session is not "
        "a trend, and this account has no chart history to establish one."
    )
    return notes
