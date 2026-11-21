"""Upload completed inbox files to one explicitly authorized YouTube channel."""
import argparse
import os
import tempfile
import time
import uuid
from pathlib import Path

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
    flow = InstalledAppFlow.from_client_secrets_file(str(client_json), SCOPES)
    creds = flow.run_local_server(host="127.0.0.1", port=0, access_type="offline", prompt="consent")
    if not creds.refresh_token:
        raise ConnectionRequired("Google did not grant offline access.")
    save_token(token, creds)


def client(path):
    try:
        creds = Credentials.from_authorized_user_file(str(path), SCOPES)
    except (FileNotFoundError, ValueError) as error:
        raise ConnectionRequired("Connect your YouTube channel with login_youtube.bat or the connect command.") from error
    if not creds.valid:
        try:
            creds.refresh(Request())
        except RefreshError as error:
            if any(isinstance(arg, dict) and arg.get("error") == "invalid_grant" for arg in error.args):
                raise ConnectionRequired("Google requires a new grant. Run login_youtube.bat or connect again.") from error
            raise
        save_token(path, creds)
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def upload(service, file, *, snippet=None, status=None, retries=4, before_chunk=None):
    snippet = dict(snippet or {})
    title = str(snippet.get("title") or file.stem)
    snippet["title"] = title.replace("<", "").replace(">", "").strip()[:100] or "Video"
    with file.open("rb") as stream:
        media = MediaIoBaseUpload(stream, mimetype="application/octet-stream",
                                  chunksize=8 * 1024 * 1024, resumable=True)
        request = service.videos().insert(
            part="snippet,status", media_body=media,
            body={"snippet": snippet, "status": status or {"privacyStatus": "private"}},
        )
        result = None
        while result is None:
            if before_chunk:
                before_chunk()
            _, result = request.next_chunk(num_retries=retries)
        return result["id"]


def scan(root, service):
    inbox = root / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    for file in sorted(inbox.iterdir()):
        if file.suffix.lower() not in EXTENSIONS or file.is_symlink() or not file.is_file():
            continue
        job = root / "processing" / uuid.uuid4().hex
        job.mkdir(parents=True)
        try:
            file.rename(job / file.name)
        except FileNotFoundError:
            job.rmdir()
            continue
        # A crash or ambiguous API response leaves the job here for reconciliation.
        video_id = upload(service, job / file.name)
        (job / "youtube-id.txt").write_text(video_id, encoding="utf-8")
        done = root / "done"
        done.mkdir(exist_ok=True)
        job.rename(done / job.name)
        print(f"{file.name}: {video_id}", flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    commands = parser.add_subparsers(dest="cmd", required=True)
    commands.add_parser("connect").add_argument("client_json")
    commands.add_parser("watch").add_argument("queue", type=Path)
    args = parser.parse_args()
    token = args.state / "authorization.json"
    if args.cmd == "connect":
        connect(args.client_json, token)
        return
    if not token.is_file():
        parser.exit(1, "Connect this channel once before starting the worker.\n")
    service = client(token)
    while True:
        scan(args.queue, service)
        time.sleep(10)


if __name__ == "__main__":
    main()
