"""MCP server exposing Spotify (via spotipy) as tools for Claude.

Read-heavy by design -- built specifically for re-com's Spotify provider
(see re-com/spotify_client.py), not as a general playback-control server.
Tools cover search, playlists/saved tracks, an artist's top tracks and
related artists, seed-track recommendations, and recently played -- the same
kind of surface ytmusic-mcp exposes for YouTube Music. Two write tools
(create_playlist, add_tracks_to_playlist) exist so a re-com recommendation
list can be turned into a real Spotify playlist.

Some endpoints here (recommendations, related-artists) are restricted by
Spotify for API apps created after November 2024's policy change; if your
app doesn't have "Extended Quota Mode" or grandfathered access, those calls
fail with a 403 that `handle_errors` turns into a clear message rather than
a raw traceback -- re-com's spotify_client.py already treats that as a
signal that's simply unavailable, not a fatal error.
"""

import functools
import os
from typing import Any

import spotipy
from mcp.server.mcpserver import MCPServer
from spotipy.exceptions import SpotifyException
from spotipy.oauth2 import SpotifyOAuth

CACHE_PATH = os.environ.get("SPOTIFY_CACHE_PATH", ".spotify_cache")
REDIRECT_URI = os.environ.get("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8888/callback")
SCOPE = (
    "user-library-read "
    "playlist-read-private "
    "playlist-read-collaborative "
    "user-read-recently-played "
    "user-top-read "
    "playlist-modify-public "
    "playlist-modify-private"
)
AUTH_HELP = (
    "Spotify auth looks invalid, expired, or missing. Run scripts/setup_auth_spotify.py "
    "to authenticate (see README)."
)

mcp = MCPServer("spotify")

_sp: spotipy.Spotify | None = None


def _auth_manager() -> SpotifyOAuth:
    client_id = os.environ.get("SPOTIFY_CLIENT_ID")
    client_secret = os.environ.get("SPOTIFY_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise RuntimeError(
            "SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET must be set (from your Spotify "
            "Developer Dashboard app). See README."
        )
    return SpotifyOAuth(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=REDIRECT_URI,
        scope=SCOPE,
        cache_path=CACHE_PATH,
        open_browser=False,
    )


def _client() -> spotipy.Spotify:
    global _sp
    if _sp is None:
        _sp = spotipy.Spotify(auth_manager=_auth_manager())
    return _sp


def handle_errors(fn):
    """Translate spotipy/network failures into clear, actionable messages.

    Mirrors ytmusic-mcp's handle_errors: auth/rate-limit/restricted-endpoint/
    network failures become one clean RuntimeError instead of a raw
    traceback or spotipy's own exception type.
    """

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except SpotifyException as e:
            if e.http_status in (401, 403) and "insufficient" not in (e.msg or "").lower():
                if e.http_status == 401:
                    raise RuntimeError(AUTH_HELP) from e
                raise RuntimeError(
                    "Spotify returned 403 Forbidden. Either this app lacks access to this "
                    "endpoint (Spotify restricts /recommendations and related-artists for "
                    "apps created after Nov 2024 without Extended Quota Mode), or the "
                    "required OAuth scope wasn't granted -- rerun scripts/setup_auth_spotify.py."
                ) from e
            if e.http_status == 429:
                retry_after = e.headers.get("Retry-After") if e.headers else None
                hint = f" (retry after {retry_after}s)" if retry_after else ""
                raise RuntimeError(f"Spotify is rate-limiting requests right now{hint}.") from e
            raise RuntimeError(f"Spotify API error: {e.msg or e}") from e
        except spotipy.SpotifyOauthError as e:
            raise RuntimeError(f"{AUTH_HELP} ({e})") from e

    return wrapper


# Spotify documents a maximum page size of 50 for /artists/{id}/albums, and
# rejects anything above 10 on a restricted app registration -- measured: 50,
# 49 and 20 all return "400 Invalid limit", 10 succeeds. The cap is specific to
# this endpoint; album_tracks and current_user_playlists both accept 50 on the
# same registration, so only this one pays for smaller pages. Pagination still
# works underneath the cap, so nothing is lost but round-trips.
_ARTIST_ALBUM_PAGE = 10


def _paginate(first_page: dict[str, Any], limit: int | None, item_key: str = "items") -> list[Any]:
    """Follow spotipy's cursor-paginated responses until exhausted or `limit`
    items are collected. `limit=None` fetches everything."""
    sp = _client()
    items: list[Any] = []
    page = first_page
    while page:
        items.extend(page.get(item_key, []))
        if limit is not None and len(items) >= limit:
            return items[:limit]
        page = sp.next(page) if page.get("next") else None
    return items


@mcp.tool()
@handle_errors
def search_music(query: str, filter: str = "track", limit: int = 20) -> list[dict[str, Any]]:
    """Search Spotify. `filter` is "track" or "artist"."""
    kind = "artist" if filter == "artist" else "track"
    result = _client().search(q=query, type=kind, limit=min(limit, 50))
    key = "artists" if kind == "artist" else "tracks"
    return result.get(key, {}).get("items", [])


@mcp.tool()
@handle_errors
def get_playlists(limit: int | None = None) -> list[dict[str, Any]]:
    """List the current user's playlists. Omit `limit` to fetch all of them."""
    first = _client().current_user_playlists(limit=min(limit or 50, 50))
    return _paginate(first, limit)


def _track_of(item: dict[str, Any]) -> dict[str, Any] | None:
    """The track object inside a playlist/saved-tracks row, in either shape.

    Spotify returns two different payloads for a playlist row. The documented
    one puts the track object under `track`. The one this account actually
    receives puts it under `item`, and uses `track` as a *boolean* flag
    meaning "this is a track, not an episode":

        {"added_at": ..., "track": null, "item": {"type": "track", ...}}

    Reading `it["track"]` therefore yielded None for every row, so the filter
    dropped all of them and every playlist came back **empty** -- measured, a
    playlist reporting `total=20` returned 0 tracks. That is not a cosmetic
    bug: re-com builds its library exclusion set from these rows, so its
    never-recommend-what-you-already-have guarantee silently covered saved
    tracks only, and `recommend_from_playlist` had nothing to seed from.

    Episodes and local files have no `id` and are still skipped.
    """
    candidate = item.get("track")
    if not isinstance(candidate, dict):
        # `track: true` (the flag form) or null -- the object is under `item`.
        candidate = item.get("item")
    if not isinstance(candidate, dict) or not candidate.get("id"):
        return None
    return candidate


@mcp.tool()
@handle_errors
def get_playlist_tracks(playlist_id: str, limit: int | None = None) -> list[dict[str, Any]]:
    """Get the tracks in a playlist. Omit `limit` to fetch the entire playlist.

    Local files and episodes (no track id) are skipped. See `_track_of` for
    the two payload shapes this has to read.
    """
    first = _client().playlist_items(playlist_id, limit=min(limit or 100, 100))
    raw_items = _paginate(first, None)  # page fully; trim to `limit` after filtering below
    tracks = [t for t in (_track_of(it) for it in raw_items) if t]
    return tracks[:limit] if limit is not None else tracks


@mcp.tool()
@handle_errors
def get_saved_tracks(limit: int | None = None) -> list[dict[str, Any]]:
    """Get the current user's saved ("Liked Songs") tracks. Omit `limit` for all of them."""
    first = _client().current_user_saved_tracks(limit=min(limit or 50, 50))
    raw_items = _paginate(first, None)
    # Saved tracks still arrive in the documented shape, but share the reader:
    # one place to fix if this payload shifts the way playlist rows did.
    tracks = [t for t in (_track_of(it) for it in raw_items) if t]
    return tracks[:limit] if limit is not None else tracks


@mcp.tool()
@handle_errors
def get_track(track_id: str) -> dict[str, Any]:
    """Get a single track's metadata (title, artists, album, ...)."""
    return _client().track(track_id)


@mcp.tool()
@handle_errors
def get_recommendations(seed_track_id: str | None, limit: int = 25) -> list[dict[str, Any]]:
    """Get Spotify's algorithmic recommendations seeded from one track.

    May 403 if this app doesn't have access to /recommendations -- Spotify
    restricts it for apps created after Nov 2024 without Extended Quota Mode.
    """
    if not seed_track_id:
        return []
    result = _client().recommendations(seed_tracks=[seed_track_id], limit=min(limit, 100))
    return result.get("tracks", [])


@mcp.tool()
@handle_errors
def get_artist(artist_id: str) -> dict[str, Any]:
    """Get an artist's profile (name, genres, popularity, images)."""
    return _client().artist(artist_id)


@mcp.tool()
@handle_errors
def get_artist_top_tracks(artist_id: str) -> list[dict[str, Any]]:
    """Get an artist's top tracks (Spotify caps this at ~10 -- there's no
    "full catalog" endpoint the way YouTube Music's channel Songs tab has)."""
    return _client().artist_top_tracks(artist_id).get("tracks", [])


@mcp.tool()
@handle_errors
def get_related_artists(artist_id: str) -> list[dict[str, Any]]:
    """Get artists related to the given one.

    May 403 for apps without access to this endpoint -- same restriction as
    /recommendations.
    """
    return _client().artist_related_artists(artist_id).get("artists", [])


@mcp.tool()
@handle_errors
def get_recently_played(limit: int = 50) -> list[dict[str, Any]]:
    """Get the user's recently played tracks (each item wraps a "track" key)."""
    result = _client().current_user_recently_played(limit=min(limit, 50))
    return result.get("items", [])


@mcp.tool()
@handle_errors
def get_current_user() -> dict[str, Any]:
    """Get the authenticated user's own profile (id, display_name, ...).

    The `id` is the only way to tell a playlist the user owns from one they
    merely follow: `get_playlists` returns both, indistinguishable except by
    `owner.id`. That distinction is load-bearing rather than cosmetic --
    Spotify 403s `playlist_items` on another user's playlist, so a caller that
    treats followed playlists as its own gets an error it cannot act on.
    """
    return _client().current_user()


@mcp.tool()
@handle_errors
def get_artist_albums(artist_id: str, limit: int | None = None) -> list[dict[str, Any]]:
    """Get an artist's albums and singles. Omit `limit` to fetch all of them.

    This plus `get_album_tracks` is the only route to an artist's real catalog
    that survives Spotify's post-Nov-2024 restrictions: `get_artist_top_tracks`
    403s for apps without Extended Quota Mode and caps at ~10 even when it
    works. Appears-on and compilation albums are excluded -- they would credit
    the artist for other people's records.
    """
    first = _client().artist_albums(
        artist_id, album_type="album,single", limit=min(limit or _ARTIST_ALBUM_PAGE, _ARTIST_ALBUM_PAGE)
    )
    return _paginate(first, limit)


@mcp.tool()
@handle_errors
def get_album_tracks(album_id: str, limit: int | None = None) -> list[dict[str, Any]]:
    """Get an album's tracks. Omit `limit` to fetch all of them.

    Album track objects are the "simplified" shape and carry no `album` key of
    their own, so callers that need one should attach it from the album they
    asked for.
    """
    first = _client().album_tracks(album_id, limit=min(limit or 50, 50))
    return _paginate(first, limit)


@mcp.tool()
@handle_errors
def create_playlist(name: str, public: bool = False, description: str = "") -> dict[str, Any]:
    """Create a new playlist owned by the current user and return it (id, name, ...)."""
    return _client().current_user_playlist_create(name, public=public, description=description)


@mcp.tool()
@handle_errors
def add_tracks_to_playlist(playlist_id: str, track_ids: list[str]) -> str:
    """Add tracks to a playlist by Spotify track ID (or URI). Chunks in batches of 100,
    Spotify's per-request limit."""
    sp = _client()
    for i in range(0, len(track_ids), 100):
        sp.playlist_add_items(playlist_id, track_ids[i : i + 100])
    return f"Added {len(track_ids)} track(s) to playlist {playlist_id}."


@mcp.tool()
def logout() -> str:
    """Delete the cached Spotify OAuth token.

    Subsequent tool calls will fail until you re-authenticate via
    scripts/setup_auth_spotify.py.
    """
    global _sp
    if not os.path.exists(CACHE_PATH):
        return f"No cached token found at {CACHE_PATH}; nothing to remove."
    os.remove(CACHE_PATH)
    _sp = None
    return f"Removed {CACHE_PATH}. Re-run scripts/setup_auth_spotify.py to authenticate again."


if __name__ == "__main__":
    mcp.run()
