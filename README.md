# saxo-mcp

An MCP server that connects Claude to a Saxo Bank account through the
[Saxo OpenAPI](https://www.developer.saxo/): read the portfolio, quote and
analyse instruments, and place orders with stops attached.

Currently wired to the **SIM (simulation) environment**. It can place real
orders there, and every order requires an explicit confirmation step. Read
"What the analysis is, and is not" before acting on anything it says.

## Requirements

Python **3.10 or newer**. Check this before anything else:

```bash
python3 --version
```

macOS ships Python 3.9 with the Xcode command line tools, and the MCP SDK
does not support it. If you see 3.9, install a newer interpreter first
(`brew install python@3.12`) and use `python3.12` in place of `python3`
below.

## Setup

```bash
git clone https://github.com/azzexxy/testtrade
cd testtrade
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
cp .env.example .env
```

Upgrading pip is not optional on a fresh macOS install: the bundled pip
21.2.4 predates PEP 660 and cannot install this project.

Now open `.env` and fill in one of the two credentials.

**OAuth (preferred).** Set `SAXO_APP_KEY` and `SAXO_APP_SECRET` to your
app's values from the developer portal, then:

```bash
.venv/bin/python scripts/login.py
```

That opens the Saxo login page, catches the redirect on localhost and
writes `token_store.sim.json`. From then on the server refreshes its own
access token — no daily paste. Your app's redirect URI must be registered
as exactly `http://localhost:8080/callback`.

The secret is not optional in practice. Leaving it empty selects PKCE,
which Saxo accepts only for apps created with PKCE enabled; an ordinary
app registration rejects the exchange with a bare `400 Bad Request`. The
portal shows the secret once at creation, so recreate the app if you lose
it.

**24-hour token (fallback).** Paste one after `SAXO_TOKEN=`. Simpler, but
it dies every day and cannot refresh itself.

If both are present, OAuth wins.

Verify:

```bash
.venv/bin/python scripts/smoke_test.py
```

You should see your name, a 1,000,000 EUR SIM balance, and a live EURUSD
quote.

## Using it from Claude

**Claude Code** — `.mcp.json` in this repo registers the server. Start
Claude Code from this directory and approve the server when prompted.

**Claude Desktop** — add this to `claude_desktop_config.json`, using
absolute paths:

```json
{
  "mcpServers": {
    "saxo": {
      "command": "/Users/lothar/testtrade/.venv/bin/python",
      "args": ["/Users/lothar/testtrade/scripts/run_server.py"]
    }
  }
}
```

The launcher resolves both the package and `.env` relative to its own
location, so it works from any working directory with no package install.

## Tools

| Tool | What it does |
|---|---|
| `connection_status` | Which environment is active, and whether the token works |
| `get_balance` | Cash, margin available, unrealized P/L |
| `list_accounts` | Accounts under this client |
| `get_positions` | Open positions with entry price and P/L |
| `get_orders` | Working orders |
| `search_instruments` | Find an instrument's Uic by name or ticker |
| `get_quote` | Current bid/ask for a Uic |
| `place_order` | Place a market or limit order, optionally with a stop and target (needs `confirm=True`) |
| `cancel_order` | Cancel a working order |
| `close_position` | Close a position at market and retire its stop (needs `confirm=True`) |
| `analyze_instrument` | Session range, dealing cost, and a sized trade plan |
| `scan_watchlist` | FX majors and gold ranked by dealing cost against range |

## What the analysis is, and is not

`analyze_instrument` and `scan_watchlist` describe. They do not forecast.

What they compute is real and checkable: the session's high and low, where
price sits between them, the spread as a share of that range, the session
range as a share of price, and a position size derived from your equity, a
risk percentage and a stop distance. The arithmetic is in `analysis.py` and
is unit-tested against hand-worked values.

What they cannot do is tell you what happens next. This account has no
access to Saxo's chart service, so there is no price history here at all —
no trend, no momentum, no moving averages, no backtest. A single session's
range is not a trend, and a narrow spread is a statement about cost, never
a reason to trade.

`scan_watchlist` therefore ranks by dealing cost relative to the available
move, which is an objective measure of *tradability*. It is not a ranking
of attractiveness, and it says nothing about direction.

Treat the output as evidence to reason over, not a recommendation to act on.

## Risk handling

`place_order` takes `stop_loss` and `take_profit`, sent as related orders
alongside the entry so a position is never briefly naked between two API
calls. A stop on the wrong side of a limit entry is refused rather than
sent. A preview without a stop says so in capitals.

Closing a position also cancels the protective orders left behind on that
instrument. Saxo keeps a stop working after its position is gone, and an
orphaned sell stop with nothing to sell will open a short if it triggers —
verified against SIM, not assumed.

Position sizing follows from the stop distance, so a tight stop implies a
large position. When the implied size exceeds `SAXO_MAX_ORDER_AMOUNT` the
plan says so and tells you to widen the stop or lower the risk, rather than
raise the cap.

## Safety rails

- `SAXO_ENV` defaults to `sim`. Reaching live needs `SAXO_ENV=live` **and**
  `SAXO_ALLOW_LIVE=yes` — two independent switches, and a typo in either
  fails back to SIM rather than through to real money.
- `place_order` and `close_position` do nothing unless called with
  `confirm=True`. Without it they return a preview of exactly what would be
  sent, so a loosely-worded request cannot become a trade in one step.
- `SAXO_MAX_ORDER_AMOUNT` (default 100,000 units) caps order size — a
  backstop against a misplaced decimal point.
- Every order, balance and status response names the environment it came
  from, so a figure or a fill can't be mistaken for the wrong account.

These rails are deliberately dumb and local. They are not a risk system:
nothing here checks your total exposure, correlation, or daily loss.

## Market data

FX spot quotes work out of the box, with no subscription.

Equities do not. Accepting the general market-data terms is necessary but
not sufficient — real-time equity prices need a subscription for each
specific exchange, arranged under market data on the Saxo portal. With
terms accepted and no exchange subscriptions, NASDAQ and Xetra both still
return `NoAccess`, so `get_quote` on a stock will tell you so rather than
return a price.

Orders on equities are a separate matter from quotes: you can place them
without a data subscription, you just cannot see the live price first.

## Authentication

Access tokens last about 20 minutes and refresh tokens about an hour. The
client fetches a token per request and refreshes 90 seconds ahead of
expiry, so a call never leaves with a token that dies in flight.

Tokens are stored per environment (`token_store.sim.json`,
`token_store.live.json`) at mode 0600, and both are gitignored. A SIM login
therefore can never be picked up as a live one.

If the refresh token itself lapses — an hour of inactivity — the next call
says so and you run `scripts/login.py` again.

## Not done yet

- **Live environment.** Requires a Saxo app approval process, not a URL swap.
  Do not point this at a real account until the OAuth flow above is in
  place; a daily-expiring token is not something to hang live execution on.
- **Price history.** The chart service returns 404 for this app, so there
  are no candles and therefore no indicators of any kind. If the app can be
  granted chart access on the portal, that is the single change that would
  most improve the analysis.
- **Trailing stops and OCO.** Stops and targets attach to an entry, but
  they do not trail, and there is no one-cancels-other handling beyond what
  Saxo does natively.
- **Portfolio-level risk.** Sizing is per trade. Nothing tracks total
  exposure, correlation between open positions, or a daily loss limit. Two
  trades each risking 1% can still be the same bet twice.

## Verified on SIM

The full loop has been run end to end against the simulation account:
search EURUSD, quote it, place a 10,000-unit market buy, see the position
open, close it at market, confirm flat. Limit orders rest and cancel
correctly. The confirmation and size guards were tested by trying to
defeat them.
