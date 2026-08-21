# spotify-mcp

An [MCP](https://modelcontextprotocol.io) server that wraps [spotipy](https://spotipy.readthedocs.io) so Claude (or any MCP client) can search Spotify and read playlists, saved tracks, artist catalogs, and discovery signals.

Built specifically as [`re-com`](https://github.com/umsachde/re-com)'s Spotify backend — read-heavy by design, not a playback-control server. It does support creating playlists and adding tracks to them (so a re-com recommendation list can become a real playlist), but there's no play/pause/queue control; look at one of the several playback-focused Spotify MCP servers already out there for that.

## Tools

| Tool | Description |
| --- | --- |
| `search_music(query, filter="track", limit=20)` | Search Spotify. `filter` is `track` or `artist`. |
| `get_playlists(limit=None)` | List the current user's playlists. Omit `limit` to fetch all of them. |
| `get_playlist_tracks(playlist_id, limit=None)` | Get the tracks in a playlist. Local files/episodes are skipped. |
| `get_saved_tracks(limit=None)` | Get the user's saved ("Liked Songs") tracks. |
| `get_track(track_id)` | Get a single track's metadata. |
| `get_recommendations(seed_track_id, limit=25)` | Spotify's algorithmic recommendations from one seed track — the closest analog to YouTube Music's radio. |
| `get_artist(artist_id)` | Get an artist's profile. |
| `get_artist_top_tracks(artist_id)` | An artist's top tracks (Spotify caps this at ~10 — there's no full-catalog endpoint). |
| `get_related_artists(artist_id)` | Artists related to the given one. |
| `get_recently_played(limit=50)` | The user's recently played tracks. |
| `create_playlist(name, public=False, description="")` | Create a new playlist owned by the current user. |
| `add_tracks_to_playlist(playlist_id, track_ids)` | Add tracks (by ID or URI) to a playlist, chunked in batches of 100. |
| `logout()` | Delete the cached OAuth token. |

**A real limitation, stated plainly:** Spotify restricts `/recommendations` and `artist_related_artists` for API apps created after November 2024 that don't have "Extended Quota Mode" (a manual approval Spotify grants sparingly). If your app doesn't have it, `get_recommendations` and `get_related_artists` will 403 — `handle_errors` turns that into a clear message rather than a raw traceback, and re-com's `spotify_client.py` treats it as one signal being unavailable, not a fatal error. `search_music`, playlists, saved tracks, and artist top tracks are unaffected.

Other Claude Code projects on this machine (e.g. `re-com`) call these tools by spawning this server over MCP rather than talking to `spotipy`/Spotify themselves — this is the only place Spotify credentials live.

## Setup

### 1. Register a Spotify app

1. Go to the [Spotify Developer Dashboard](https://developer.spotify.com/dashboard), create an app.
2. Add a redirect URI matching `SPOTIFY_REDIRECT_URI` (default `http://127.0.0.1:8888/callback` — you don't need anything actually listening on that port; see step 3).
3. Note the app's Client ID and Client Secret.

### 2. Install dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

### 3. Authenticate

```bash
export SPOTIFY_CLIENT_ID="..."
export SPOTIFY_CLIENT_SECRET="..."
python scripts/setup_auth_spotify.py
```

This prints an authorization URL, waits for you to log in and paste back the URL you're redirected to (works fine over SSH/headless — nothing needs to bind the redirect port), and writes the resulting token to `.spotify_cache` (path configurable via `SPOTIFY_CACHE_PATH`).

**`.spotify_cache` is equivalent to your logged-in session — never commit it or share it.** It's already gitignored. Tokens refresh automatically once cached; re-run this script only if refresh itself starts failing (e.g. the app's client secret was rotated, or you revoked access from your Spotify account settings), or if `SCOPE` in `server.py` gains new permissions (delete `.spotify_cache` first so the auth flow re-prompts for consent — a stale cached token won't pick up new scopes on its own).

### 4. Add to Claude Code

```bash
claude mcp add spotify -s user \
  -e SPOTIFY_CLIENT_ID="..." \
  -e SPOTIFY_CLIENT_SECRET="..." \
  -e SPOTIFY_CACHE_PATH="$(pwd)/.spotify_cache" \
  -- "$(pwd)/.venv/bin/python" "$(pwd)/server.py"
```

`-s user` makes it available in any Claude Code session, not just this directory. Use absolute paths for the python interpreter, `server.py`, and `SPOTIFY_CACHE_PATH` since the server can be launched from any working directory.

For other MCP clients (Claude Desktop, etc.), point them at the same command and env vars using their respective config format.

## Testing

The unit test suite (`tests/`) runs against a hand-rolled fake `spotipy.Spotify` client — no network access or Spotify credentials needed:

```bash
pip install -e ".[dev]"
pytest
```

## Error handling

Tool calls translate common failure modes into clear messages instead of raw tracebacks:

- Missing/expired auth (401) → tells you to redo [authenticate step](#3-authenticate).
- Restricted/forbidden (403) → tells you it's likely a Spotify API access restriction (see the recommendations/related-artists caveat above) or a missing OAuth scope.
- Rate limiting (429) → tells you to wait, including the `Retry-After` hint if Spotify sent one.
- Any other API or OAuth error is reported directly rather than as a raw traceback.

## License

MIT — see [LICENSE](LICENSE).
