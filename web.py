"""Launch pgweb against the local embedded Postgres.

Usage:  uv run python web.py [--port 8081]

Requires pgweb on your PATH:
  macOS:  brew install pgweb
  Linux:  go install github.com/sosedoff/pgweb@latest
  other:  https://github.com/sosedoff/pgweb/releases
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import urllib.parse

import db


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", default="8081", help="pgweb listen port (default 8081)")
    args = parser.parse_args()

    pgweb = shutil.which("pgweb")
    if pgweb is None:
        sys.exit(
            "pgweb not found on PATH.\n"
            "Install it:  brew install pgweb  (macOS)\n"
            "             https://github.com/sosedoff/pgweb/releases  (other)"
        )

    with db.connect() as conn:
        info = conn.info
        host = info.host
        if not host or "/" in host or "\\" in host:
            host = "127.0.0.1"
        user = urllib.parse.quote(info.user or "", safe="")
        dbname = urllib.parse.quote(info.dbname or "", safe="")
        url = (
            f"postgres://{user}@{host}:{info.port}/{dbname}"
            f"?sslmode=disable"
        )
        print(f"database -> host={host} port={info.port} dbname={info.dbname}")

    print(f"pgweb -> http://localhost:{args.port}")
    subprocess.run([pgweb, "--url", url, "--listen", args.port], check=False)


if __name__ == "__main__":
    main()
