#!/usr/bin/env python3
"""
Shared configuration helpers
============================
Small utilities used by both the sync pipeline and the credential setup
scripts: environment reading, secret sanitisation and Spotify ID parsing.
"""

from __future__ import annotations

import os
from typing import Optional

try:  # Local development convenience; never required in CI.
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover - dotenv is an optional dependency
    pass

# Prefixes used by this project. A value that starts with one of them is
# assumed to have been pasted as "KEY=VALUE" by mistake.
_SECRET_PREFIXES = ("SPOTIPY_", "SPOTIFY_", "YTM_")


def sanitize_secret_value(val: Optional[str]) -> str:
    """Cleans raw environment variables from accidental quotes, whitespace or KEY= prefix."""
    if not val:
        return ""
    cleaned = val.strip()
    # If pasted as KEY=VALUE, extract VALUE
    if "=" in cleaned and any(cleaned.startswith(k) for k in _SECRET_PREFIXES):
        cleaned = cleaned.split("=", 1)[1].strip()
    # Strip wrapping quotes if present
    if (cleaned.startswith('"') and cleaned.endswith('"')) or (
        cleaned.startswith("'") and cleaned.endswith("'")
    ):
        cleaned = cleaned[1:-1].strip()
    return cleaned


def env(name: str, default: str = "") -> str:
    """Reads an environment variable and sanitises it."""
    return sanitize_secret_value(os.getenv(name, default))


def extract_playlist_id(raw_id: str) -> str:
    """Extracts a clean Spotify playlist ID from a URL, URI or raw ID string."""
    clean = sanitize_secret_value(raw_id)
    if "/playlist/" in clean:
        clean = clean.split("/playlist/")[1].split("?")[0].split("/")[0].strip()
    elif clean.startswith("spotify:playlist:"):
        clean = clean.split("spotify:playlist:", 1)[1].split("?")[0].strip()
    elif "?" in clean:
        clean = clean.split("?")[0].strip()
    return clean
