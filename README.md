# saxo-mcp

An MCP server that gives Claude read-only access to a Saxo Bank account
through the [Saxo OpenAPI](https://www.developer.saxo/).

Currently wired to the **SIM (simulation) environment** with a 24-hour
developer token. Nothing here can place a trade.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
cp .env.example .env     # then paste your 24-hour token into SAXO_TOKEN
```

Get the token from [developer.saxo](https://www.developer.saxo/) under the
24-hour token section. It expires daily; regenerate and update `.env`.

Verify the connection:

```bash
.venv/bin/python scripts/smoke_test.py
```

## Using it from Claude

`.mcp.json` registers the server for Claude Code in this directory. Start
Claude Code here and approve the server when prompted. For Claude Desktop,
add the same block to `claude_desktop_config.json` with absolute paths.

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

## Safety rails

- `SAXO_ENV` defaults to `sim`. Reaching live needs `SAXO_ENV=live` **and**
  `SAXO_ALLOW_LIVE=yes` — two independent switches, and a typo in either
  fails back to SIM rather than through to real money.
- Every balance and status response states which environment produced it,
  so a figure can't be mistaken for the wrong account.
- No order-placement tool exists yet. See below.

## Market data

The SIM account starts with `MarketDataViaOpenApiTermsAccepted: false`,
so equity quotes return `NoAccess`. FX spot works without a subscription.
Accept the market data terms on the developer portal to enable stock quotes.

## Not done yet

- **OAuth refresh flow.** Access tokens last ~20 minutes and refresh tokens
  ~1 hour, so anything unattended needs the code grant from Step 3 rather
  than a 24-hour token.
- **Order placement.** Deliberately absent. A daily-expiring token pasted
  into a file is the wrong credential to hang trade execution on, and the
  order path needs its own confirmation rail before Claude can reach it.
- **Live environment.** Requires a Saxo app approval process, not a URL swap.
