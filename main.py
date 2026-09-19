#!/usr/bin/env python3
"""
YouTube Music to Spotify Playlist Synchronizer
===============================================
Synchronizes liked songs from YouTube Music to a specified Spotify playlist.
Designed for serverless execution via GitHub Actions (zero-cost).

Exit codes:
    0 - success
    1 - unexpected failure
    2 - YouTube Music credentials missing / expired (actionable by the user)
    3 - Spotify credentials rejected (actionable by the user)
    4 - configuration error (a required secret is not set)
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Optional, Set

import spotipy
from spotipy.cache_handler import MemoryCacheHandler
from spotipy.oauth2 import SpotifyOAuth

from config import env, extract_playlist_id
from models import TrackInfo
from ytm_auth import YouTubeMusicAuthError, build_liked_songs_source

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("ytm_spotify_sync")

# File path for tracking synchronization state
STATE_FILE_PATH = Path(__file__).resolve().parent / "synced_tracks.json"

# Spotify accepts at most 100 URIs per playlist-add request.
SPOTIFY_ADD_BATCH_SIZE = 100

# A candidate whose runtime differs this much from the YouTube track is a
# different recording (edit, live version, full album upload, ...).
MAX_DURATION_DRIFT_SECONDS = 30

SPOTIFY_SCOPE = "playlist-modify-public playlist-modify-private playlist-read-private"

SPOTIFY_REMEDIATION = (
    "Spotify rejected the credentials. Regenerate the refresh token with "
    "'python get_spotify_token.py' and update the SPOTIPY_REFRESH_TOKEN repository secret."
)


class ConfigurationError(RuntimeError):
    """Raised when a required environment variable / secret is not configured."""


class SpotifyAuthError(RuntimeError):
    """Raised when Spotify rejects the configured credentials."""


@dataclass
class SyncStats:
    """Tracks synchronization run metrics."""

    total_processed: int = 0
    added_to_playlist: int = 0
    already_in_playlist: int = 0
    skipped_synced_state: int = 0
    not_found: int = 0
    failed_to_add: int = 0
    unmatched: list[str] = field(default_factory=list)


def clean_track_title(title: str) -> str:
    """
    Cleans noise and decorative text frequently found in YouTube Music titles.
    Removes patterns like (Official Video), [Audio], [HQ], etc.
    """
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

    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


# --------------------------------------------------------------------------- #
# State handling
# --------------------------------------------------------------------------- #


def load_state() -> dict[str, Any]:
    """Loads the synchronization state from the state file."""
    empty: dict[str, Any] = {"last_sync": None, "synced_yt_ids": [], "unmatched": []}
    if not STATE_FILE_PATH.exists():
        logger.info("State file not found. Initializing new state structure.")
        return empty

    try:
        with open(STATE_FILE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return empty
        data.setdefault("synced_yt_ids", [])
        data.setdefault("unmatched", [])
        return data
    except Exception as e:
        logger.warning(
            f"Failed to read state file '{STATE_FILE_PATH}': {e}. Starting with empty state."
        )
        return empty


def save_state(state: dict[str, Any]) -> None:
    """Persists the synchronization state atomically."""
    state["last_sync"] = datetime.now(timezone.utc).isoformat()
    # Deduplicate and sort IDs for clean git diffs
    state["synced_yt_ids"] = sorted(set(state.get("synced_yt_ids", [])))
    state["unmatched"] = sorted(set(state.get("unmatched", [])))

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


# --------------------------------------------------------------------------- #
# Spotify
# --------------------------------------------------------------------------- #


def get_spotify_client() -> spotipy.Spotify:
    """
    Initializes and authenticates the Spotify client using the OAuth refresh token flow.

    The auth manager is kept attached to the client so that long runs transparently
    renew the access token when the initial one expires (Spotify tokens last 1h).
    """
    client_id = env("SPOTIPY_CLIENT_ID")
    client_secret = env("SPOTIPY_CLIENT_SECRET")
    refresh_token = env("SPOTIPY_REFRESH_TOKEN")

    missing = [
        name
        for name, value in (
            ("SPOTIPY_CLIENT_ID", client_id),
            ("SPOTIPY_CLIENT_SECRET", client_secret),
            ("SPOTIPY_REFRESH_TOKEN", refresh_token),
        )
        if not value
    ]
    if missing:
        raise ConfigurationError(f"Missing required Spotify secret(s): {', '.join(missing)}")

    auth_manager = SpotifyOAuth(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri="http://127.0.0.1:9090",
        scope=SPOTIFY_SCOPE,
        # Never touch the filesystem in CI: the token lives in memory only.
        cache_handler=MemoryCacheHandler(),
    )

    try:
        logger.info("Refreshing Spotify access token with SPOTIPY_REFRESH_TOKEN...")
        token_info = auth_manager.refresh_access_token(refresh_token)
        if not token_info or "access_token" not in token_info:
            raise SpotifyAuthError("Spotify did not return an access token for the refresh token.")
    except SpotifyAuthError:
        raise
    except Exception as e:
        raise SpotifyAuthError(f"Spotify authentication failed: {e}") from e

    # spotipy retries 429/5xx with exponential backoff out of the box.
    return spotipy.Spotify(auth_manager=auth_manager, requests_timeout=30, retries=5)


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

        logger.info(
            f"Retrieved {len(existing_ids)} existing track identifier(s) from target Spotify playlist."
        )
        return existing_ids
    except Exception as e:
        raise RuntimeError(f"Failed to fetch Spotify playlist items for ID '{playlist_id}': {e}") from e


# --------------------------------------------------------------------------- #
# Matching
# --------------------------------------------------------------------------- #


def _normalize_for_match(text: str) -> str:
    """Lowercases, strips accents and punctuation so two spellings can be compared."""
    decomposed = unicodedata.normalize("NFKD", text or "")
    ascii_text = decomposed.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", " ", ascii_text.lower()).strip()


def _text_score(a: str, b: str) -> float:
    """Similarity in [0, 1]; containment counts as a perfect match."""
    na, nb = _normalize_for_match(a), _normalize_for_match(b)
    if not na or not nb:
        return 0.0
    if na in nb or nb in na:
        return 1.0
    return SequenceMatcher(None, na, nb).ratio()


def _duration_delta(track: TrackInfo, sp_track: dict[str, Any]) -> Optional[int]:
    """Absolute runtime difference in seconds, or None when unknown on either side."""
    duration_ms = sp_track.get("duration_ms")
    if not track.duration_seconds or not duration_ms:
        return None
    return abs(round(duration_ms / 1000) - track.duration_seconds)


def _spotify_artists(sp_track: dict[str, Any]) -> str:
    return ", ".join(a.get("name", "") for a in sp_track.get("artists", []))


def is_plausible_match(track: TrackInfo, sp_track: dict[str, Any], *, relaxed: bool) -> tuple[bool, str]:
    """
    Guards against confidently wrong matches (a cover, a live take, a 1h mix).
    ``relaxed`` candidates come from a free-text query, so they also need the
    title and artist to actually look alike.
    """
    delta = _duration_delta(track, sp_track)
    if delta is not None and delta > MAX_DURATION_DRIFT_SECONDS:
        return False, f"runtime differs by {delta}s"

    if not relaxed:
        return True, ""

    # The raw video title ("Artist - Song (Official Video)") carries both names
    # in whichever order, so it is checked alongside the derived fields.
    haystack = clean_track_title(track.raw_title or track.title)
    sp_name = sp_track.get("name", "")
    title_score = max(_text_score(clean_track_title(track.title), sp_name), _text_score(haystack, sp_name))
    if title_score < 0.6:
        return False, f"title similarity {title_score:.2f}"

    sp_artists = _spotify_artists(sp_track)
    artist_score = max(_text_score(track.artist, sp_artists), _text_score(haystack, sp_artists))
    if artist_score < 0.5 and (delta is None or delta > 10):
        return False, f"artist similarity {artist_score:.2f}"

    return True, ""


def search_spotify_track(sp: spotipy.Spotify, track: TrackInfo) -> Optional[dict[str, Any]]:
    """
    Searches for a track on Spotify using a hierarchical strategy:
    1. Direct ISRC search (exact match if available).
    2. Strict track name + primary artist search.
    3. Cleaned title + artist fallback search, validated against title/artist/runtime.
    """
    # 1. Search by ISRC - an exact identifier, no further validation needed.
    if track.isrc:
        try:
            res = sp.search(q=f"isrc:{track.isrc.strip()}", type="track", limit=1)
            items = res.get("tracks", {}).get("items", [])
            if items:
                logger.info(
                    f"  -> Match found via ISRC ({track.isrc}): "
                    f"'{items[0]['name']}' by {_spotify_artists(items[0])}"
                )
                return items[0]
        except Exception as e:
            logger.debug(f"ISRC query failed for '{track.isrc}': {e}")

    clean_title = clean_track_title(track.title)
    # Remove quotes from query parameters to prevent syntax errors in the Spotify Search API
    safe_title = clean_title.replace('"', "").strip()
    safe_artist = track.artist.replace('"', "").strip()

    # 2. Strict field query.
    strict_query = f'track:"{safe_title}" artist:"{safe_artist}"'
    try:
        res = sp.search(q=strict_query, type="track", limit=3)
        for candidate in res.get("tracks", {}).get("items", []):
            ok, reason = is_plausible_match(track, candidate, relaxed=False)
            if ok:
                logger.info(
                    f"  -> Match found via strict search: "
                    f"'{candidate['name']}' by {_spotify_artists(candidate)}"
                )
                return candidate
            logger.debug(f"  -> Rejected strict candidate '{candidate['name']}': {reason}")
    except Exception as e:
        logger.debug(f"Strict search failed for '{strict_query}': {e}")

    # 3. Relaxed free-text query on the full video title (it usually carries both
    #    artist and song, in whichever order), validated before accepting.
    #    Bracketed chatter ("(ineditas 2010)", "[HD]") only hurts the search.
    raw = clean_track_title(track.raw_title or track.title).replace('"', "").strip()
    raw = re.sub(r"\s*[\(\[].*$", "", raw).strip() or raw
    if _normalize_for_match(safe_artist) in _normalize_for_match(raw):
        relaxed_query = raw
    else:
        relaxed_query = f"{raw} {safe_artist}".strip()
    try:
        res = sp.search(q=relaxed_query, type="track", limit=5)
        for candidate in res.get("tracks", {}).get("items", []):
            ok, reason = is_plausible_match(track, candidate, relaxed=True)
            if ok:
                logger.info(
                    f"  -> Match found via relaxed search: "
                    f"'{candidate['name']}' by {_spotify_artists(candidate)}"
                )
                return candidate
            logger.debug(f"  -> Rejected relaxed candidate '{candidate['name']}': {reason}")
    except Exception as e:
        logger.debug(f"Relaxed search failed for '{relaxed_query}': {e}")

    return None


# --------------------------------------------------------------------------- #
# Pipeline
# --------------------------------------------------------------------------- #


def _flush_pending(
    sp: spotipy.Spotify,
    playlist_id: str,
    pending: list[tuple[str, str]],
    synced_yt_ids: Set[str],
    stats: SyncStats,
    dry_run: bool,
) -> None:
    """Adds a batch of (video_id, spotify_uri) pairs to the playlist in a single request."""
    if not pending:
        return

    uris = [uri for _, uri in pending]
    if dry_run:
        logger.info(f"[DRY-RUN] Would add {len(uris)} track(s) to the Spotify playlist.")
    else:
        try:
            sp.playlist_add_items(playlist_id, uris)
        except Exception as e:
            logger.error(f"Failed to add a batch of {len(uris)} track(s) to the playlist: {e}")
            stats.failed_to_add += len(uris)
            pending.clear()
            return
        logger.info(f"[ADDED] {len(uris)} track(s) added to the Spotify playlist.")

    stats.added_to_playlist += len(uris)
    # Only mark as synced once Spotify confirmed the write.
    if not dry_run:
        synced_yt_ids.update(video_id for video_id, _ in pending)
    pending.clear()


def run_sync(
    limit: Optional[int] = None, dry_run: bool = False, check_auth_only: bool = False
) -> SyncStats:
    """Main synchronization execution pipeline."""
    logger.info("=" * 60)
    logger.info("Starting YouTube Music -> Spotify Playlist Sync Pipeline")
    logger.info("=" * 60)

    raw_playlist = env("SPOTIFY_PLAYLIST_ID")
    if not raw_playlist:
        raise ConfigurationError("Environment variable 'SPOTIFY_PLAYLIST_ID' is not configured.")
    playlist_id = extract_playlist_id(raw_playlist)

    # 1. Authenticate both sides first: credentials problems must surface in
    #    seconds, before any state or playlist work.
    source = build_liked_songs_source()
    source.describe()
    sp = get_spotify_client()

    if check_auth_only:
        # Prove both tokens actually reach their APIs.
        source.fetch_liked(limit=1)
        sp.playlist_items(playlist_id, fields="items.track.id", limit=1, additional_types=["track"])
        logger.info("Credential check passed: YouTube Music and Spotify are both reachable.")
        return SyncStats()

    # 2. Load synchronization state
    state = load_state()
    synced_yt_ids: Set[str] = set(state.get("synced_yt_ids", []))
    initial_synced_count = len(synced_yt_ids)
    logger.info(f"Loaded {initial_synced_count} previously synced YouTube track ID(s).")

    # 3. Retrieve current playlist track IDs from Spotify
    existing_spotify_ids = get_spotify_playlist_track_ids(sp, playlist_id)

    # 4. Fetch Liked Songs from YouTube
    liked_tracks = source.fetch_liked(limit=limit)

    stats = SyncStats(total_processed=len(liked_tracks))
    pending: list[tuple[str, str]] = []

    # 5. Process each track. State is persisted in the finally block so a crash
    #    halfway through never loses the work already done.
    try:
        logger.info("Processing liked songs...")
        for idx, track in enumerate(liked_tracks, start=1):
            if track.video_id in synced_yt_ids:
                stats.skipped_synced_state += 1
                continue

            logger.info(
                f"[{idx}/{len(liked_tracks)}] Processing: '{track.title}' by '{track.artist}' "
                f"(YT ID: {track.video_id})"
            )

            spotify_track = search_spotify_track(sp, track)
            if not spotify_track:
                logger.warning(
                    f"  -> [NOT FOUND] No confident Spotify match for "
                    f"'{track.title}' - '{track.artist}'."
                )
                stats.not_found += 1
                stats.unmatched.append(f"{track.artist} - {track.title}")
                continue

            spotify_id = spotify_track.get("id")
            spotify_uri = spotify_track.get("uri")
            if not spotify_uri:
                stats.not_found += 1
                continue

            if spotify_id in existing_spotify_ids or spotify_uri in existing_spotify_ids:
                logger.info("  -> Track is already present in target Spotify playlist.")
                stats.already_in_playlist += 1
                synced_yt_ids.add(track.video_id)
                continue

            pending.append((track.video_id, spotify_uri))
            existing_spotify_ids.add(spotify_id)
            existing_spotify_ids.add(spotify_uri)

            if len(pending) >= SPOTIFY_ADD_BATCH_SIZE:
                _flush_pending(sp, playlist_id, pending, synced_yt_ids, stats, dry_run)

        _flush_pending(sp, playlist_id, pending, synced_yt_ids, stats, dry_run)
    finally:
        if dry_run:
            logger.info("[DRY-RUN] State file left untouched.")
        elif len(synced_yt_ids) != initial_synced_count or stats.unmatched:
            state["synced_yt_ids"] = list(synced_yt_ids)
            state["unmatched"] = stats.unmatched
            save_state(state)
        else:
            logger.info("No new tracks to persist to state file.")

    # 6. Summary report
    logger.info("=" * 60)
    logger.info("SYNCHRONIZATION COMPLETED - SUMMARY REPORT")
    logger.info("=" * 60)
    logger.info(f"  Total YouTube Liked Songs Checked: {stats.total_processed}")
    logger.info(f"  [+] Newly Added to Spotify:        {stats.added_to_playlist}")
    logger.info(f"  [=] Already in Spotify Playlist:   {stats.already_in_playlist}")
    logger.info(f"  [-] Skipped (Previously Synced):   {stats.skipped_synced_state}")
    logger.info(f"  [!] Not Found on Spotify:          {stats.not_found}")
    if stats.failed_to_add:
        logger.info(f"  [x] Failed to Add (API errors):    {stats.failed_to_add}")
    logger.info("=" * 60)
    if stats.unmatched:
        logger.info("Tracks without a confident Spotify match (retried on every run):")
        for name in stats.unmatched[:25]:
            logger.info(f"  - {name}")
        if len(stats.unmatched) > 25:
            logger.info(f"  ... and {len(stats.unmatched) - 25} more (see synced_tracks.json).")

    return stats


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sync YouTube Music liked songs to a Spotify playlist.")
    parser.add_argument("--limit", type=int, default=None, help="only process the N most recent liked songs")
    parser.add_argument(
        "--dry-run", action="store_true", help="search and report, but do not modify Spotify or the state file"
    )
    parser.add_argument(
        "--check-auth", action="store_true", help="verify both credentials reach their API and exit"
    )
    parser.add_argument("--verbose", action="store_true", help="enable debug logging")
    return parser.parse_args(argv)


if __name__ == "__main__":
    args = parse_args()
    if args.verbose:
        logger.setLevel(logging.DEBUG)
        logging.getLogger("ytm_spotify_sync.auth").setLevel(logging.DEBUG)

    try:
        run_sync(limit=args.limit, dry_run=args.dry_run, check_auth_only=args.check_auth)
    except KeyboardInterrupt:
        logger.info("Synchronization aborted by user.")
        sys.exit(130)
    except YouTubeMusicAuthError as exc:
        logger.critical("=" * 60)
        logger.critical(f"YOUTUBE MUSIC AUTHENTICATION FAILED: {exc}")
        if exc.remediation:
            for line in exc.remediation.splitlines():
                logger.critical(line)
        logger.critical("=" * 60)
        sys.exit(2)
    except ConfigurationError as exc:
        logger.critical("=" * 60)
        logger.critical(f"CONFIGURATION ERROR: {exc}")
        logger.critical("Set the missing value(s) in your .env file or as GitHub repository secrets.")
        logger.critical("See README.md -> 'Guia paso a paso para configurar credenciales'.")
        logger.critical("=" * 60)
        sys.exit(4)
    except SpotifyAuthError as exc:
        logger.critical("=" * 60)
        logger.critical(f"SPOTIFY AUTHENTICATION FAILED: {exc}")
        for line in SPOTIFY_REMEDIATION.splitlines():
            logger.critical(line)
        logger.critical("=" * 60)
        sys.exit(3)
    except Exception as exc:
        logger.critical(f"Critical execution failure: {exc}", exc_info=True)
        sys.exit(1)
