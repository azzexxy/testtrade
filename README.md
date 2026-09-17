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

Now open `.env` in an editor and paste your 24-hour token after
`SAXO_TOKEN=`. Get it from [developer.saxo](https://www.developer.saxo/)
under the 24-hour token section; it expires daily.

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
