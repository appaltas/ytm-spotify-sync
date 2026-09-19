#!/usr/bin/env python3
"""
YouTube Music authentication
============================
OAuth-first authentication layer for ytmusicapi with explicit detection of
expired / revoked sessions.

Why OAuth: browser cookies (``YTM_HEADERS_JSON``) are tied to a Google web
session. Google rotates and invalidates those cookies, and it does so much
faster when the same cookie is replayed from a datacenter IP (a GitHub Actions
runner). When that happens YouTube Music answers with the *signed out* page
instead of the library, which surfaces as a cryptic
``KeyError: 'twoColumnBrowseResultsRenderer'``. An OAuth refresh token issued to
your own Google Cloud client does not expire on its own, so unattended syncs
keep working.

Credential precedence:
1. ``YTM_OAUTH_JSON``          - full oauth token JSON (string or file path)
2. ``YTM_OAUTH_REFRESH_TOKEN`` - just the refresh token (recommended for CI)
3. ``YTM_HEADERS_JSON``        - legacy browser headers (deprecated fallback)
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Optional

from ytmusicapi import OAuthCredentials, YTMusic

from config import env

logger = logging.getLogger("ytm_spotify_sync.auth")

OAUTH_SCOPE = "https://www.googleapis.com/auth/youtube"

#: Fingerprints of a "you are signed out" answer from YouTube Music, or of a
#: refresh token Google no longer accepts.
_EXPIRED_MARKERS = (
    "twocolumnbrowseresultsrenderer",  # signed-out library layout
    "singlecolumnbrowseresultsrenderer",
    "signinendpoint",
    "sign in to listen",
    "activeaccountheaderrenderer",
    "invalid_grant",
    "token has been expired or revoked",
    "unauthorized_client",
    "invalid_client",
)

OAUTH_REMEDIATION = (
    "The YouTube Music OAuth token was rejected. Regenerate it locally with\n"
    "    python setup_ytm_oauth.py\n"
    "and update the YTM_OAUTH_REFRESH_TOKEN repository secret.\n"
    "Also make sure the OAuth consent screen is PUBLISHED: while it is in "
    "'Testing' mode Google expires refresh tokens after 7 days."
)

BROWSER_REMEDIATION = (
    "The YouTube Music browser session (YTM_HEADERS_JSON) has expired. Cookie "
    "auth is deprecated and dies regularly when replayed from CI runners.\n"
    "Migrate to OAuth (recommended, it does not expire on its own):\n"
    "    python setup_ytm_oauth.py\n"
    "then set YTM_OAUTH_CLIENT_ID, YTM_OAUTH_CLIENT_SECRET and "
    "YTM_OAUTH_REFRESH_TOKEN as repository secrets.\n"
    "See README.md -> 'La sesion de YouTube Music ha caducado'."
)

MISSING_REMEDIATION = (
    "No usable YouTube Music credentials. Run 'python setup_ytm_oauth.py' and set "
    "YTM_OAUTH_CLIENT_ID, YTM_OAUTH_CLIENT_SECRET and YTM_OAUTH_REFRESH_TOKEN."
)


class YouTubeMusicAuthError(RuntimeError):
    """Raised when YouTube Music credentials are missing, invalid or expired."""

    def __init__(self, message: str, remediation: str = "") -> None:
        super().__init__(message)
        self.remediation = remediation


def looks_like_expired_session(error: BaseException | str) -> bool:
    """Heuristic: does this failure mean 'YouTube Music considers us signed out'?"""
    text = str(error).lower()
    return any(marker in text for marker in _EXPIRED_MARKERS)


def _normalize_oauth_token(data: dict[str, Any]) -> dict[str, Any]:
    """
    Builds the token payload ytmusicapi expects from whatever the user stored.

    ``expires_at`` is forced to 0 so the very first request always trades the
    refresh token for a fresh access token: CI runs are stateless, so a cached
    access token is always stale by the next run.
    """
    refresh_token = str(data.get("refresh_token") or "").strip()
    if not refresh_token:
        raise YouTubeMusicAuthError(
            "OAuth payload does not contain a 'refresh_token'.", MISSING_REMEDIATION
        )

    return {
        "scope": data.get("scope") or OAUTH_SCOPE,
        "token_type": data.get("token_type") or "Bearer",
        "access_token": data.get("access_token") or "",
        "refresh_token": refresh_token,
        "expires_at": 0,
        "expires_in": 0,
    }


def _load_oauth_token() -> Optional[dict[str, Any]]:
    """Reads the OAuth token from YTM_OAUTH_JSON or YTM_OAUTH_REFRESH_TOKEN."""
    raw = env("YTM_OAUTH_JSON")
    if raw:
        if os.path.isfile(raw):
            logger.info("Loading YouTube Music OAuth token from file '%s'.", raw)
            with open(raw, "r", encoding="utf-8") as handle:
                return _normalize_oauth_token(json.load(handle))
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise YouTubeMusicAuthError(
                f"YTM_OAUTH_JSON is neither valid JSON nor an existing file path: {exc}",
                MISSING_REMEDIATION,
            ) from exc
        if not isinstance(parsed, dict):
            raise YouTubeMusicAuthError("YTM_OAUTH_JSON must be a JSON object.", MISSING_REMEDIATION)
        logger.info("Loading YouTube Music OAuth token from YTM_OAUTH_JSON.")
        return _normalize_oauth_token(parsed)

    refresh_token = env("YTM_OAUTH_REFRESH_TOKEN")
    if refresh_token:
        logger.info("Loading YouTube Music OAuth token from YTM_OAUTH_REFRESH_TOKEN.")
        return _normalize_oauth_token({"refresh_token": refresh_token})

    return None


def build_ytmusic_client() -> tuple[YTMusic, str]:
    """
    Builds an authenticated YTMusic client.

    :return: tuple of (client, auth mode used: 'oauth' or 'browser')
    :raises YouTubeMusicAuthError: when credentials are missing or rejected.
    """
    token = _load_oauth_token()

    if token:
        client_id = env("YTM_OAUTH_CLIENT_ID")
        client_secret = env("YTM_OAUTH_CLIENT_SECRET")
        if not client_id or not client_secret:
            raise YouTubeMusicAuthError(
                "OAuth token provided but YTM_OAUTH_CLIENT_ID / YTM_OAUTH_CLIENT_SECRET are "
                "missing. A refresh token can only be redeemed by the client that issued it.",
                MISSING_REMEDIATION,
            )
        try:
            credentials = OAuthCredentials(client_id=client_id, client_secret=client_secret)
            client = YTMusic(auth=token, oauth_credentials=credentials)
            # Touch the token so an unusable refresh token fails here, loudly,
            # instead of halfway through the sync.
            _ = client._token.access_token  # noqa: SLF001 - intentional early validation
        except YouTubeMusicAuthError:
            raise
        except Exception as exc:
            raise YouTubeMusicAuthError(
                f"YouTube Music OAuth initialisation failed: {exc}", OAUTH_REMEDIATION
            ) from exc

        logger.info("YouTube Music client initialised using OAuth (refresh token).")
        return client, "oauth"

    raw_headers = env("YTM_HEADERS_JSON")
    if raw_headers:
        logger.warning(
            "Using deprecated browser-cookie authentication (YTM_HEADERS_JSON). These cookies "
            "expire regularly when replayed from CI - migrate to OAuth with "
            "'python setup_ytm_oauth.py'."
        )
        try:
            if os.path.isfile(raw_headers):
                client = YTMusic(auth=raw_headers)
            else:
                parsed = json.loads(raw_headers)
                if not isinstance(parsed, dict):
                    raise ValueError("YTM_HEADERS_JSON must be a JSON object of request headers.")
                client = YTMusic(auth=parsed)
        except Exception as exc:
            raise YouTubeMusicAuthError(
                f"Failed to initialise YouTube Music client from browser headers: {exc}",
                BROWSER_REMEDIATION,
            ) from exc
        return client, "browser"

    raise YouTubeMusicAuthError(
        "No YouTube Music credentials configured (YTM_OAUTH_REFRESH_TOKEN / YTM_OAUTH_JSON / "
        "YTM_HEADERS_JSON are all empty).",
        MISSING_REMEDIATION,
    )


def remediation_for(auth_mode: str) -> str:
    """Returns the actionable fix text for the auth mode in use."""
    return OAUTH_REMEDIATION if auth_mode == "oauth" else BROWSER_REMEDIATION


def describe_session(client: YTMusic, auth_mode: str) -> Optional[str]:
    """
    Best-effort identity probe. Returns the account name when available.

    A clear "signed out" answer is raised as YouTubeMusicAuthError so the run
    stops immediately with actionable output; anything else is only logged,
    because this probe must never be the reason a healthy sync fails.
    """
    try:
        info = client.get_account_info()
    except Exception as exc:
        if looks_like_expired_session(exc):
            raise YouTubeMusicAuthError(
                f"YouTube Music reports the session as signed out: {exc}",
                remediation_for(auth_mode),
            ) from exc
        logger.warning("Could not read YouTube Music account info (continuing anyway): %s", exc)
        return None

    account_name = info.get("accountName")
    if account_name:
        logger.info("Authenticated with YouTube Music as '%s'.", account_name)
    return account_name
