"""Interactive Spotify OAuth setup.

Requires SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET (from a Spotify
Developer Dashboard app -- https://developer.spotify.com/dashboard) set in
the environment, plus that app's redirect URI added under its settings
matching SPOTIFY_REDIRECT_URI (default http://127.0.0.1:8888/callback).

Prints an authorization URL, waits for you to open it, log in, and paste
back the URL you were redirected to (this works over SSH/headless too --
nothing needs to bind a local port). Writes the resulting token to
SPOTIFY_CACHE_PATH (default .spotify_cache in the project root).
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from spotipy.oauth2 import SpotifyOAuth

from server import CACHE_PATH, REDIRECT_URI, SCOPE


def main() -> int:
    client_id = os.environ.get("SPOTIFY_CLIENT_ID")
    client_secret = os.environ.get("SPOTIFY_CLIENT_SECRET")
    if not client_id or not client_secret:
        print("Set SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET first (see this script's docstring).")
        return 1

    auth_manager = SpotifyOAuth(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=REDIRECT_URI,
        scope=SCOPE,
        cache_path=CACHE_PATH,
        open_browser=False,
    )

    auth_url = auth_manager.get_authorize_url()
    print("1. Open this URL and log in to Spotify:\n")
    print(f"   {auth_url}\n")
    print(f"2. You'll be redirected to a {REDIRECT_URI}?code=... URL that won't load -- that's expected.")
    print("   Copy that full URL from your browser's address bar and paste it below.\n")

    redirected = input("Paste the redirect URL: ").strip()
    if not redirected:
        print("No URL given.")
        return 1

    code = auth_manager.parse_response_code(redirected)
    try:
        auth_manager.get_access_token(code, as_dict=False)
    except Exception as e:
        print(f"Failed to complete auth: {e}")
        return 1

    print(f"Saved auth to {CACHE_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
