#!/usr/bin/env python3
"""
Offline tests for the sync pipeline
===================================
No network, no credentials. Run with ``python test_sync.py`` or ``pytest``.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import main
import ytm_auth
import youtube_data
from config import extract_playlist_id, sanitize_secret_value
from main import clean_track_title, is_plausible_match
from models import TrackInfo


def _spotify_track(name: str, artist: str, duration_s: int) -> dict:
    return {
        "id": "abc123",
        "uri": "spotify:track:abc123",
        "name": name,
        "artists": [{"name": artist}],
        "duration_ms": duration_s * 1000,
    }


# --------------------------------------------------------------------------- #
# Helpers / config
# --------------------------------------------------------------------------- #


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


# --------------------------------------------------------------------------- #
# Matching
# --------------------------------------------------------------------------- #


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


def test_match_uses_raw_title_when_artist_and_song_are_swapped():
    # A fan upload titled "Song - Artist": the derived fields are swapped, but
    # the raw title still carries both names.
    track = TrackInfo(
        video_id="v1",
        title="Pablo Hasel ( ineditas )",
        artist="Voy A Salir De Esta",
        duration_seconds=406,
        raw_title="Voy A Salir De Esta - Pablo Hasel ( ineditas )",
    )
    candidate = _spotify_track("Voy a Salir de Esta", "Pablo Hasél", 404)
    ok, reason = is_plausible_match(track, candidate, relaxed=True)
    assert ok, reason


# --------------------------------------------------------------------------- #
# YouTube Data API mapping
# --------------------------------------------------------------------------- #


def test_parse_iso8601_duration():
    assert youtube_data.parse_iso8601_duration("PT3M58S") == 238
    assert youtube_data.parse_iso8601_duration("PT1H11M32S") == 4292
    assert youtube_data.parse_iso8601_duration("PT45S") == 45
    assert youtube_data.parse_iso8601_duration("garbage") is None
    assert youtube_data.parse_iso8601_duration(None) is None


def test_derive_artist_and_title():
    assert youtube_data.derive_artist_and_title("Con la Misma Piedra", "Julio Iglesias - Topic") == (
        "Julio Iglesias",
        "Con la Misma Piedra",
    )
    assert youtube_data.derive_artist_and_title("Daft Punk - Get Lucky (Official Audio)", "DaftPunkVEVO") == (
        "Daft Punk",
        "Get Lucky (Official Audio)",
    )
    assert youtube_data.derive_artist_and_title("Nightcall", "KavinskyVEVO") == ("Kavinsky", "Nightcall")
    assert youtube_data.derive_artist_and_title("Some Song", "") == ("Unknown Artist", "Some Song")


def _video(video_id: str, title: str, channel: str, category: str, duration: str) -> dict:
    return {
        "id": video_id,
        "snippet": {"title": title, "channelTitle": channel, "categoryId": category},
        "contentDetails": {"duration": duration},
    }


class _FakeResponse:
    def __init__(self, status: int, payload: dict):
        self.status_code = status
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self) -> dict:
        return self._payload


class _FakeSession:
    """Serves canned Data API pages keyed by pageToken."""

    def __init__(self, pages: dict):
        self.pages = pages
        self.calls: list[dict] = []

    def get(self, url, params=None, headers=None, timeout=None):
        params = dict(params or {})
        self.calls.append(params)
        if url.endswith("/channels"):
            return _FakeResponse(200, {"items": [{"snippet": {"title": "Alfred"}}]})
        return _FakeResponse(200, self.pages[params.get("pageToken")])


def test_data_api_client_paginates_and_filters_music():
    pages = {
        None: {
            "items": [
                _video("a", "Con la Misma Piedra", "Julio Iglesias - Topic", "10", "PT3M58S"),
                _video("b", "Un podcast cualquiera", "Paranormal BlackDaga", "24", "PT1H11M32S"),
            ],
            "nextPageToken": "p2",
        },
        "p2": {"items": [_video("c", "Kavinsky - Nightcall", "Some Uploader", "10", "PT4M18S")]},
    }
    session = _FakeSession(pages)
    client = youtube_data.YouTubeDataClient("token", session)

    assert client.channel_title() == "Alfred"

    tracks = client.liked_music()
    assert [t.video_id for t in tracks] == ["a", "c"]  # the podcast (category 24) is ignored
    assert tracks[0].artist == "Julio Iglesias" and tracks[0].duration_seconds == 238
    assert tracks[1].artist == "Kavinsky" and tracks[1].title == "Nightcall"
    assert tracks[1].raw_title == "Kavinsky - Nightcall"
    # Second page requested with the continuation token.
    assert any(call.get("pageToken") == "p2" for call in session.calls)


def test_data_api_client_honours_limit():
    pages = {None: {"items": [_video(str(i), f"Song {i}", "X - Topic", "10", "PT3M") for i in range(5)]}}
    client = youtube_data.YouTubeDataClient("token", _FakeSession(pages))
    assert len(client.liked_music(limit=2)) == 2


def test_data_api_errors_are_translated():
    class _FailingSession:
        def get(self, url, params=None, headers=None, timeout=None):
            return _FakeResponse(
                403,
                {"error": {"message": "Quota exceeded", "errors": [{"reason": "quotaExceeded"}]}},
            )

    source = ytm_auth.DataApiSource("id", "secret", "refresh")
    source._client = youtube_data.YouTubeDataClient("token", _FailingSession())
    try:
        source.fetch_liked()
    except RuntimeError as exc:
        assert "quota" in str(exc).lower()
        assert not isinstance(exc, ytm_auth.YouTubeMusicAuthError)
    else:
        raise AssertionError("expected RuntimeError")


# --------------------------------------------------------------------------- #
# Expired session handling (legacy cookie path)
# --------------------------------------------------------------------------- #


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


def test_signed_out_cookie_source_raises_actionable_error():
    try:
        ytm_auth.CookieSource(_SignedOutYTMusic()).fetch_liked()
    except ytm_auth.YouTubeMusicAuthError as exc:
        assert "signed-out" in str(exc)
        assert "setup_ytm_oauth.py" in exc.remediation
    else:
        raise AssertionError("expected YouTubeMusicAuthError")


def test_refresh_token_is_read_from_json_or_plain_env():
    saved = {k: os.environ.pop(k, None) for k in ("YTM_OAUTH_JSON", "YTM_OAUTH_REFRESH_TOKEN")}
    try:
        os.environ["YTM_OAUTH_JSON"] = '{"access_token": "x", "refresh_token": "1//from-json"}'
        assert ytm_auth._load_refresh_token() == "1//from-json"
        os.environ.pop("YTM_OAUTH_JSON")
        os.environ["YTM_OAUTH_REFRESH_TOKEN"] = "1//plain"
        assert ytm_auth._load_refresh_token() == "1//plain"
        os.environ.pop("YTM_OAUTH_REFRESH_TOKEN")
        assert ytm_auth._load_refresh_token() is None
    finally:
        for key, value in saved.items():
            if value is not None:
                os.environ[key] = value


# --------------------------------------------------------------------------- #
# State
# --------------------------------------------------------------------------- #


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


class _FakeSource(ytm_auth.LikedSongsSource):
    mode = "fake"

    def __init__(self, tracks: list[TrackInfo]):
        self._tracks = tracks

    def describe(self):
        return "Tester"

    def fetch_liked(self, limit=None):
        return self._tracks[:limit] if limit else self._tracks


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
        TrackInfo("yt1", "Starboy (Official Video)", "The Weeknd", isrc="USUM71607007", duration_seconds=230),
        TrackInfo("yt2", "Nightcall", "Kavinsky", duration_seconds=258, raw_title="Kavinsky - Nightcall"),
        TrackInfo("yt3", "Cancion Inexistente", "Artista Fantasma", duration_seconds=200),
    ]

    fake_sp = _FakeSpotify(catalog)
    originals = (main.STATE_FILE_PATH, main.build_liked_songs_source, main.get_spotify_client)

    os.environ["SPOTIFY_PLAYLIST_ID"] = "https://open.spotify.com/playlist/PL123?si=x"
    main.STATE_FILE_PATH = tmp_path / "synced_tracks.json"
    main.build_liked_songs_source = lambda: _FakeSource(liked)
    main.get_spotify_client = lambda: fake_sp
    try:
        stats = main.run_sync()
    finally:
        main.STATE_FILE_PATH, main.build_liked_songs_source, main.get_spotify_client = originals
        os.environ.pop("SPOTIFY_PLAYLIST_ID", None)

    # Both matches go out in a single batched request, not one call per track.
    assert fake_sp.add_calls == [["spotify:track:s1", "spotify:track:s2"]]
    assert stats.added_to_playlist == 2
    assert stats.not_found == 1
    assert stats.unmatched == ["Artista Fantasma - Cancion Inexistente"]

    saved = json.loads((tmp_path / "synced_tracks.json").read_text(encoding="utf-8"))
    assert saved["synced_yt_ids"] == ["yt1", "yt2"]  # the unmatched track stays out of the state


def test_merge_state_unions_ids_and_keeps_newest_unmatched():
    import merge_state

    ours = {"last_sync": "2026-09-19T14:00:00+00:00", "synced_yt_ids": ["a", "c"], "unmatched": ["X - New"]}
    theirs = {"last_sync": "2026-09-19T13:00:00+00:00", "synced_yt_ids": ["a", "b"], "unmatched": ["Y - Old"]}
    merged = merge_state.merge_states(ours, theirs)
    assert merged["synced_yt_ids"] == ["a", "b", "c"]  # nothing lost from either run
    assert merged["unmatched"] == ["X - New"]  # newest run's view wins
    assert merged["last_sync"] == "2026-09-19T14:00:00+00:00"
    # Symmetric: the newer side may be "theirs".
    assert merge_state.merge_states(theirs, ours)["unmatched"] == ["X - New"]
    # Garbage / missing files count as empty.
    assert merge_state.merge_states({}, ours)["synced_yt_ids"] == ["a", "c"]


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
