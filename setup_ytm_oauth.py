#!/usr/bin/env python3
"""
YouTube Music OAuth setup helper
================================
Run this ONCE on your own machine to obtain a long-lived YouTube Music refresh
token, then paste the printed values into your GitHub repository secrets.

Prerequisites (see README.md, "Paso 1"):
1. A Google Cloud project with the **YouTube Data API v3** enabled.
2. An OAuth client of type **TVs and Limited Input devices**.
3. The OAuth consent screen **published** (in "Testing" mode Google revokes
   refresh tokens after 7 days).

Usage::

    python setup_ytm_oauth.py                 # asks for client id/secret
    python setup_ytm_oauth.py --save-file     # also writes oauth.json (gitignored)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ytmusicapi import OAuthCredentials, YTMusic, setup_oauth

from config import env

OAUTH_FILE = Path(__file__).resolve().parent / "oauth.json"
SEPARATOR = "=" * 70


def prompt(label: str, current: str) -> str:
    if current:
        print(f"{label}: loaded from environment.")
        return current
    value = input(f"{label}: ").strip()
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a YouTube Music OAuth refresh token.")
    parser.add_argument(
        "--save-file",
        action="store_true",
        help="also store the full token in oauth.json (already gitignored)",
    )
    args = parser.parse_args()

    print(SEPARATOR)
    print("YouTube Music OAuth Token Generator")
    print(SEPARATOR)
    print("Needs a Google Cloud OAuth client of type 'TVs and Limited Input devices'")
    print("with the YouTube Data API v3 enabled. Details in README.md.\n")

    client_id = prompt("YTM_OAUTH_CLIENT_ID", env("YTM_OAUTH_CLIENT_ID"))
    client_secret = prompt("YTM_OAUTH_CLIENT_SECRET", env("YTM_OAUTH_CLIENT_SECRET"))

    if not client_id or not client_secret:
        print("\nERROR: both client id and client secret are required.")
        return 1

    print("\nA browser window will open with a code to authorise the app.")
    print("Sign in with the SAME Google account you use in YouTube Music.\n")

    token = setup_oauth(
        client_id=client_id,
        client_secret=client_secret,
        filepath=str(OAUTH_FILE) if args.save_file else None,
        open_browser=True,
    )

    refresh_token = token.refresh_token

    # Verify the brand new token really reaches the library.
    print("\nVerifying the token against your YouTube Music library...")
    try:
        client = YTMusic(
            auth=token.as_dict(),
            oauth_credentials=OAuthCredentials(client_id=client_id, client_secret=client_secret),
        )
        account = client.get_account_info().get("accountName", "unknown account")
        liked = client.get_liked_songs(limit=1)
        print(f"OK - authenticated as '{account}', liked songs playlist reachable "
              f"({liked.get('trackCount', '?')} tracks).")
    except Exception as exc:  # noqa: BLE001 - diagnostics, keep the token output either way
        print(f"WARNING: the token was created but the verification call failed: {exc}")
        print("Check that the YouTube Data API v3 is enabled for this Google Cloud project.")

    print("\n" + SEPARATOR)
    print("SUCCESS - add these three repository secrets on GitHub")
    print("(Settings -> Secrets and variables -> Actions):")
    print(SEPARATOR)
    print(f"\nYTM_OAUTH_CLIENT_ID={client_id}")
    print(f"YTM_OAUTH_CLIENT_SECRET={client_secret}")
    print(f"YTM_OAUTH_REFRESH_TOKEN={refresh_token}\n")
    print(SEPARATOR)
    print("Once they are set you can delete the old YTM_HEADERS_JSON secret.")
    if args.save_file:
        print(f"Full token also written to {OAUTH_FILE.name} (gitignored - never commit it).")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nAborted.")
        sys.exit(130)
