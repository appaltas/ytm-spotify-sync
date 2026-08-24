#!/usr/bin/env python3
"""
Spotify Refresh Token Helper
============================
Run this script locally once to obtain your SPOTIPY_REFRESH_TOKEN.

Prerequisites:
1. Set SPOTIPY_CLIENT_ID and SPOTIPY_CLIENT_SECRET in your .env or environment.
2. In Spotify Developer Dashboard, ensure "http://127.0.0.1:9090" is added to Redirect URIs.
"""

import os
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

CLIENT_ID = os.getenv("SPOTIPY_CLIENT_ID")
CLIENT_SECRET = os.getenv("SPOTIPY_CLIENT_SECRET")
REDIRECT_URI = "http://127.0.0.1:9090"
SCOPE = "playlist-modify-public playlist-modify-private playlist-read-private"

def main():
    print("=" * 60)
    print("Spotify Refresh Token Generator")
    print("=" * 60)

    if not CLIENT_ID or not CLIENT_SECRET:
        print("ERROR: SPOTIPY_CLIENT_ID or SPOTIPY_CLIENT_SECRET is missing.")
        print("Please set them in your .env file or environment variables.")
        return

    auth_manager = SpotifyOAuth(
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        redirect_uri=REDIRECT_URI,
        scope=SCOPE,
        open_browser=True,
    )

    print("\n1. Opening browser for Spotify Authorization...")
    print("2. If the browser does not open automatically, copy the URL printed in console.")
    print("3. After accepting, you will be redirected to http://127.0.0.1:9090 (it's normal if the page says 'can't be reached').")
    
    token_info = auth_manager.get_access_token(as_dict=True)

    if token_info and "refresh_token" in token_info:
        print("\n" + "=" * 60)
        print("SUCCESS! Your Spotify Refresh Token is:")
        print("=" * 60)
        print(f"\nSPOTIPY_REFRESH_TOKEN={token_info['refresh_token']}\n")
        print("=" * 60)
        print("Add this value to your GitHub Secrets and .env file.")
    else:
        print("\nCould not obtain refresh token. Please verify credentials and try again.")

if __name__ == "__main__":
    main()
