"""Sign in to Saxo and store OAuth tokens. Run: python scripts/login.py

Opens the Saxo login page in your browser, catches the redirect on
localhost, exchanges the code for tokens and writes them to
token_store.<env>.json. After this the server refreshes on its own until
the refresh token lapses.
"""

from __future__ import annotations

import secrets
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

if sys.version_info < (3, 10):
    sys.exit(
        f"saxo-mcp needs Python 3.10+; this is {sys.version.split()[0]}. "
        "Rebuild the virtualenv with a newer interpreter."
    )

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from saxo_mcp.auth import (  # noqa: E402
    AuthError,
    build_authorize_url,
    exchange_code,
    make_pkce,
    save_tokens,
)
from saxo_mcp.config import ConfigError, load_config  # noqa: E402

PAGE = b"""<!doctype html><meta charset="utf-8"><title>Saxo login</title>
<body style="font-family:system-ui;padding:3rem;max-width:32rem">
<h2>%s</h2><p>%s</p></body>"""


class Callback(BaseHTTPRequestHandler):
    result: dict[str, str] = {}

    def do_GET(self) -> None:  # noqa: N802
        query = parse_qs(urlparse(self.path).query)
        Callback.result = {k: v[0] for k, v in query.items()}

        ok = "code" in Callback.result
        self.send_response(200 if ok else 400)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        if ok:
            self.wfile.write(PAGE % (b"Signed in", b"You can close this tab."))
        else:
            error = Callback.result.get("error", "no code returned").encode()
            self.wfile.write(PAGE % (b"Login failed", error))

    def log_message(self, *args: object) -> None:
        pass  # keep the console clean


def main() -> int:
    try:
        config = load_config()
    except ConfigError as exc:
        print(f"config error: {exc}")
        return 1

    if not config.can_oauth:
        print("SAXO_APP_KEY is not set in .env — nothing to log in with.")
        return 1

    parsed = urlparse(config.redirect_uri)
    port = parsed.port or 80
    if parsed.hostname not in ("localhost", "127.0.0.1"):
        print(
            f"SAXO_REDIRECT_URI must point at localhost for this script to catch "
            f"the redirect; it is {config.redirect_uri!r}."
        )
        return 1

    verifier = challenge = None
    if not config.app_secret:
        verifier, challenge = make_pkce()

    state = secrets.token_urlsafe(16)
    url = build_authorize_url(config, state, challenge)

    try:
        server = HTTPServer(("127.0.0.1", port), Callback)
    except OSError as exc:
        print(f"Could not listen on port {port}: {exc}")
        print("Something else is probably using it. Free the port and retry.")
        return 1

    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()

    print(f"Environment: {config.env.upper()}")
    print(f"Auth mode:   {'PKCE' if verifier else 'client secret'}")
    print("\nOpening your browser. If nothing happens, paste this in yourself:\n")
    print(url + "\n")
    webbrowser.open(url)

    print(f"Waiting for the redirect on port {port} ...")
    thread.join(timeout=300)
    server.server_close()

    result = Callback.result
    if not result:
        print("Timed out after 5 minutes with no redirect.")
        return 1
    if "error" in result:
        print(f"Saxo refused the login: {result.get('error')} "
              f"{result.get('error_description', '')}")
        return 1
    if result.get("state") != state:
        print("State mismatch — discarding this response rather than trusting it.")
        return 1
    if "code" not in result:
        print(f"No authorization code in the redirect: {result}")
        return 1

    try:
        tokens = exchange_code(config, result["code"], verifier)
        save_tokens(config, tokens)
    except AuthError as exc:
        print(f"Token exchange failed: {exc}")
        return 1

    mins = (tokens.expires_at - time.time()) / 60
    print(f"\nSigned in. Tokens written to {config.token_store_path.name}")
    print(f"Access token valid ~{mins:.0f} min; refreshes automatically from here.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
