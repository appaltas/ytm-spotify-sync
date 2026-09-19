#!/usr/bin/env python3
"""
Liked-songs sources and authentication
======================================
Decides how the user's YouTube Music likes are read and turns credential
failures into one actionable error type.

Two sources, in order of preference:

1. **OAuth + YouTube Data API v3** (``YTM_OAUTH_*``). A refresh token issued by
   the user's own Google Cloud client. Does not expire on its own once the
   consent screen is published, and the Data API is an official, documented
   surface - the right choice for unattended CI.
2. **Browser cookies + ytmusicapi** (``YTM_HEADERS_JSON``). Deprecated. Google
   invalidates those cookies regularly, much faster when they are replayed
   from a datacenter IP, and the failure surfaces as a cryptic
   ``KeyError: 'twoColumnBrowseResultsRenderer'`` (the signed-out page).

Note: ytmusicapi's own OAuth mode is *not* used. music.youtube.com's private
API rejects bearer tokens from third-party clients with HTTP 400 (see
sigma67/ytmusicapi#676), while the public Data API accepts the same token.
"""

from __future__ import annotations

import json
import logging
import os
from abc import ABC, abstractmethod
from typing import Any, Optional

import requests

from config import env
from models import TrackInfo
from youtube_data import GoogleOAuthError, YouTubeApiError, YouTubeDataClient, refresh_access_token

logger = logging.getLogger("ytm_spotify_sync.auth")

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
    "Google rejected the YouTube OAuth credentials. Regenerate the token locally with\n"
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
    "No usable YouTube credentials. Run 'python setup_ytm_oauth.py' and set "
    "YTM_OAUTH_CLIENT_ID, YTM_OAUTH_CLIENT_SECRET and YTM_OAUTH_REFRESH_TOKEN."
)


class YouTubeMusicAuthError(RuntimeError):
    """Raised when YouTube credentials are missing, invalid or expired."""

    def __init__(self, message: str, remediation: str = "") -> None:
        super().__init__(message)
        self.remediation = remediation


def looks_like_expired_session(error: BaseException | str) -> bool:
    """Heuristic: does this failure mean 'YouTube considers us signed out'?"""
    text = str(error).lower()
    return any(marker in text for marker in _EXPIRED_MARKERS)


def remediation_for(auth_mode: str) -> str:
    """Returns the actionable fix text for the auth mode in use."""
    return OAUTH_REMEDIATION if auth_mode == "oauth" else BROWSER_REMEDIATION


# --------------------------------------------------------------------------- #
# Sources
# --------------------------------------------------------------------------- #


class LikedSongsSource(ABC):
    """Anything that can tell us who we are and what the user liked."""

    mode: str = "unknown"

    @abstractmethod
    def describe(self) -> Optional[str]:
        """Best-effort identity probe; returns the account/channel name."""

    @abstractmethod
    def fetch_liked(self, limit: Optional[int] = None) -> list[TrackInfo]:
        """Liked songs, most recent first, all of them unless ``limit`` is given."""


class DataApiSource(LikedSongsSource):
    """OAuth refresh token -> YouTube Data API v3."""

    mode = "oauth"

    def __init__(self, client_id: str, client_secret: str, refresh_token: str) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._refresh_token = refresh_token
        self._session = requests.Session()
        self._client: Optional[YouTubeDataClient] = None

    def _ensure_client(self) -> YouTubeDataClient:
        if self._client is None:
            try:
                token = refresh_access_token(
                    self._client_id, self._client_secret, self._refresh_token, self._session
                )
            except GoogleOAuthError as exc:
                raise YouTubeMusicAuthError(
                    f"Google rejected the OAuth refresh token ({exc}).", OAUTH_REMEDIATION
                ) from exc
            except requests.RequestException as exc:
                raise RuntimeError(f"Could not reach Google's token endpoint: {exc}") from exc
            self._client = YouTubeDataClient(token["access_token"], self._session)
            logger.info("YouTube client initialised using OAuth (refresh token) + Data API v3.")
        return self._client

    def _translate(self, exc: YouTubeApiError) -> Exception:
        """Maps Data API failures to the project's error types."""
        if exc.status == 401:
            return YouTubeMusicAuthError(f"YouTube rejected the access token: {exc}", OAUTH_REMEDIATION)
        if exc.status == 403 and exc.reason in ("accessNotConfigured", "forbidden", "insufficientPermissions"):
            return YouTubeMusicAuthError(
                f"The Google Cloud project cannot use the YouTube Data API v3: {exc}. "
                "Enable 'YouTube Data API v3' in the project and make sure the token was "
                "granted the youtube / youtube.readonly scope.",
                OAUTH_REMEDIATION,
            )
        if exc.status == 403 and exc.reason in ("quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded"):
            return RuntimeError(
                f"YouTube Data API quota exhausted ({exc.reason}); it resets at midnight Pacific time. "
                "A normal sync uses ~15 of the 10,000 daily units, so check for other apps on the project."
            )
        return RuntimeError(f"YouTube Data API request failed: {exc}")

    def describe(self) -> Optional[str]:
        try:
            title = self._ensure_client().channel_title()
        except YouTubeApiError as exc:
            translated = self._translate(exc)
            if isinstance(translated, YouTubeMusicAuthError):
                raise translated from exc
            logger.warning("Could not read the YouTube channel name (continuing anyway): %s", exc)
            return None
        if title:
            logger.info("Authenticated with YouTube as '%s'.", title)
        return title

    def fetch_liked(self, limit: Optional[int] = None) -> list[TrackInfo]:
        if limit:
            logger.info("Fetching up to %d liked songs from the YouTube Data API...", limit)
        else:
            logger.info("Fetching all liked songs from the YouTube Data API...")
        try:
            return self._ensure_client().liked_music(limit=limit)
        except YouTubeApiError as exc:
            raise self._translate(exc) from exc
        except requests.RequestException as exc:
            raise RuntimeError(f"Network error while reading liked songs: {exc}") from exc


class CookieSource(LikedSongsSource):
    """Deprecated: browser cookies -> ytmusicapi private API."""

    mode = "browser"

    def __init__(self, client: Any) -> None:
        self._client = client

    def describe(self) -> Optional[str]:
        try:
            info = self._client.get_account_info()
        except Exception as exc:
            if looks_like_expired_session(exc):
                raise YouTubeMusicAuthError(
                    f"YouTube Music reports the session as signed out: {exc}", BROWSER_REMEDIATION
                ) from exc
            logger.warning("Could not read YouTube Music account info (continuing anyway): %s", exc)
            return None
        account_name = info.get("accountName")
        if account_name:
            logger.info("Authenticated with YouTube Music as '%s'.", account_name)
        return account_name

    def fetch_liked(self, limit: Optional[int] = None) -> list[TrackInfo]:
        if limit:
            logger.info("Fetching up to %d liked songs from YouTube Music...", limit)
        else:
            logger.info("Fetching all liked songs from YouTube Music library...")

        try:
            liked_response = self._client.get_liked_songs(limit=limit)
        except Exception as exc:
            if looks_like_expired_session(exc):
                raise YouTubeMusicAuthError(
                    f"YouTube Music answered as a signed-out user: {exc}", BROWSER_REMEDIATION
                ) from exc
            raise RuntimeError(f"Failed to fetch liked songs from YouTube Music: {exc}") from exc

        tracks_raw = liked_response.get("tracks", []) if isinstance(liked_response, dict) else []
        logger.info("Successfully retrieved %d liked track(s) from YouTube Music.", len(tracks_raw))

        tracks: list[TrackInfo] = []
        for item in tracks_raw:
            video_id = item.get("videoId")
            title = item.get("title")
            if not video_id or not title:
                continue
            artists = item.get("artists") or []
            primary_artist = artists[0].get("name", "Unknown Artist") if artists else "Unknown Artist"
            album = item.get("album")
            isrc = item.get("isrc") or (album.get("isrc") if isinstance(album, dict) else None)
            duration = item.get("duration_seconds")
            tracks.append(
                TrackInfo(
                    video_id=video_id,
                    title=title,
                    artist=primary_artist,
                    isrc=isrc,
                    duration_seconds=int(duration) if isinstance(duration, int) else None,
                )
            )
        return tracks


# --------------------------------------------------------------------------- #
# Credential loading
# --------------------------------------------------------------------------- #


def _load_refresh_token() -> Optional[str]:
    """Reads the OAuth refresh token from YTM_OAUTH_JSON or YTM_OAUTH_REFRESH_TOKEN."""
    raw = env("YTM_OAUTH_JSON")
    if raw:
        if os.path.isfile(raw):
            logger.info("Loading YouTube OAuth token from file '%s'.", raw)
            with open(raw, "r", encoding="utf-8") as handle:
                parsed = json.load(handle)
        else:
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise YouTubeMusicAuthError(
                    f"YTM_OAUTH_JSON is neither valid JSON nor an existing file path: {exc}",
                    MISSING_REMEDIATION,
                ) from exc
            logger.info("Loading YouTube OAuth token from YTM_OAUTH_JSON.")
        if not isinstance(parsed, dict) or not str(parsed.get("refresh_token") or "").strip():
            raise YouTubeMusicAuthError(
                "YTM_OAUTH_JSON must be a JSON object containing a 'refresh_token'.",
                MISSING_REMEDIATION,
            )
        return str(parsed["refresh_token"]).strip()

    refresh_token = env("YTM_OAUTH_REFRESH_TOKEN")
    if refresh_token:
        logger.info("Loading YouTube OAuth token from YTM_OAUTH_REFRESH_TOKEN.")
        return refresh_token
    return None


def build_liked_songs_source() -> LikedSongsSource:
    """
    Picks and initialises the liked-songs source from the environment.

    Credential problems surface here, loudly, before any state or playlist work.
    :raises YouTubeMusicAuthError: when credentials are missing or rejected.
    """
    refresh_token = _load_refresh_token()
    if refresh_token:
        client_id = env("YTM_OAUTH_CLIENT_ID")
        client_secret = env("YTM_OAUTH_CLIENT_SECRET")
        if not client_id or not client_secret:
            raise YouTubeMusicAuthError(
                "OAuth token provided but YTM_OAUTH_CLIENT_ID / YTM_OAUTH_CLIENT_SECRET are "
                "missing. A refresh token can only be redeemed by the client that issued it.",
                MISSING_REMEDIATION,
            )
        source = DataApiSource(client_id, client_secret, refresh_token)
        source._ensure_client()  # noqa: SLF001 - fail fast on a dead refresh token
        return source

    raw_headers = env("YTM_HEADERS_JSON")
    if raw_headers:
        from ytmusicapi import YTMusic  # imported lazily: only the legacy path needs it

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
        return CookieSource(client)

    raise YouTubeMusicAuthError(
        "No YouTube credentials configured (YTM_OAUTH_REFRESH_TOKEN / YTM_OAUTH_JSON / "
        "YTM_HEADERS_JSON are all empty).",
        MISSING_REMEDIATION,
    )
