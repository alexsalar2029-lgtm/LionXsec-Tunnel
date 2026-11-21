"""Upload completed inbox files to one explicitly authorized YouTube channel."""
import argparse
import os
import tempfile
import time
import uuid
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
EXTENSIONS = {".mp4", ".mov", ".webm", ".mkv", ".avi", ".mpeg", ".wmv", ".m4v"}


def save_token(path, creds):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False) as tmp:
        tmp.write(creds.to_json())
    os.replace(tmp.name, path)


def client(path):
    creds = Credentials.from_authorized_user_file(str(path), SCOPES)
    if not creds.valid:
        creds.refresh(Request())
        save_token(path, creds)
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def upload(service, file):
    title = file.stem.replace("<", "").replace(">", "").strip()[:100] or "Video"
    request = service.videos().insert(
        part="snippet,status",
        body={"snippet": {"title": title}, "status": {"privacyStatus": "private"}},
        media_body=MediaFileUpload(str(file), mimetype="application/octet-stream",
                                   chunksize=8 * 1024 * 1024, resumable=True))
    result = None
    while result is None:
        _, result = request.next_chunk(num_retries=4)
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
    parser.add_argument("--state", type=Path, default=Path.home() / ".youtube-auto")
    commands = parser.add_subparsers(dest="cmd", required=True)
    commands.add_parser("connect").add_argument("client_json")
    commands.add_parser("watch").add_argument("queue", type=Path)
    args = parser.parse_args()
    token = args.state / "authorization.json"
    if args.cmd == "connect":
        flow = InstalledAppFlow.from_client_secrets_file(args.client_json, SCOPES)
        creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")
        if not creds.refresh_token:
            parser.exit(1, "Google did not grant offline access.\n")
        save_token(token, creds)
        return
    if not token.is_file():
        parser.exit(1, "Connect this channel once before starting the worker.\n")
    service = client(token)
    while True:
        scan(args.queue, service)
        time.sleep(10)


if __name__ == "__main__":
    main()
