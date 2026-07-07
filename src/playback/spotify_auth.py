"""One-time interactive Spotify authorization (Authorization Code + PKCE).

    read-the-room-spotify-auth        # or: python -m playback.spotify_auth

Opens the Spotify consent page, catches the redirect on a localhost
callback server, exchanges the code, and writes the token cache the
SpotifyProvider refreshes from ever after. PKCE means no client secret:
only RTR_PLAYBACK_CLIENT_ID is required (register an app at
developer.spotify.com with redirect URI
http://127.0.0.1:<RTR_PLAYBACK_REDIRECT_PORT>/callback).
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import sys
import threading
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import httpx

from .config import PlaybackConfig
from .spotify import ACCOUNTS_BASE, TOKEN_URL, save_token_cache, token_cache_from_response

AUTHORIZE_URL = f"{ACCOUNTS_BASE}/authorize"

# Playback control + reading the user's own playlists for the mapping.
SCOPES = (
    "user-read-playback-state user-modify-playback-state "
    "playlist-read-private playlist-read-collaborative"
)


def pkce_pair() -> tuple[str, str]:
    """(code_verifier, code_challenge) per RFC 7636, S256 method."""
    verifier = secrets.token_urlsafe(64)[:128]
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def build_authorize_url(
    client_id: str, redirect_uri: str, challenge: str, state: str
) -> str:
    params = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "code_challenge_method": "S256",
        "code_challenge": challenge,
        "scope": SCOPES,
        "state": state,
    }
    return f"{AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"


def wait_for_code(port: int, expected_state: str, timeout_s: float = 300.0) -> str:
    """One-shot localhost HTTP server: block until Spotify redirects back
    with ?code=...&state=..., validate state, return the code."""
    result: dict = {}
    done = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 (stdlib naming)
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if query.get("state", [None])[0] != expected_state:
                result["error"] = "state mismatch (CSRF?) — try again"
            elif "error" in query:
                result["error"] = query["error"][0]
            elif "code" in query:
                result["code"] = query["code"][0]
            else:
                result["error"] = "redirect carried no code"
            body = (
                b"Read the Room: authorization received. You can close this tab."
                if "code" in result
                else b"Read the Room: authorization FAILED. See the terminal."
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(body)
            done.set()

        def log_message(self, *args):  # silence request logging
            pass

    server = HTTPServer(("127.0.0.1", port), Handler)
    server.timeout = 1.0
    try:
        deadline = threading.Event()
        waiter = threading.Thread(target=lambda: (done.wait(timeout_s), deadline.set()))
        waiter.start()
        while not deadline.is_set():
            server.handle_request()
        waiter.join()
    finally:
        server.server_close()
    if "code" not in result:
        raise RuntimeError(result.get("error", f"no redirect within {timeout_s:.0f}s"))
    return result["code"]


def exchange_code(
    client: httpx.Client, code: str, verifier: str, client_id: str, redirect_uri: str
) -> dict:
    res = client.post(
        TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "code_verifier": verifier,
        },
    )
    if res.status_code != 200:
        raise RuntimeError(
            f"token exchange rejected ({res.status_code}): {res.text[:200]}"
        )
    return token_cache_from_response(res.json())


def run(config: PlaybackConfig, open_browser: bool = True) -> Path:
    verifier, challenge = pkce_pair()
    state = secrets.token_urlsafe(16)
    redirect_uri = f"http://127.0.0.1:{config.redirect_port}/callback"
    url = build_authorize_url(config.client_id, redirect_uri, challenge, state)

    print("Open this URL to authorize Read the Room with Spotify:\n")
    print(f"  {url}\n")
    print(f"(waiting for the redirect on {redirect_uri} ...)")
    if open_browser:
        webbrowser.open(url)

    code = wait_for_code(config.redirect_port, state)
    with httpx.Client(timeout=10.0) as client:
        cache = exchange_code(client, code, verifier, config.client_id, redirect_uri)
    path = Path(config.token_cache_path)
    save_token_cache(path, cache)
    return path


def main(argv: list[str] | None = None) -> int:
    config = PlaybackConfig.from_env()
    if not config.client_id:
        print(
            "RTR_PLAYBACK_CLIENT_ID is not set. Register an app at "
            "developer.spotify.com (redirect URI "
            f"http://127.0.0.1:{config.redirect_port}/callback) and put its "
            "client ID in .env.",
            file=sys.stderr,
        )
        return 2
    try:
        path = run(config)
    except (RuntimeError, OSError, httpx.HTTPError) as exc:
        print(f"authorization failed: {exc}", file=sys.stderr)
        return 1
    print(f"authorized ✓  token cache written to {path}")
    print("Playback control needs Spotify Premium on this account.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
