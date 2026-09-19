#!/usr/bin/env python3
"""
Offline tests for the sync pipeline
===================================
No network, no credentials. Run with ``python test_sync.py`` or ``pytest``.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import main
import ytm_auth
from config import extract_playlist_id, sanitize_secret_value
from main import TrackInfo, clean_track_title, is_plausible_match


def _spotify_track(name: str, artist: str, duration_s: int) -> dict:
    return {
        "id": "abc123",
        "uri": "spotify:track:abc123",
        "name": name,
        "artists": [{"name": artist}],
        "duration_ms": duration_s * 1000,
    }


def test_clean_track_title_strips_noise():
    assert clean_track_title("Starboy (Official Video)") == "Starboy"
    assert clean_track_title("Nightcall [Audio] [HQ]") == "Nightcall"
    assert clean_track_title("Bohemian Rhapsody") == "Bohemian Rhapsody"


def test_extract_playlist_id_accepts_every_shape():
    assert extract_playlist_id("https://open.spotify.com/playlist/37i9dQZF1?si=deadbeef") == "37i9dQZF1"
    assert extract_playlist_id("spotify:playlist:37i9dQZF1") == "37i9dQZF1"
    assert extract_playlist_id('  "37i9dQZF1"  ') == "37i9dQZF1"
    assert extract_playlist_id("SPOTIFY_PLAYLIST_ID=37i9dQZF1") == "37i9dQZF1"


def test_sanitize_secret_value_keeps_json_untouched():
    payload = '{"Cookie": "a=b; c=d"}'
    assert sanitize_secret_value(payload) == payload


def test_match_rejects_wrong_runtime():
    track = TrackInfo(video_id="v1", title="Starboy", artist="The Weeknd", duration_seconds=230)
    one_hour_mix = _spotify_track("Starboy", "The Weeknd", 3600)
    ok, reason = is_plausible_match(track, one_hour_mix, relaxed=False)
    assert not ok and "runtime" in reason


def test_match_rejects_unrelated_relaxed_candidate():
    track = TrackInfo(video_id="v1", title="Starboy", artist="The Weeknd", duration_seconds=230)
    unrelated = _spotify_track("Cumbia del Sol", "Los Tigres", 228)
    ok, reason = is_plausible_match(track, unrelated, relaxed=True)
    assert not ok and "title similarity" in reason


def test_match_accepts_same_song_with_accents_and_noise():
    track = TrackInfo(
        video_id="v1", title="Corazon Espinado (Official Video)", artist="Santana", duration_seconds=278
    )
    candidate = _spotify_track("Corazón Espinado", "Santana", 280)
    ok, _ = is_plausible_match(track, candidate, relaxed=True)
    assert ok


def test_expired_session_detection():
    signed_out = (
        "Unable to find 'twoColumnBrowseResultsRenderer' using path [...] on "
        "{'singleColumnBrowseResultsRenderer': {...'Sign in'...}}"
    )
    assert ytm_auth.looks_like_expired_session(signed_out)
    assert ytm_auth.looks_like_expired_session("invalid_grant: Token has been expired or revoked.")
    assert not ytm_auth.looks_like_expired_session("HTTPSConnectionPool: read timed out")


class _SignedOutYTMusic:
    """Mimics ytmusicapi when YouTube Music answers with the signed-out page."""

    def get_liked_songs(self, limit=None):
        raise KeyError(
            "Unable to find 'twoColumnBrowseResultsRenderer' using path "
            "['contents', 'twoColumnBrowseResultsRenderer'] on "
            "{'singleColumnBrowseResultsRenderer': {'signInEndpoint': {'hack': True}}}"
        )


def test_signed_out_fetch_raises_actionable_error():
    try:
        main.fetch_ytmusic_liked_songs(_SignedOutYTMusic(), "browser")
    except ytm_auth.YouTubeMusicAuthError as exc:
        assert "signed-out" in str(exc)
        assert "setup_ytm_oauth.py" in exc.remediation
    else:
        raise AssertionError("expected YouTubeMusicAuthError")


def test_oauth_token_is_normalized_for_ytmusicapi():
    token = ytm_auth._normalize_oauth_token({"refresh_token": "1//refresh"})
    assert set(token) == {
        "scope",
        "token_type",
        "access_token",
        "refresh_token",
        "expires_at",
        "expires_in",
    }
    # Forced expiry guarantees a fresh access token on every stateless CI run.
    assert token["expires_at"] == 0
    assert token["token_type"] == "Bearer"


def test_state_round_trip(tmp_path: Path | None = None):
    tmp_path = tmp_path or Path(tempfile.mkdtemp())
    original = main.STATE_FILE_PATH
    main.STATE_FILE_PATH = tmp_path / "synced_tracks.json"
    try:
        assert main.load_state() == {"last_sync": None, "synced_yt_ids": [], "unmatched": []}
        main.save_state({"synced_yt_ids": ["b", "a", "b"], "unmatched": ["X - Y"]})
        saved = json.loads(main.STATE_FILE_PATH.read_text(encoding="utf-8"))
        assert saved["synced_yt_ids"] == ["a", "b"]  # deduplicated + sorted for clean diffs
        assert saved["last_sync"] is not None
        assert main.load_state()["unmatched"] == ["X - Y"]
    finally:
        main.STATE_FILE_PATH = original


# --------------------------------------------------------------------------- #
# End-to-end pipeline test with fake APIs
# --------------------------------------------------------------------------- #


class _FakeYTMusic:
    def __init__(self, tracks):
        self._tracks = tracks

    def get_liked_songs(self, limit=None):
        return {"tracks": self._tracks[:limit] if limit else self._tracks}


class _FakeSpotify:
    """Minimal stand-in for the subset of the Spotify API the sync uses."""

    def __init__(self, catalog):
        self.catalog = catalog
        self.add_calls: list[list[str]] = []

    def playlist_items(self, playlist_id, fields=None, limit=None, additional_types=None):
        return {"items": [], "next": None}

    def next(self, results):
        return None

    def search(self, q, type="track", limit=1):
        q_lower = q.lower()
        hits = []
        for entry in self.catalog:
            if q_lower.startswith("isrc:"):
                if entry.get("isrc", "").lower() == q_lower.split("isrc:", 1)[1].strip():
                    hits.append(entry)
            elif entry["name"].lower().split()[0] in q_lower:
                hits.append(entry)
        return {"tracks": {"items": hits[:limit]}}

    def playlist_add_items(self, playlist_id, uris):
        self.add_calls.append(list(uris))


def _liked(video_id, title, artist, duration, isrc=None):
    item = {
        "videoId": video_id,
        "title": title,
        "artists": [{"name": artist}],
        "duration_seconds": duration,
    }
    if isrc:
        item["isrc"] = isrc
    return item


def test_pipeline_batches_adds_and_persists_state(tmp_path: Path | None = None):
    tmp_path = tmp_path or Path(tempfile.mkdtemp())
    catalog = [
        {
            "id": "s1",
            "uri": "spotify:track:s1",
            "name": "Starboy",
            "artists": [{"name": "The Weeknd"}],
            "duration_ms": 230_000,
            "isrc": "USUM71607007",
        },
        {
            "id": "s2",
            "uri": "spotify:track:s2",
            "name": "Nightcall",
            "artists": [{"name": "Kavinsky"}],
            "duration_ms": 258_000,
        },
    ]
    liked = [
        _liked("yt1", "Starboy (Official Video)", "The Weeknd", 230, isrc="USUM71607007"),
        _liked("yt2", "Nightcall [Audio]", "Kavinsky", 258),
        _liked("yt3", "Cancion Inexistente", "Artista Fantasma", 200),
    ]

    fake_sp = _FakeSpotify(catalog)
    originals = (
        main.STATE_FILE_PATH,
        main.build_ytmusic_client,
        main.describe_session,
        main.get_spotify_client,
    )
    import os

    os.environ["SPOTIFY_PLAYLIST_ID"] = "https://open.spotify.com/playlist/PL123?si=x"
    main.STATE_FILE_PATH = tmp_path / "synced_tracks.json"
    main.build_ytmusic_client = lambda: (_FakeYTMusic(liked), "oauth")
    main.describe_session = lambda client, mode: "Tester"
    main.get_spotify_client = lambda: fake_sp
    try:
        stats = main.run_sync()
    finally:
        (
            main.STATE_FILE_PATH,
            main.build_ytmusic_client,
            main.describe_session,
            main.get_spotify_client,
        ) = originals
        os.environ.pop("SPOTIFY_PLAYLIST_ID", None)

    # Both matches go out in a single batched request, not one call per track.
    assert fake_sp.add_calls == [["spotify:track:s1", "spotify:track:s2"]]
    assert stats.added_to_playlist == 2
    assert stats.not_found == 1
    assert stats.unmatched == ["Artista Fantasma - Cancion Inexistente"]

    saved = json.loads((tmp_path / "synced_tracks.json").read_text(encoding="utf-8"))
    assert saved["synced_yt_ids"] == ["yt1", "yt2"]  # the unmatched track stays out of the state


def _run_all() -> int:
    failures = 0
    for name, func in sorted(globals().items()):
        if not name.startswith("test_") or not callable(func):
            continue
        try:
            func()
            print(f"PASS {name}")
        except AssertionError as exc:
            failures += 1
            print(f"FAIL {name}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"ERROR {name}: {exc!r}")
    print("-" * 50)
    print("All tests passed." if not failures else f"{failures} test(s) failed.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(_run_all())
