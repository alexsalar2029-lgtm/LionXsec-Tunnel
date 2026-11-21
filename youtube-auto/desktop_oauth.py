"""Locate a Google Desktop client and keep application identity separate from channel grants."""
import json
from pathlib import Path


PUBLISHER_CLIENT = "publisher_oauth_client.json"
SETUP_HELP = (
    "The publisher must supply a Google OAuth client JSON of type Desktop app. "
    "Enable YouTube Data API v3 and download the file from Google Auth Platform > Clients. "
    "Place it beside login_youtube.bat or select it in the file picker."
)


def read_client(path):
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (UnicodeError, ValueError) as error:
        raise ValueError("The selected file is not valid Google Desktop client JSON.") from error
    installed = document.get("installed") if isinstance(document, dict) else None
    if not isinstance(installed, dict):
        raise ValueError("Select the JSON downloaded for a Desktop app OAuth client. " + SETUP_HELP)
    client_id = installed.get("client_id")
    secret = installed.get("client_secret")
    if not (isinstance(client_id, str) and client_id.endswith(".apps.googleusercontent.com")
            and isinstance(secret, str) and secret.strip()):
        raise ValueError("The Google Desktop client JSON is missing a valid client ID or client secret.")
    # Desktop application credentials are distributed with the app. Only these
    # application fields are needed. The channel's grant is stored separately.
    return {"installed": {
        "client_id": client_id,
        "client_secret": secret,
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": ["http://localhost"],
    }}


def find_client(directory, configured):
    for path in (directory / configured, directory / PUBLISHER_CLIENT, directory / "client_secret.json"):
        if path.is_file():
            return path
    downloaded = [p for p in directory.glob("client_secret_*.json") if p.is_file()]
    if len(downloaded) == 1:
        return downloaded[0]
    if downloaded:
        raise FileNotFoundError("Several Google client files are beside the launcher. Select the intended Desktop app JSON.")
    raise FileNotFoundError(SETUP_HELP)


def pick_client_file(parent=None):
    from tkinter import TclError, Tk, filedialog

    window = None
    try:
        if parent is None:
            window = Tk()
            window.withdraw()
        return filedialog.askopenfilename(
            parent=parent if parent is not None else window,
            title="Select the publisher's Google Desktop OAuth client JSON",
            filetypes=[("Google Desktop OAuth JSON", "*.json")],
        )
    except TclError as error:
        raise FileNotFoundError("Cannot open a desktop file picker. Use --setup --client-json PATH. " + SETUP_HELP) from error
    finally:
        if window is not None:
            window.destroy()
