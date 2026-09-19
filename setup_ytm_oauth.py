#!/usr/bin/env python3
"""
YouTube OAuth setup helper
==========================
Run this ONCE on your own machine to obtain a long-lived YouTube refresh token,
then paste the printed values into your GitHub repository secrets.

Prerequisites (see README.md, "Paso 1"):
1. A Google Cloud project with the **YouTube Data API v3** enabled.
2. An OAuth client of type **TVs and Limited Input devices**.
3. The OAuth consent screen **published** (in "Testing" mode Google revokes
   refresh tokens after 7 days).

Usage::

    python setup_ytm_oauth.py                 # asks for client id/secret
    python setup_ytm_oauth.py --save-file     # also writes oauth.json (gitignored)

The script prints a URL + code, then waits until you finish the login in the
browser - no need to press Enter.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import webbrowser
from pathlib import Path
from typing import Any

import requests

from config import env
from youtube_data import (
    GOOGLE_DEVICE_CODE_URL,
    GOOGLE_TOKEN_URL,
    REQUEST_TIMEOUT,
    SCOPE_YOUTUBE_READONLY,
    YouTubeDataClient,
)

OAUTH_FILE = Path(__file__).resolve().parent / "oauth.json"
SEPARATOR = "=" * 70
DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"


def prompt(label: str, current: str) -> str:
    if current:
        print(f"{label}: loaded from environment.")
        return current
    return input(f"{label}: ").strip()


def request_device_code(session: requests.Session, client_id: str) -> dict[str, Any]:
    response = session.post(
        GOOGLE_DEVICE_CODE_URL,
        data={"client_id": client_id, "scope": SCOPE_YOUTUBE_READONLY},
        timeout=REQUEST_TIMEOUT,
    )
    payload = response.json()
    if response.status_code >= 400 or "device_code" not in payload:
        raise SystemExit(
            f"Google refused to start the device flow: {payload.get('error', response.status_code)} "
            f"{payload.get('error_description', '')}\n"
            "Check the client id and that the OAuth client type is 'TVs and Limited Input devices'."
        )
    return payload


def poll_for_token(
    session: requests.Session, client_id: str, client_secret: str, code: dict[str, Any]
) -> dict[str, Any]:
    interval = max(int(code.get("interval", 5)), 5)
    deadline = time.time() + int(code.get("expires_in", 1800))
    while time.time() < deadline:
        response = session.post(
            GOOGLE_TOKEN_URL,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "device_code": code["device_code"],
                "grant_type": DEVICE_GRANT,
            },
            timeout=REQUEST_TIMEOUT,
        )
        payload = response.json()
        error = payload.get("error")
        if not error and "refresh_token" in payload:
            return payload
        if error == "authorization_pending":
            time.sleep(interval)
            continue
        if error == "slow_down":
            interval += 5
            time.sleep(interval)
            continue
        raise SystemExit(
            f"Authorisation failed: {error} {payload.get('error_description', '')}\n"
            "If it says access_denied, the account is not allowed to use this app: either publish "
            "the consent screen or add the account under Audience -> Test users."
        )
    raise SystemExit("The device code expired before the login was completed. Run the script again.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a YouTube OAuth refresh token.")
    parser.add_argument(
        "--save-file",
        action="store_true",
        help="also store the full token in oauth.json (already gitignored)",
    )
    parser.add_argument("--no-browser", action="store_true", help="do not open the browser automatically")
    args = parser.parse_args()

    print(SEPARATOR)
    print("YouTube OAuth Token Generator")
    print(SEPARATOR)
    print("Needs a Google Cloud OAuth client of type 'TVs and Limited Input devices'")
    print("with the YouTube Data API v3 enabled. Details in README.md.\n")

    client_id = prompt("YTM_OAUTH_CLIENT_ID", env("YTM_OAUTH_CLIENT_ID"))
    client_secret = prompt("YTM_OAUTH_CLIENT_SECRET", env("YTM_OAUTH_CLIENT_SECRET"))
    if not client_id or not client_secret:
        print("\nERROR: both client id and client secret are required.")
        return 1

    session = requests.Session()
    code = request_device_code(session, client_id)
    url = f"{code['verification_url']}?user_code={code['user_code']}"

    print("\n1. Open this URL and sign in with the SAME Google account you use in YouTube Music:")
    print(f"   {url}")
    print(f"2. Code (if asked for it separately): {code['user_code']}")
    print("3. Accept the YouTube read-only permission.")
    print("\nWaiting for you to finish the login (Ctrl-C to abort)...")
    if not args.no_browser:
        webbrowser.open(url)

    token = poll_for_token(session, client_id, client_secret, code)
    refresh_token = token["refresh_token"]
    print("\nAuthorised.")

    if "refresh_token_expires_in" in token:
        days = int(token["refresh_token_expires_in"]) // 86400
        print(
            f"\nWARNING: Google says this refresh token expires in {days} day(s). That means the "
            "OAuth consent screen is still in 'Testing' mode. Publish it and run this script again "
            "to get a token that does not expire."
        )

    print("\nVerifying the token against your YouTube likes...")
    try:
        client = YouTubeDataClient(token["access_token"], session)
        channel = client.channel_title() or "unknown channel"
        liked = client.liked_music(limit=3)
        print(f"OK - authenticated as '{channel}'. Most recent liked songs:")
        for track in liked:
            print(f"  - {track.artist} - {track.title}")
    except Exception as exc:  # noqa: BLE001 - diagnostics, keep the token output either way
        print(f"WARNING: the token was created but the verification call failed: {exc}")
        print("Check that the YouTube Data API v3 is enabled for this Google Cloud project.")

    if args.save_file:
        payload = {
            "scope": token.get("scope", SCOPE_YOUTUBE_READONLY),
            "token_type": token.get("token_type", "Bearer"),
            "access_token": token["access_token"],
            "refresh_token": refresh_token,
            "expires_at": int(time.time()) + int(token.get("expires_in", 0)),
            "expires_in": int(token.get("expires_in", 0)),
        }
        OAUTH_FILE.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

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
