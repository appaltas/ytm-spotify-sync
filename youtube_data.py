#!/usr/bin/env python3
"""
YouTube Data API v3 access
==========================
Official, quota-cheap way to read the authenticated user's liked music with an
OAuth token issued by the user's own Google Cloud client.

Why not ytmusicapi's OAuth mode: music.youtube.com's private InnerTube API
rejects bearer tokens from third-party OAuth clients with HTTP 400
"Request contains an invalid argument" (see sigma67/ytmusicapi#676). The public
Data API accepts the very same token, is documented, and costs about one quota
unit per 50 videos - a full sync of 700 likes is ~15 of the 10,000 daily units.

Liking a song in YouTube Music is the same as liking its video on YouTube, so
``videos.list(myRating=like)`` filtered to the Music category (10) is the
"Liked Music" playlist.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from typing import Any, Optional

import requests

from models import TrackInfo

logger = logging.getLogger("ytm_spotify_sync.youtube")

GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_DEVICE_CODE_URL = "https://oauth2.googleapis.com/device/code"
YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"

#: Read-only is enough to list likes and keeps the consent screen honest.
SCOPE_YOUTUBE_READONLY = "https://www.googleapis.com/auth/youtube.readonly"

MUSIC_CATEGORY_ID = "10"
PAGE_SIZE = 50
REQUEST_TIMEOUT = 30


class GoogleOAuthError(RuntimeError):
    """Google's token endpoint refused the request (invalid_grant, invalid_client, ...)."""

    def __init__(self, reason: str, description: str = "") -> None:
        super().__init__(f"{reason}: {description}" if description else reason)
        self.reason = reason


class YouTubeApiError(RuntimeError):
    """The Data API answered with an HTTP error."""

    def __init__(self, status: int, reason: str, message: str) -> None:
        super().__init__(f"HTTP {status} ({reason}): {message}")
        self.status = status
        self.reason = reason


def refresh_access_token(
    client_id: str,
    client_secret: str,
    refresh_token: str,
    session: Optional[requests.Session] = None,
) -> dict[str, Any]:
    """Trades a refresh token for a fresh access token."""
    http = session or requests.Session()
    response = http.post(
        GOOGLE_TOKEN_URL,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
        timeout=REQUEST_TIMEOUT,
    )
    try:
        payload = response.json()
    except ValueError:
        payload = {}

    if response.status_code >= 400 or "access_token" not in payload:
        raise GoogleOAuthError(
            payload.get("error", f"http_{response.status_code}"),
            payload.get("error_description", response.text[:200]),
        )
    return payload


# --------------------------------------------------------------------------- #
# Video -> TrackInfo
# --------------------------------------------------------------------------- #

_DURATION_RE = re.compile(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?")
_TITLE_SPLIT_RE = re.compile(r"^(?P<left>.{1,80}?)\s+[-–—|]\s+(?P<right>.+)$")
_CHANNEL_NOISE_RE = re.compile(r"(?i)\s*(?:-\s*)?(?:vevo|official|oficial|music|tv|channel)\s*$")
_TOPIC_SUFFIX = " - Topic"


def parse_iso8601_duration(text: Optional[str]) -> Optional[int]:
    """'PT3M58S' -> 238. Returns None for anything it cannot read."""
    match = _DURATION_RE.fullmatch(text or "")
    if not match:
        return None
    days, hours, minutes, seconds = (int(part) if part else 0 for part in match.groups())
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def derive_artist_and_title(video_title: str, channel_title: Optional[str]) -> tuple[str, str]:
    """
    Best-effort (artist, song title) from a YouTube video.

    * Auto-generated music channels are named "Artist - Topic" and their video
      title is exactly the song name - the reliable case, and the common one
      for YouTube Music likes.
    * Otherwise "Artist - Song" in the title is the next best signal.
    * Failing that, the channel name minus VEVO/Official noise.
    """
    channel = (channel_title or "").strip()
    title = (video_title or "").strip()

    if channel.endswith(_TOPIC_SUFFIX):
        return channel[: -len(_TOPIC_SUFFIX)].strip(), title

    match = _TITLE_SPLIT_RE.match(title)
    if match:
        return match.group("left").strip(), match.group("right").strip()

    cleaned_channel = _CHANNEL_NOISE_RE.sub("", channel).strip()
    return cleaned_channel or "Unknown Artist", title


def video_to_track(item: dict[str, Any]) -> Optional[TrackInfo]:
    """Maps one ``videos.list`` item to a TrackInfo, or None when unusable."""
    snippet = item.get("snippet") or {}
    video_id = item.get("id")
    raw_title = snippet.get("title")
    if not video_id or not raw_title:
        return None

    artist, title = derive_artist_and_title(raw_title, snippet.get("channelTitle"))
    duration = parse_iso8601_duration((item.get("contentDetails") or {}).get("duration"))
    return TrackInfo(
        video_id=video_id,
        title=title,
        artist=artist,
        duration_seconds=duration,
        raw_title=raw_title if raw_title != title else None,
    )


# --------------------------------------------------------------------------- #
# Client
# --------------------------------------------------------------------------- #


class YouTubeDataClient:
    """Thin authenticated wrapper over the handful of Data API calls we need."""

    def __init__(self, access_token: str, session: Optional[requests.Session] = None) -> None:
        self._session = session or requests.Session()
        self._headers = {"Authorization": f"Bearer {access_token}"}

    def _get(self, resource: str, **params: Any) -> dict[str, Any]:
        response = self._session.get(
            f"{YOUTUBE_API_BASE}/{resource}",
            params=params,
            headers=self._headers,
            timeout=REQUEST_TIMEOUT,
        )
        try:
            payload = response.json()
        except ValueError:
            payload = {}

        if response.status_code >= 400:
            error = payload.get("error") or {}
            first = (error.get("errors") or [{}])[0]
            raise YouTubeApiError(
                response.status_code,
                first.get("reason", "unknown"),
                error.get("message", response.text[:200]),
            )
        return payload

    def channel_title(self) -> Optional[str]:
        """Name of the authenticated user's channel (cheap identity probe)."""
        data = self._get("channels", part="snippet", mine="true")
        items = data.get("items") or []
        return (items[0].get("snippet") or {}).get("title") if items else None

    def iter_liked_videos(self) -> Iterator[dict[str, Any]]:
        """Yields every liked video, most recent first, following pagination."""
        page_token: Optional[str] = None
        while True:
            params: dict[str, Any] = {
                "part": "snippet,contentDetails",
                "myRating": "like",
                "maxResults": PAGE_SIZE,
            }
            if page_token:
                params["pageToken"] = page_token
            data = self._get("videos", **params)
            yield from data.get("items") or []
            page_token = data.get("nextPageToken")
            if not page_token:
                return

    def liked_music(self, limit: Optional[int] = None, music_only: bool = True) -> list[TrackInfo]:
        """Liked videos in the Music category, mapped to TrackInfo."""
        tracks: list[TrackInfo] = []
        skipped = 0
        for item in self.iter_liked_videos():
            category = (item.get("snippet") or {}).get("categoryId")
            if music_only and category != MUSIC_CATEGORY_ID:
                skipped += 1
                continue
            track = video_to_track(item)
            if track:
                tracks.append(track)
            if limit and len(tracks) >= limit:
                break

        logger.info(
            "Retrieved %d liked music video(s) from the YouTube Data API (%d non-music like(s) ignored).",
            len(tracks),
            skipped,
        )
        return tracks
