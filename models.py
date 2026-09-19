#!/usr/bin/env python3
"""Shared data model for a liked track, independent of where it was read from."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class TrackInfo:
    """Normalized track metadata extracted from YouTube / YouTube Music."""

    video_id: str
    title: str
    artist: str
    isrc: Optional[str] = None
    duration_seconds: Optional[int] = None
    #: Original video title when ``title``/``artist`` were derived from it
    #: (e.g. "Artist - Song (Official Video)"). Used for free-text fallbacks.
    raw_title: Optional[str] = None
