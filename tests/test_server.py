"""Unit tests for server.py.

Everything here runs against a hand-rolled fake spotipy.Spotify client -- no
network, no Spotify credentials required. This complements (does not
replace) a real-account smoke test.
"""

import pytest
import spotipy
from spotipy.exceptions import SpotifyException

import server
from server import (
    AUTH_HELP,
    add_tracks_to_playlist,
    create_playlist,
    get_artist,
    get_artist_top_tracks,
    get_playlist_tracks,
    get_playlists,
    get_recently_played,
    get_recommendations,
    get_related_artists,
    get_saved_tracks,
    get_track,
    handle_errors,
    logout,
    search_music,
)


class _FakeSpotify:
    def __init__(
        self,
        search_results=None,
        playlists_pages=None,
        playlist_items_pages=None,
        saved_tracks_pages=None,
        track_result=None,
        recommendations_result=None,
        artist_result=None,
        artist_top_tracks_result=None,
        related_artists_result=None,
        recently_played_result=None,
        current_user_playlist_create_result=None,
        current_user_result=None,
        artist_albums_pages=None,
        album_tracks_pages=None,
    ):
        self._search_results = search_results
        self._playlists_pages = playlists_pages or []
        self._playlist_items_pages = playlist_items_pages or []
        self._saved_tracks_pages = saved_tracks_pages or []
        self._track_result = track_result
        self._recommendations_result = recommendations_result
        self._artist_result = artist_result
        self._artist_top_tracks_result = artist_top_tracks_result
        self._related_artists_result = related_artists_result
        self._recently_played_result = recently_played_result
        self._current_user_playlist_create_result = current_user_playlist_create_result
        self._current_user_result = current_user_result
        self._artist_albums_pages = artist_albums_pages or []
        self._album_tracks_pages = album_tracks_pages or []
        self.playlist_add_items_calls = []
        self.playlist_create_calls = []
        self.artist_albums_calls = []
        self.artist_albums_limits = []

    def _raise_if_exc(self, value):
        if isinstance(value, Exception):
            raise value
        return value

    def search(self, q, type, limit):
        return self._raise_if_exc(self._search_results)

    def current_user_playlists(self, limit):
        return self._raise_if_exc(self._playlists_pages[0])

    def playlist_items(self, playlist_id, limit):
        return self._raise_if_exc(self._playlist_items_pages[0])

    def current_user_saved_tracks(self, limit):
        return self._raise_if_exc(self._saved_tracks_pages[0])

    def current_user(self):
        return self._raise_if_exc(self._current_user_result)

    def artist_albums(self, artist_id, album_type=None, limit=None):
        self.artist_albums_calls.append((artist_id, album_type))
        self.artist_albums_limits.append(limit)
        return self._raise_if_exc(self._artist_albums_pages[0])

    def album_tracks(self, album_id, limit=None):
        return self._raise_if_exc(self._album_tracks_pages[0])

    def next(self, page):
        for pages_list in (self._playlists_pages, self._playlist_items_pages,
                           self._saved_tracks_pages, self._artist_albums_pages,
                           self._album_tracks_pages):
            if pages_list and pages_list[0] is page:
                pages_list.pop(0)
                return pages_list[0] if pages_list else None
        return None

    def track(self, track_id):
        return self._raise_if_exc(self._track_result)

    def recommendations(self, seed_tracks, limit):
        return self._raise_if_exc(self._recommendations_result)

    def artist(self, artist_id):
        return self._raise_if_exc(self._artist_result)

    def artist_top_tracks(self, artist_id):
        return self._raise_if_exc(self._artist_top_tracks_result)

    def artist_related_artists(self, artist_id):
        return self._raise_if_exc(self._related_artists_result)

    def current_user_recently_played(self, limit):
        return self._raise_if_exc(self._recently_played_result)

    # spotipy's one-call convenience method, which is what server.py uses.
    # The fake previously mirrored the older two-step
    # current_user() + user_playlist_create() pair instead, so this test
    # exercised a surface the server never touches and failed on the one it does.
    def current_user_playlist_create(self, name, public=False, description=""):
        self.playlist_create_calls.append((name, public, description))
        return self._raise_if_exc(self._current_user_playlist_create_result)

    def playlist_add_items(self, playlist_id, items):
        self.playlist_add_items_calls.append((playlist_id, list(items)))


def _install(monkeypatch, fake):
    monkeypatch.setattr(server, "_sp", fake)


# --- search_music --------------------------------------------------------


def test_search_music_tracks(monkeypatch):
    fake = _FakeSpotify(search_results={"tracks": {"items": [{"id": "t1", "name": "Song"}]}})
    _install(monkeypatch, fake)
    assert search_music("song", filter="track", limit=10) == [{"id": "t1", "name": "Song"}]


def test_search_music_artists(monkeypatch):
    fake = _FakeSpotify(search_results={"artists": {"items": [{"id": "a1", "name": "Artist"}]}})
    _install(monkeypatch, fake)
    assert search_music("artist", filter="artist", limit=1) == [{"id": "a1", "name": "Artist"}]


# --- get_playlists / get_playlist_tracks / get_saved_tracks -------------


def test_get_playlists_single_page(monkeypatch):
    page = {"items": [{"id": "p1", "name": "PL"}], "next": None}
    fake = _FakeSpotify(playlists_pages=[page])
    _install(monkeypatch, fake)
    assert get_playlists() == [{"id": "p1", "name": "PL"}]


def test_get_playlists_paginates_until_next_is_none(monkeypatch):
    page1 = {"items": [{"id": "p1"}], "next": "url"}
    page2 = {"items": [{"id": "p2"}], "next": None}
    fake = _FakeSpotify(playlists_pages=[page1, page2])
    _install(monkeypatch, fake)
    result = get_playlists()
    assert [p["id"] for p in result] == ["p1", "p2"]


def test_get_playlist_tracks_skips_local_and_null_tracks(monkeypatch):
    page = {
        "items": [
            {"track": {"id": "t1", "name": "Real Track"}},
            {"track": None},
            {"track": {"id": None, "name": "Local File"}},
        ],
        "next": None,
    }
    fake = _FakeSpotify(playlist_items_pages=[page])
    _install(monkeypatch, fake)
    result = get_playlist_tracks("pl1")
    assert [t["id"] for t in result] == ["t1"]


def test_get_saved_tracks(monkeypatch):
    page = {"items": [{"track": {"id": "t1", "name": "Liked"}}], "next": None}
    fake = _FakeSpotify(saved_tracks_pages=[page])
    _install(monkeypatch, fake)
    assert get_saved_tracks() == [{"id": "t1", "name": "Liked"}]


# --- get_track / get_recommendations -------------------------------------


def test_get_track(monkeypatch):
    fake = _FakeSpotify(track_result={"id": "t1", "name": "Song"})
    _install(monkeypatch, fake)
    assert get_track("t1") == {"id": "t1", "name": "Song"}


def test_get_recommendations(monkeypatch):
    fake = _FakeSpotify(recommendations_result={"tracks": [{"id": "r1"}]})
    _install(monkeypatch, fake)
    assert get_recommendations("seed1", limit=10) == [{"id": "r1"}]


def test_get_recommendations_no_seed_short_circuits(monkeypatch):
    fake = _FakeSpotify(recommendations_result=RuntimeError("should not be called"))
    _install(monkeypatch, fake)
    assert get_recommendations(None) == []


# --- get_artist / get_artist_top_tracks / get_related_artists -----------


def test_get_artist(monkeypatch):
    fake = _FakeSpotify(artist_result={"id": "a1", "name": "Artist"})
    _install(monkeypatch, fake)
    assert get_artist("a1") == {"id": "a1", "name": "Artist"}


def test_get_artist_top_tracks(monkeypatch):
    fake = _FakeSpotify(artist_top_tracks_result={"tracks": [{"id": "t1"}]})
    _install(monkeypatch, fake)
    assert get_artist_top_tracks("a1") == [{"id": "t1"}]


def test_get_related_artists(monkeypatch):
    fake = _FakeSpotify(related_artists_result={"artists": [{"id": "a2"}]})
    _install(monkeypatch, fake)
    assert get_related_artists("a1") == [{"id": "a2"}]


# --- get_recently_played --------------------------------------------------


def test_get_recently_played(monkeypatch):
    fake = _FakeSpotify(recently_played_result={"items": [{"track": {"id": "t1"}}]})
    _install(monkeypatch, fake)
    assert get_recently_played() == [{"track": {"id": "t1"}}]


# --- create_playlist / add_tracks_to_playlist ------------------------------


def test_create_playlist(monkeypatch):
    fake = _FakeSpotify(current_user_playlist_create_result={"id": "p1", "name": "jaytest"})
    _install(monkeypatch, fake)
    assert create_playlist("jaytest") == {"id": "p1", "name": "jaytest"}
    # Playlists default to private: this server is a discovery backend, and
    # creating public playlists on someone's account by default would be a
    # surprising side effect.
    assert fake.playlist_create_calls == [("jaytest", False, "")]


def test_create_playlist_passes_visibility_and_description(monkeypatch):
    fake = _FakeSpotify(current_user_playlist_create_result={"id": "p1", "name": "mix"})
    _install(monkeypatch, fake)
    create_playlist("mix", public=True, description="from re-com")
    assert fake.playlist_create_calls == [("mix", True, "from re-com")]


def test_add_tracks_to_playlist_single_batch(monkeypatch):
    fake = _FakeSpotify()
    _install(monkeypatch, fake)
    result = add_tracks_to_playlist("p1", ["t1", "t2"])
    assert fake.playlist_add_items_calls == [("p1", ["t1", "t2"])]
    assert "Added 2 track(s)" in result


def test_add_tracks_to_playlist_chunks_by_100(monkeypatch):
    fake = _FakeSpotify()
    _install(monkeypatch, fake)
    track_ids = [f"t{i}" for i in range(150)]
    add_tracks_to_playlist("p1", track_ids)
    assert [pid for pid, _ in fake.playlist_add_items_calls] == ["p1", "p1"]
    assert [len(batch) for _, batch in fake.playlist_add_items_calls] == [100, 50]


# --- handle_errors ---------------------------------------------------------


def _spotify_exc(status, msg="error", headers=None):
    exc = SpotifyException(status, -1, msg)
    exc.headers = headers or {}
    return exc


def test_handle_errors_401_is_auth_help():
    @handle_errors
    def fn():
        raise _spotify_exc(401)

    with pytest.raises(RuntimeError, match=AUTH_HELP.split(".")[0]):
        fn()


def test_handle_errors_403_mentions_restricted_endpoint():
    @handle_errors
    def fn():
        raise _spotify_exc(403)

    with pytest.raises(RuntimeError, match="403 Forbidden"):
        fn()


def test_handle_errors_429_mentions_rate_limit():
    @handle_errors
    def fn():
        raise _spotify_exc(429, headers={"Retry-After": "5"})

    with pytest.raises(RuntimeError, match="rate-limiting"):
        fn()


def test_handle_errors_other_status_wraps_message():
    @handle_errors
    def fn():
        raise _spotify_exc(500, msg="server exploded")

    with pytest.raises(RuntimeError, match="server exploded"):
        fn()


def test_handle_errors_oauth_error():
    @handle_errors
    def fn():
        raise spotipy.SpotifyOauthError("bad token")

    with pytest.raises(RuntimeError, match="bad token"):
        fn()


# --- logout ----------------------------------------------------------------


def test_logout_no_cache_file(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "CACHE_PATH", str(tmp_path / "missing_cache"))
    assert "nothing to remove" in logout()


def test_logout_removes_cache_file(monkeypatch, tmp_path):
    cache = tmp_path / "cache"
    cache.write_text("token")
    monkeypatch.setattr(server, "CACHE_PATH", str(cache))
    monkeypatch.setattr(server, "_sp", "connected")
    result = logout()
    assert not cache.exists()
    assert server._sp is None
    assert "Removed" in result


# --- identity, and the artist catalog that survives the restrictions --------


def test_get_current_user_returns_the_profile(monkeypatch):
    _install(monkeypatch, _FakeSpotify(current_user_result={"id": "jay", "display_name": "Jay"}))
    assert server.get_current_user()["id"] == "jay"


def test_artist_albums_pages_through_everything(monkeypatch):
    fake = _FakeSpotify(artist_albums_pages=[
        {"items": [{"id": "al1"}], "next": "more"},
        {"items": [{"id": "al2"}], "next": None},
    ])
    _install(monkeypatch, fake)
    assert [a["id"] for a in server.get_artist_albums("art1")] == ["al1", "al2"]


def test_artist_albums_excludes_appears_on_and_compilations(monkeypatch):
    # An "appears_on" album would credit this artist for someone else's record,
    # which is exactly the wrong answer for "songs by this artist".
    fake = _FakeSpotify(artist_albums_pages=[{"items": [], "next": None}])
    _install(monkeypatch, fake)
    server.get_artist_albums("art1")
    assert fake.artist_albums_calls == [("art1", "album,single")]


def test_artist_albums_honours_a_limit(monkeypatch):
    fake = _FakeSpotify(artist_albums_pages=[
        {"items": [{"id": "al1"}, {"id": "al2"}, {"id": "al3"}], "next": None},
    ])
    _install(monkeypatch, fake)
    assert len(server.get_artist_albums("art1", limit=2)) == 2


def test_album_tracks_pages_through_everything(monkeypatch):
    fake = _FakeSpotify(album_tracks_pages=[
        {"items": [{"id": "t1"}], "next": "more"},
        {"items": [{"id": "t2"}], "next": None},
    ])
    _install(monkeypatch, fake)
    assert [t["id"] for t in server.get_album_tracks("al1")] == ["t1", "t2"]


def test_artist_albums_requests_a_page_small_enough_for_a_restricted_app(monkeypatch):
    # Measured on a real restricted registration: limit=20 and above return
    # "400 Invalid limit" on this endpoint alone. Asking for more than the cap
    # fails the call outright rather than returning fewer albums, so the page
    # size is not a tuning knob.
    fake = _FakeSpotify(artist_albums_pages=[{"items": [], "next": None}])
    _install(monkeypatch, fake)
    server.get_artist_albums("art1", limit=500)
    assert fake.artist_albums_limits == [server._ARTIST_ALBUM_PAGE]


# --- the two playlist-row payload shapes ------------------------------------


def test_playlist_rows_in_the_documented_shape_are_read(monkeypatch):
    _install(monkeypatch, _FakeSpotify(playlist_items_pages=[
        {"items": [{"track": {"id": "t1", "name": "Song"}}], "next": None},
    ]))
    assert [t["id"] for t in server.get_playlist_tracks("p1")] == ["t1"]


def test_playlist_rows_with_the_track_under_item_are_read(monkeypatch):
    # The shape a real account actually returns: `track` is a boolean flag and
    # the object lives under `item`. Reading it["track"] dropped every row, so
    # playlists reporting total=20 came back empty and re-com's exclusion set
    # silently covered saved tracks only.
    _install(monkeypatch, _FakeSpotify(playlist_items_pages=[
        {"items": [{"track": True, "item": {"id": "t1", "name": "Song", "type": "track"}}],
         "next": None},
    ]))
    assert [t["id"] for t in server.get_playlist_tracks("p1")] == ["t1"]


def test_a_row_with_neither_shape_is_skipped_not_crashed(monkeypatch):
    _install(monkeypatch, _FakeSpotify(playlist_items_pages=[
        {"items": [{"track": None}, {"track": True, "item": None}, {}], "next": None},
    ]))
    assert server.get_playlist_tracks("p1") == []


def test_episodes_and_local_files_are_still_skipped(monkeypatch):
    # No id -- the original reason this filter existed.
    _install(monkeypatch, _FakeSpotify(playlist_items_pages=[
        {"items": [{"item": {"name": "An Episode", "type": "episode"}},
                   {"item": {"id": "t1", "name": "Song"}}], "next": None},
    ]))
    assert [t["id"] for t in server.get_playlist_tracks("p1")] == ["t1"]


def test_saved_tracks_read_the_same_shapes(monkeypatch):
    _install(monkeypatch, _FakeSpotify(saved_tracks_pages=[
        {"items": [{"track": {"id": "s1"}}, {"track": True, "item": {"id": "s2"}}],
         "next": None},
    ]))
    assert [t["id"] for t in server.get_saved_tracks()] == ["s1", "s2"]
