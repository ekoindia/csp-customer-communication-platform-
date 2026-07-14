"""
Persists a handful of keys into the local .env file (creating it if it
doesn't exist yet — a fresh CSP install ships without one, see
MAKE_ZIP.ps1's exclusions). Used by the first-run "Connect to Eko Admin
Portal" screen (dashboard/routes.py) so the CSP ID + API key issued by the
admin survive app restarts without editing any file by hand.

Thin wrapper around python-dotenv's set_key — kept separate so the route
handler doesn't need to know the .env file's location.
"""
import os

from dotenv import set_key

_ENV_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")


def write_values(values: dict) -> None:
    """Write/update each key=value pair in .env. Creates the file if missing."""
    if not os.path.exists(_ENV_PATH):
        open(_ENV_PATH, "a", encoding="utf-8").close()
    for key, value in values.items():
        set_key(_ENV_PATH, key, str(value), quote_mode="never")
