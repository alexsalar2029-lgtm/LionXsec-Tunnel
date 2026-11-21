"""Exercise the restored Telegram, desktop and Windows distribution entry points."""
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from zipfile import ZipFile

from googleapiclient.discovery import build
from googleapiclient.http import HttpMockSequence
from PIL import Image

import make_uploader_package as packaging
import telegram_downloader as telegram
import uploader
import yt_uploader as desktop


class DesktopTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.videos = self.root / "videos"
        self.videos.mkdir()
        self.config = self.root / "yt_config.json"
        self.log = self.root / "uploaded_videos.log"
        self.tg_log = self.root / "telegram_downloaded.log"
        self.enter_patch(desktop, "CONFIG_FILE", self.config)
        self.enter_patch(desktop, "LOG_FILE", self.log)
        self.enter_patch(telegram, "TG_DL_LOG", self.tg_log)
        self.enter_patch(uploader, "DEFAULT_TOKEN", self.root / "authorization.json")
        self.connect = self.enter_patch(uploader, "connect", MagicMock(side_effect=AssertionError("unexpected consent")))
        self.config.write_text(json.dumps({
            "video_folder": "videos", "tg_api_id": "12345", "tg_api_hash": "test-hash",
            "tg_channel": "@demo", "visibility": "UNLISTED", "made_for_kids": True,
            "tg_auto_upload": True, "upload_delay_seconds": 0, "auto_cover": False,
        }), encoding="utf-8")
        # A locally authorized channel. Both Google and Telegram transports below are offline.
        uploader.DEFAULT_TOKEN.write_text(json.dumps({
            "token": "test-access", "refresh_token": "test-refresh", "client_id": "test-client",
            "client_secret": "test-secret", "expiry": "2099-01-01T00:00:00Z",
        }))
        self.media = b"video fixture"
        self.filename = "tg_demo_0000007.mp4"
        message = SimpleNamespace(id=7, document=None, video=True, file=SimpleNamespace(
            size=len(self.media), name="clip.mp4", ext=".mp4"
        ))
        self.message = message
        self.telegram_client = MagicMock()
        self.telegram_client.connect = AsyncMock()
        self.telegram_client.disconnect = AsyncMock()
        self.telegram_client.is_user_authorized = AsyncMock(return_value=True)
        self.telegram_client.get_entity = AsyncMock(return_value=SimpleNamespace(id=10, title="Fixture"))

        async def messages(entity, limit=None):
            yield message

        async def download(msg, file, progress_callback):
            file.write(self.media)
            progress_callback(len(self.media), len(self.media))

        self.telegram_client.iter_messages = messages
        self.telegram_client.download_media = AsyncMock(side_effect=download)
        self.enter_patch(telegram.TelegramDownloader, "_client", lambda dl: self.telegram_client)
        self.responses = [
            ({"status": "200", "location": "https://example.invalid/session"}, b""),
            ({"status": "200"}, b'{"id":"fixture-id"}'),
            ({"status": "200"}, b'{"items":[]}'),
        ]
        self.http = HttpMockSequence(self.responses)
        self.http.close = MagicMock()
        self.enter_patch(uploader, "build", lambda *a, **kw: build(
            "youtube", "v3", http=self.http, static_discovery=True, cache_discovery=False
        ))

    def enter_patch(self, obj, name, value):
        patcher = patch.object(obj, name, value)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def test_cli_download_upload_and_daily_batch_share_history(self):
        sidecar = self.videos / "tg_demo_0000007.json"
        sidecar.write_text(json.dumps({"title": "Manual title", "description": "Description", "tags": ["Demo"]}))
        cover = self.root / "covers" / "tg_demo_0000007.jpg"
        cover.parent.mkdir()
        Image.new("RGB", (16, 9)).save(cover)
        self.assertEqual(desktop.main(["--setup"]), 0)
        for args in (["--tg-download", "--tg-upload"], ["--tg-download", "--tg-upload"], ["--auto"]):
            self.assertEqual(desktop.main(args), 0)
        requests = self.http.request_sequence
        self.assertEqual([r[1] for r in requests], ["POST", "PUT", "POST"])
        body = json.loads(requests[0][2])
        self.assertEqual(body["snippet"]["title"], "Manual title")
        self.assertEqual(body["snippet"]["description"], "Description")
        self.assertEqual(body["snippet"]["tags"], ["Demo"])
        self.assertEqual(body["status"], {"privacyStatus": "unlisted", "selfDeclaredMadeForKids": True})
        self.assertIn("videoId=fixture-id", requests[2][0])
        self.assertEqual((self.videos / self.filename).read_bytes(), self.media)
        self.assertEqual(self.log.read_text().splitlines(), [self.filename])
        self.assertEqual(self.tg_log.read_text().splitlines(), ["demo:7"])
        self.telegram_client.download_media.assert_awaited_once()
        self.connect.assert_not_called()

    def test_incomplete_download_can_retry_and_never_enters_upload_queue(self):
        async def incomplete(msg, file, progress_callback):
            file.write(b"short")
        self.telegram_client.download_media.side_effect = incomplete
        self.assertEqual(desktop.main(["--tg-download", "--tg-upload"]), 1)
        self.assertEqual(list(self.videos.iterdir()), [])
        self.assertFalse(self.tg_log.exists())
        self.assertEqual(self.http.request_sequence, [])
        self.telegram_client.download_media.side_effect = lambda msg, file, progress_callback: file.write(self.media)
        self.assertEqual(desktop.main(["--tg-download"]), 0)
        self.assertTrue((self.videos / self.filename).is_file())

    def test_uncertain_upload_is_held_for_reconciliation(self):
        (self.videos / self.filename).write_bytes(self.media)
        with patch.object(uploader, "upload", side_effect=TimeoutError) as send:
            self.assertEqual(desktop.main(["--auto"]), 1)
            desktop.main(["--auto"])
            send.assert_called_once()
        self.assertFalse(self.log.exists())
        claims = list((self.root / ".uploading").iterdir())
        self.assertEqual([p.read_text() for p in claims], [self.filename])

    def test_expired_saved_grant_is_refreshed_and_reused(self):
        token = uploader.DEFAULT_TOKEN
        old = json.loads(token.read_text())
        old["expiry"] = "2000-01-01T00:00:00Z"
        token.write_text(json.dumps(old))
        response = SimpleNamespace(status=200, headers={}, data=json.dumps({
            "access_token": "renewed-fixture", "expires_in": 3600, "token_type": "Bearer",
        }).encode())
        refresh_request = MagicMock(return_value=response)
        with patch.object(uploader, "Request", return_value=refresh_request):
            self.assertEqual(desktop.main(["--setup"]), 0)
            self.assertEqual(desktop.main(["--setup"]), 0)
        refresh_request.assert_called_once()
        self.assertEqual(json.loads(token.read_text())["token"], "renewed-fixture")
        self.assertEqual(json.loads(token.read_text())["refresh_token"], old["refresh_token"])
        self.connect.assert_not_called()

    @unittest.skipUnless(os.name == "nt" or os.environ.get("DISPLAY"), "requires a desktop display")
    def test_native_gui_download_actions(self):
        import yt_uploader_gui as gui
        app = gui.UploaderApp()
        self.addCleanup(app.destroy)
        for page in ("telegram", "settings", "schedule", "logs", "help", "queue"):
            app.show_page(page)
            app.update_idletasks()
            self.assertTrue(app.pages[page].winfo_ismapped())
        app.show_page("telegram")
        # Use both actions in the same window, just as an existing installation does.
        self.responses.extend(self.responses.copy())
        filenames = []
        for message_id, label in [(7, "⬇▶ Download + Upload"), (8, "⬇ Download")]:
            self.message.id = message_id
            filename = f"tg_demo_{message_id:07d}.mp4"
            filenames.append(filename)
            Image.new("RGB", (16, 9)).save((self.videos / filename).with_suffix(".jpg"))
            if message_id == 7:
                app.tg_widgets["tg_auto_upload"].deselect()
            else:
                app.tg_widgets["tg_auto_upload"].select()
            download = next(b for b in app.tg_action_buttons if b.cget("text") == label)
            download.invoke()
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                app.update()
                if self.log.exists() and filename in desktop.load_uploaded_log() and app._mode is None:
                    break
                time.sleep(0.01)
            self.assertEqual(self.log.read_text().splitlines(), filenames)
            self.assertTrue(app.rows[filename].status_lbl.cget("text").endswith("Uploaded"))
        self.assertEqual([r[1] for r in self.http.request_sequence], ["POST", "PUT", "POST"] * 2)
        self.connect.assert_not_called()

    def test_zip_overlays_existing_installation(self):
        archive_dir = self.root / "dist"
        packaging.build(archive_dir, archive=True)
        # Existing settings, media and account files must never be overwritten by an upgrade.
        original = {
            "yt_config.json": b'{"video_folder":"videos"}', "tg_session.session": b"local fixture",
            "uploaded_videos.log": b"old.mp4\n", "telegram_downloaded.log": b"demo:1\n",
            "covers/old.jpg": b"cover", "videos/old.mp4": b"video",
            "METADATA_LIVE_REPORT.txt": b"local report", "chrome_session/Local State": b"private",
        }
        previous = self.root / "previous"
        for name, data in original.items():
            path = previous / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        with ZipFile(archive_dir.with_suffix(".zip")) as z:
            self.assertIsNone(z.testzip())
            names = set(z.namelist())
            self.assertEqual({n for n in names if n.endswith(".bat")}, {
                "auto_run.bat", "run_gui.bat", "start_youtube_uploader.bat", "login_youtube.bat", "install_telegram.bat"
            })
            self.assertFalse(names.intersection(original))
            z.extractall(previous)
        for name, data in original.items():
            self.assertEqual((previous / name).read_bytes(), data)
        self.assertEqual(desktop.load_config(previous / "yt_config.json")["video_folder"], str(previous / "videos"))


if __name__ == "__main__":
    unittest.main()
