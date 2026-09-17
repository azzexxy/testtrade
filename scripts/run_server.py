"""Launcher for the Saxo MCP server.

Resolves the package relative to this file, so the server starts from any
working directory and without installing the package. Point your MCP client
at this script.
"""

from __future__ import annotations

import sys
from pathlib import Path

if sys.version_info < (3, 10):
    sys.exit(
        f"saxo-mcp needs Python 3.10+; this is {sys.version.split()[0]}. "
        "Rebuild the virtualenv with a newer interpreter."
    )

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from saxo_mcp.server import main  # noqa: E402

if __name__ == "__main__":
    main()
