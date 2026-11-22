"""Upload completed inbox files to one explicitly authorized YouTube channel."""
import argparse
import os
import re
import tempfile
import time
import uuid
from pathlib import Path

from desktop_oauth import read_client

from google.auth.transport.requests import Request
from google.auth.exceptions import RefreshError
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
DEFAULT_STATE = Path.home() / ".youtube-auto"
DEFAULT_TOKEN = DEFAULT_STATE / "authorization.json"
EXTENSIONS = {".mp4", ".mov", ".webm", ".mkv", ".avi", ".mpeg", ".wmv", ".m4v"}


def save_token(path, creds):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False) as tmp:
        tmp.write(creds.to_json())
    os.replace(tmp.name, path)


class ConnectionRequired(RuntimeError):
    """The channel owner must authorize the uploader."""


def connect(client_json, token):
    flow = InstalledAppFlow.from_client_config(read_client(client_json), SCOPES, autogenerate_code_verifier=True)
    creds = flow.run_local_server(host="127.0.0.1", port=0, access_type="offline", prompt="consent")
    if not creds.refresh_token:
        raise ConnectionRequired("Google did not grant offline access.")
    save_token(token, creds)


def client(path):
    try:
        creds = Credentials.from_authorized_user_file(str(path), SCOPES)
    except FileNotFoundError as error:
        raise ConnectionRequired(
            f"First-time YouTube connection needed for this user. Expected a grant at {path}. "
            "Click Connect YouTube or run login_youtube.bat under the account that runs the uploader."
        ) from error
    except ValueError as error:
        raise ConnectionRequired(
            f"The saved YouTube grant at {path} could not be read. Reconnect using login_youtube.bat."
        ) from error
    if not creds.valid:
        try:
            creds.refresh(Request())
        except RefreshError as error:
            if any(isinstance(arg, dict) and arg.get("error") == "invalid_grant" for arg in error.args):
                raise ConnectionRequired("Google requires a new grant. Run login_youtube.bat or connect again.") from error
            raise
        save_token(path, creds)
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


class MetadataRequired(ValueError):
    pass


def opaque_title(title):
    return re.fullmatch(r"(?:rec[ _-]*)?[a-f0-9]{16,}|tg[_ ].+[_ ]\d+|rec[ _-]*\d+", title.strip(), re.IGNORECASE)


def check_public_metadata(file, title, description, visibility):
    if visibility.lower() != "public":
        return
    if opaque_title(str(title)) or not str(title or "").replace("<", "").replace(">", "").strip() or not str(description or "").strip():
        raise MetadataRequired(f"{file.name}: add a descriptive title and description using Queue > Edit "
                               "or a same-name .json/.txt file before public upload.")


def upload(service, file, *, snippet=None, status=None, retries=4, before_chunk=None, log=None):
    snippet = dict(snippet or {})
    title = str(snippet.get("title") or file.stem)
    snippet["title"] = title.replace("<", "").replace(">", "").strip()[:100] or "Video"
    status = status or {"privacyStatus": "public"}
    if snippet.get("description"):
        clean = snippet["description"].replace("<", "").replace(">", "").strip()
        snippet["description"] = clean.encode("utf-8")[:5000].decode("utf-8", errors="ignore")
    check_public_metadata(file, snippet["title"], snippet.get("description"), status["privacyStatus"])
    with file.open("rb") as stream:
        media = MediaIoBaseUpload(stream, mimetype="application/octet-stream",
                                  chunksize=8 * 1024 * 1024, resumable=True)
        request = service.videos().insert(
            part="snippet,status", media_body=media,
            body={"snippet": snippet, "status": status},
        )
        result = None
        while result is None:
            if before_chunk:
                before_chunk()
            _, result = request.next_chunk(num_retries=retries)
        actual = (result.get("status") or {}).get("privacyStatus")
        report = log or (lambda message, level: print(message, flush=True))
        requested = status["privacyStatus"]
        if not actual:
            report(f"{file.name}: requested {requested}. YouTube did not confirm visibility in its response.", "WARNING")
        elif actual != requested:
            report(f"{file.name}: requested {requested}, YouTube returned {actual}. Check YouTube Studio "
                   "and your project's YouTube API audit status.", "WARNING")
        else:
            report(f"{file.name}: YouTube returned visibility {actual}.", "INFO")
        return result["id"]


def scan(root, service, visibility="public", config=None):
    from yt_uploader import DEFAULT_CONFIG, IMAGE_EXTENSIONS, generate_metadata_ex, upload_thumbnail

    if config is None:
        config = dict(DEFAULT_CONFIG, cover_dir=str(root / "covers"))

    inbox = root / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    for file in sorted(inbox.iterdir()):
        if file.suffix.lower() not in EXTENSIONS or file.is_symlink() or not file.is_file():
            continue
        title, description, tags, fields = generate_metadata_ex(file, config)
        try:
            check_public_metadata(file, title, description, visibility)
        except MetadataRequired as error:
            print(error, flush=True)
            continue
        job = root / "processing" / uuid.uuid4().hex
        job.mkdir(parents=True)
        try:
            file.rename(job / file.name)
        except FileNotFoundError:
            job.rmdir()
            continue
        # A crash or ambiguous API response leaves the job here for reconciliation.
        for suffix in (".json", ".txt", *IMAGE_EXTENSIONS):
            companion = file.with_suffix(suffix)
            if companion.is_file():
                companion.rename(job / companion.name)
        snippet = {"title": title, "description": description, "categoryId": "22"}
        if tags:
            snippet["tags"] = tags
        video_id = upload(service, job / file.name, snippet=snippet, status={"privacyStatus": visibility})
        (job / "youtube-id.txt").write_text(video_id, encoding="utf-8")
        try:
            upload_thumbnail(service, config, job / file.name, video_id, fields)
        except Exception as error:
            print(f"Video {video_id} uploaded. Thumbnail failed ({type(error).__name__}).", flush=True)
        done = root / "done"
        done.mkdir(exist_ok=True)
        job.rename(done / job.name)
        print(f"{file.name}: {video_id}", flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    commands = parser.add_subparsers(dest="cmd", required=True)
    commands.add_parser("connect").add_argument("client_json")
    watch = commands.add_parser("watch")
    watch.add_argument("queue", type=Path)
    watch.add_argument("--visibility", choices=["public", "unlisted", "private"], default="public")
    args = parser.parse_args()
    token = args.state / "authorization.json"
    if args.cmd == "connect":
        connect(args.client_json, token)
        return
    if not token.is_file():
        parser.exit(1, "Connect this channel once before starting the worker.\n")
    service = client(token)
    from yt_uploader import load_config
    config = load_config()
    while True:
        scan(args.queue, service, args.visibility, config)
        time.sleep(10)


if __name__ == "__main__":
    main()
