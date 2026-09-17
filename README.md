# saxo-mcp

An MCP server that gives Claude read-only access to a Saxo Bank account
through the [Saxo OpenAPI](https://www.developer.saxo/).

Currently wired to the **SIM (simulation) environment** with a 24-hour
developer token. Nothing here can place a trade.

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

**OAuth (preferred).** Set `SAXO_APP_KEY` to your app's key from the
developer portal, leave `SAXO_APP_SECRET` empty to use PKCE, then:

```bash
.venv/bin/python scripts/login.py
```

That opens the Saxo login page, catches the redirect on localhost and
writes `token_store.sim.json`. From then on the server refreshes its own
access token — no daily paste. Your app's redirect URI must be registered
as exactly `http://localhost:8080/callback`.

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
| `place_order` | Place a market or limit order (needs `confirm=True`) |
| `cancel_order` | Cancel a working order |
| `close_position` | Close a position at market (needs `confirm=True`) |

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
- **Order types beyond market and limit.** No stops, no trailing stops, no
  OCO or other bracket orders. Which means a position opened through
  `place_order` has no attached protection — closing it is a manual step.
- **Any notion of risk.** No exposure limits, no daily loss cap, no
  position sizing. `SAXO_MAX_ORDER_AMOUNT` caps one order, nothing more.

## Verified on SIM

The full loop has been run end to end against the simulation account:
search EURUSD, quote it, place a 10,000-unit market buy, see the position
open, close it at market, confirm flat. Limit orders rest and cancel
correctly. The confirmation and size guards were tested by trying to
defeat them.
