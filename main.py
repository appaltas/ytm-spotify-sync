#!/usr/bin/env python3
"""
YouTube Music to Spotify Playlist Synchronizer
===============================================
Synchronizes liked songs from YouTube Music to a specified Spotify playlist.
Designed for serverless execution via GitHub Actions (zero-cost).

Author: Senior DevOps & Python Developer
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Set

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import spotipy
from spotipy.oauth2 import SpotifyOAuth
from ytmusicapi import YTMusic

# Configure logging format
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("ytm_spotify_sync")

# File path for tracking synchronization state
STATE_FILE_PATH = Path(__file__).resolve().parent / "synced_tracks.json"


@dataclass
class TrackInfo:
    """Represents normalized track metadata extracted from YouTube Music."""
    video_id: str
    title: str
    artist: str
    isrc: Optional[str] = None


@dataclass
class SyncStats:
    """Tracks synchronization run metrics."""
    total_processed: int = 0
    added_to_playlist: int = 0
    already_in_playlist: int = 0
    skipped_synced_state: int = 0
    not_found: int = 0


def clean_track_title(title: str) -> str:
    """
    Cleans noise and decorative text frequently found in YouTube Music titles.
    Removes patterns like (Official Video), [Audio], (feat. Artist), [HQ], etc.
    """
    # Remove patterns in brackets or parentheses that indicate media type or resolution
    noise_patterns = [
        r"\(official\s+(?:music\s+)?video\)",
        r"\[official\s+(?:music\s+)?video\]",
        r"\(official\s+audio\)",
        r"\[official\s+audio\]",
        r"\(audio\)",
        r"\[audio\]",
        r"\(lyric\s+video\)",
        r"\[lyric\s+video\]",
        r"\(lyrics\)",
        r"\[lyrics\]",
        r"\(visualizer\)",
        r"\[visualizer\]",
        r"\(music\s+video\)",
        r"\[music\s+video\]",
        r"\(clip\s+officiel\)",
        r"\[clip\s+officiel\]",
        r"\(video\)",
        r"\[video\]",
        r"\(hd\)",
        r"\[hd\]",
        r"\(4k\)",
        r"\[4k\]",
        r"\(hq\)",
        r"\[hq\]",
        r"\|\s*official\s+video",
    ]

    cleaned = title
    for pattern in noise_patterns:
        cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE)

    # Clean superfluous punctuation and multiple spaces
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def load_state() -> dict[str, Any]:
    """Loads the synchronization state from the state file."""
    if not STATE_FILE_PATH.exists():
        logger.info("State file not found. Initializing new state structure.")
        return {"last_sync": None, "synced_yt_ids": []}

    try:
        with open(STATE_FILE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            if not isinstance(data, dict):
                return {"last_sync": None, "synced_yt_ids": []}
            if "synced_yt_ids" not in data:
                data["synced_yt_ids"] = []
            return data
    except Exception as e:
        logger.warning(f"Failed to read state file '{STATE_FILE_PATH}': {e}. Starting with empty state.")
        return {"last_sync": None, "synced_yt_ids": []}


def save_state(state: dict[str, Any]) -> None:
    """Persists the synchronization state atomically."""
    state["last_sync"] = datetime.now(timezone.utc).isoformat()
    # Deduplicate and sort IDs for clean git diffs
    state["synced_yt_ids"] = sorted(list(set(state.get("synced_yt_ids", []))))

    temp_file = STATE_FILE_PATH.with_suffix(".tmp")
    try:
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
            f.write("\n")
        temp_file.replace(STATE_FILE_PATH)
        logger.info(f"State successfully saved to '{STATE_FILE_PATH.name}'.")
    except Exception as e:
        logger.error(f"Failed to save state file: {e}")
        if temp_file.exists():
            temp_file.unlink()


def get_ytmusic_client() -> YTMusic:
    """
    Initializes and authenticates the YouTube Music client using YTM_HEADERS_JSON.
    Supports either a JSON string of headers or a path to a JSON configuration file.
    """
    raw_headers = os.getenv("YTM_HEADERS_JSON")
    if not raw_headers:
        raise ValueError("Environment variable 'YTM_HEADERS_JSON' is not set or empty.")

    # Check if raw_headers is a valid file path
    if os.path.isfile(raw_headers):
        logger.info("Initializing YTMusic using headers file path.")
        return YTMusic(auth=raw_headers)

    # If it's a JSON string, write to a secure temporary file for ytmusicapi compatibility
    try:
        parsed_json = json.loads(raw_headers)
    except json.JSONDecodeError as err:
        raise ValueError("Invalid JSON in 'YTM_HEADERS_JSON' environment variable.") from err

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as temp_auth_file:
        json.dump(parsed_json, temp_auth_file)
        temp_auth_path = temp_auth_file.name

    try:
        logger.info("Initializing YTMusic client with provided JSON headers.")
        ytmusic = YTMusic(auth=temp_auth_path)
        return ytmusic
    finally:
        if os.path.exists(temp_auth_path):
            os.remove(temp_auth_path)


def get_spotify_client() -> spotipy.Spotify:
    """
    Initializes and authenticates the Spotify client using OAuth Refresh Token flow.
    """
    client_id = os.getenv("SPOTIPY_CLIENT_ID")
    client_secret = os.getenv("SPOTIPY_CLIENT_SECRET")
    refresh_token = os.getenv("SPOTIPY_REFRESH_TOKEN")

    missing = []
    if not client_id:
        missing.append("SPOTIPY_CLIENT_ID")
    if not client_secret:
        missing.append("SPOTIPY_CLIENT_SECRET")
    if not refresh_token:
        missing.append("SPOTIPY_REFRESH_TOKEN")

    if missing:
        raise ValueError(f"Missing required Spotify environment variables: {', '.join(missing)}")

    auth_manager = SpotifyOAuth(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri="http://127.0.0.1:9090",
        scope="playlist-modify-public playlist-modify-private playlist-read-private",
    )

    try:
        logger.info("Refreshing Spotify access token with SPOTIPY_REFRESH_TOKEN...")
        token_info = auth_manager.refresh_access_token(refresh_token)
        if not token_info or "access_token" not in token_info:
            raise ValueError("Failed to obtain access token from Spotify OAuth refresh token.")
        sp = spotipy.Spotify(auth=token_info["access_token"])
        return sp
    except Exception as e:
        raise RuntimeError(f"Spotify authentication failed: {e}") from e


def get_spotify_playlist_track_ids(sp: spotipy.Spotify, playlist_id: str) -> Set[str]:
    """
    Fetches all existing track IDs and URIs currently in the target Spotify playlist
    to ensure idempotency and prevent duplicate additions.
    """
    existing_ids: Set[str] = set()
    logger.info(f"Fetching current tracks from Spotify playlist '{playlist_id}'...")

    try:
        results = sp.playlist_items(
            playlist_id,
            fields="items.track.id,items.track.uri,next",
            limit=100,
            additional_types=["track"],
        )

        while results:
            for item in results.get("items", []):
                track = item.get("track")
                if track:
                    if track.get("id"):
                        existing_ids.add(track["id"])
                    if track.get("uri"):
                        existing_ids.add(track["uri"])

            if results.get("next"):
                results = sp.next(results)
            else:
                break

        logger.info(f"Retrieved {len(existing_ids)} existing track identifier(s) from target Spotify playlist.")
        return existing_ids
    except Exception as e:
        raise RuntimeError(f"Failed to fetch Spotify playlist items for ID '{playlist_id}': {e}") from e


def search_spotify_track(sp: spotipy.Spotify, track: TrackInfo) -> Optional[dict[str, Any]]:
    """
    Searches for a track on Spotify using a hierarchical strategy:
    1. Direct ISRC search (exact match if available).
    2. Strict track name + primary artist search.
    3. Cleaned title + artist fallback search.
    """
    # 1. Search by ISRC if available
    if track.isrc:
        query_isrc = f"isrc:{track.isrc.strip()}"
        try:
            res = sp.search(q=query_isrc, type="track", limit=1)
            tracks = res.get("tracks", {}).get("items", [])
            if tracks:
                logger.info(f"  -> Match found via ISRC ({track.isrc}): '{tracks[0]['name']}' by {tracks[0]['artists'][0]['name']}")
                return tracks[0]
        except Exception as e:
            logger.debug(f"ISRC query failed for '{track.isrc}': {e}")

    # 2. Search by strict track and artist
    clean_title = clean_track_title(track.title)
    # Remove quotes from query parameters to prevent syntax errors in Spotify Search API
    safe_title = clean_title.replace('"', "").strip()
    safe_artist = track.artist.replace('"', "").strip()

    strict_query = f'track:"{safe_title}" artist:"{safe_artist}"'
    try:
        res = sp.search(q=strict_query, type="track", limit=1)
        tracks = res.get("tracks", {}).get("items", [])
        if tracks:
            logger.info(f"  -> Match found via strict search: '{tracks[0]['name']}' by {tracks[0]['artists'][0]['name']}")
            return tracks[0]
    except Exception as e:
        logger.debug(f"Strict search failed for '{strict_query}': {e}")

    # 3. Fallback search (relaxed text query)
    relaxed_query = f"{safe_title} {safe_artist}".strip()
    try:
        res = sp.search(q=relaxed_query, type="track", limit=1)
        tracks = res.get("tracks", {}).get("items", [])
        if tracks:
            logger.info(f"  -> Match found via relaxed search: '{tracks[0]['name']}' by {tracks[0]['artists'][0]['name']}")
            return tracks[0]
    except Exception as e:
        logger.debug(f"Relaxed search failed for '{relaxed_query}': {e}")

    return None


def fetch_ytmusic_liked_songs(ytmusic: YTMusic, limit: int = 50) -> list[TrackInfo]:
    """
    Retrieves the most recent liked songs from YouTube Music and extracts normalized metadata.
    """
    logger.info(f"Fetching up to {limit} liked songs from YouTube Music...")
    try:
        liked_response = ytmusic.get_liked_songs(limit=limit)
        tracks_raw = liked_response.get("tracks", []) if isinstance(liked_response, dict) else []
        logger.info(f"Successfully retrieved {len(tracks_raw)} liked track(s) from YouTube Music.")

        extracted_tracks: list[TrackInfo] = []
        for item in tracks_raw:
            video_id = item.get("videoId")
            title = item.get("title")
            if not video_id or not title:
                continue

            artists = item.get("artists", [])
            primary_artist = artists[0].get("name", "Unknown Artist") if artists else "Unknown Artist"
            isrc = item.get("isrc") or item.get("album", {}).get("isrc") if isinstance(item.get("album"), dict) else None

            extracted_tracks.append(
                TrackInfo(
                    video_id=video_id,
                    title=title,
                    artist=primary_artist,
                    isrc=isrc,
                )
            )

        return extracted_tracks
    except Exception as e:
        raise RuntimeError(f"Failed to fetch liked songs from YouTube Music: {e}") from e


def run_sync() -> None:
    """Main synchronization execution pipeline."""
    logger.info("=" * 60)
    logger.info("Starting YouTube Music -> Spotify Playlist Sync Pipeline")
    logger.info("=" * 60)

    playlist_id = os.getenv("SPOTIFY_PLAYLIST_ID")
    if not playlist_id:
        raise ValueError("Environment variable 'SPOTIFY_PLAYLIST_ID' is not configured.")

    # 1. Load synchronization state
    state = load_state()
    synced_yt_ids: Set[str] = set(state.get("synced_yt_ids", []))
    logger.info(f"Loaded {len(synced_yt_ids)} previously synced YouTube track ID(s).")

    # 2. Authenticate API clients
    ytmusic = get_ytmusic_client()
    sp = get_spotify_client()

    # 3. Retrieve current playlist track IDs from Spotify
    existing_spotify_ids = get_spotify_playlist_track_ids(sp, playlist_id)

    # 4. Fetch latest Liked Songs from YouTube Music
    liked_tracks = fetch_ytmusic_liked_songs(ytmusic, limit=50)

    stats = SyncStats(total_processed=len(liked_tracks))
    newly_added_yt_ids: list[str] = []

    # 5. Process each track
    logger.info("Processing liked songs...")
    for idx, track in enumerate(liked_tracks, start=1):
        logger.info(f"[{idx}/{len(liked_tracks)}] Processing: '{track.title}' by '{track.artist}' (YT ID: {track.video_id})")

        # Check if already processed in local state
        if track.video_id in synced_yt_ids:
            logger.info("  -> Already processed in previous sync runs. Skipping.")
            stats.skipped_synced_state += 1
            continue

        # Search for equivalent track in Spotify
        spotify_track = search_spotify_track(sp, track)

        if not spotify_track:
            logger.warning(f"  -> [NOT FOUND] Could not find matching Spotify track for '{track.title}' - '{track.artist}'.")
            stats.not_found += 1
            continue

        spotify_id = spotify_track.get("id")
        spotify_uri = spotify_track.get("uri")

        # Check if track is already in the Spotify playlist
        if spotify_id in existing_spotify_ids or spotify_uri in existing_spotify_ids:
            logger.info(f"  -> Track is already present in target Spotify playlist.")
            stats.already_in_playlist += 1
            synced_yt_ids.add(track.video_id)
            newly_added_yt_ids.append(track.video_id)
            continue

        # Add track to target Spotify playlist
        try:
            sp.playlist_add_items(playlist_id, [spotify_uri])
            logger.info(f"  -> [ADDED] Successfully added '{spotify_track['name']}' to Spotify playlist!")
            stats.added_to_playlist += 1
            existing_spotify_ids.add(spotify_id)
            existing_spotify_ids.add(spotify_uri)
            synced_yt_ids.add(track.video_id)
            newly_added_yt_ids.append(track.video_id)
        except Exception as e:
            logger.error(f"  -> Failed to add track '{spotify_uri}' to Spotify playlist: {e}")

    # 6. Save updated state if changes occurred
    if newly_added_yt_ids:
        state["synced_yt_ids"] = list(synced_yt_ids)
        save_state(state)
    else:
        logger.info("No new tracks to persist to state file.")

    # 7. Print summary report
    logger.info("=" * 60)
    logger.info("SYNCHRONIZATION COMPLETED - SUMMARY REPORT")
    logger.info("=" * 60)
    logger.info(f"  Total YouTube Liked Songs Checked: {stats.total_processed}")
    logger.info(f"  ✨ Newly Added to Spotify:          {stats.added_to_playlist}")
    logger.info(f"  🔁 Already in Spotify Playlist:     {stats.already_in_playlist}")
    logger.info(f"  ⏭️  Skipped (Previously Synced):   {stats.skipped_synced_state}")
    logger.info(f"  ⚠️  Not Found on Spotify:            {stats.not_found}")
    logger.info("=" * 60)


if __name__ == "__main__":
    try:
        run_sync()
    except KeyboardInterrupt:
        logger.info("Synchronization aborted by user.")
        sys.exit(130)
    except Exception as exc:
        logger.critical(f"Critical execution failure: {exc}", exc_info=True)
        sys.exit(1)
