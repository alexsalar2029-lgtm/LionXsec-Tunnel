"""Exercise the restored Telegram, desktop and Windows distribution entry points."""
import asyncio
import json
import os
import tempfile
import threading
import time
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from zipfile import ZipFile

from googleapiclient.discovery import build
from googleapiclient.http import HttpMockSequence
import av
from PIL import Image, ImageDraw, ImageFont

import desktop_oauth
import make_uploader_package as packaging
import telegram_downloader as telegram
import uploader
import yt_uploader as desktop


def write_recording(path, *, blank=False):
    """Encode a real screen recording with an uninformative name and container title."""
    with av.open(str(path), "w") as output:
        output.metadata["title"] = "Screen Recording 2026-09-28 172608"
        stream = output.add_stream("mpeg4", rate=4)
        stream.width, stream.height, stream.pix_fmt = 880, 460, "yuv420p"
        for index in range(24):
            image = Image.new("RGB", (880, 460), (23, 26, 31))
            if not blank:
                draw = ImageDraw.Draw(image)
                detail = "Mixing warm and cool colors" if index < 12 else "Layering transparent washes"
                for y, text, size in ((10, "Download history   |   Google Chrome", 18),
                                      (145, "Watercolor basics", 56), (251, detail, 31),
                                      (342, "Ask anything", 18)):
                    draw.text((100, y), text, fill="white", font=ImageFont.load_default(size=size))
            for packet in stream.encode(av.VideoFrame.from_image(image)):
                output.mux(packet)
        for packet in stream.encode():
            output.mux(packet)


class DesktopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with tempfile.TemporaryDirectory() as folder:
            clip = Path(folder) / "sample.mp4"
            write_recording(clip)
            cls.media = clip.read_bytes()

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
        self.filename = "tg_demo_0000007.mp4"
        message = SimpleNamespace(id=7, raw_text="A morning walk by the lake", document=None, video=True, file=SimpleNamespace(
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
        description = body["snippet"]["description"]
        self.assertTrue(description.startswith("Description\n"))
        self.assertIn("https://t.me/link_Lion", description)
        self.assertIn("https://t.me/LionXSec", description)
        self.assertNotIn("https://t.me/demo", description)
        self.assertEqual(body["snippet"]["tags"], ["Demo"])
        self.assertEqual(body["status"], {"privacyStatus": "unlisted", "selfDeclaredMadeForKids": True})
        self.assertIn("videoId=fixture-id", requests[2][0])
        self.assertEqual((self.videos / self.filename).read_bytes(), self.media)
        self.assertEqual(self.log.read_text().splitlines(), [self.filename])
        self.assertEqual(self.tg_log.read_text().splitlines(), ["demo:7"])
        self.telegram_client.download_media.assert_awaited_once()
        self.connect.assert_not_called()

    def test_public_telegram_upload_preserves_caption_and_reports_private_result(self):
        self.message.raw_text = "Watercolor basics\nتوضیح نویسنده دربارهٔ آبرنگ"
        self.responses[1] = ({"status": "200"}, b'{"id":"fixture-id","status":{"privacyStatus":"private"}}')
        cfg = json.loads(self.config.read_text())
        del cfg["visibility"]  # Use the clean installation default.
        self.config.write_text(json.dumps(cfg))
        with patch.object(desktop, "log_message") as log:
            self.assertEqual(desktop.main(["--tg-download", "--tg-upload"]), 0)
        body = json.loads(self.http.request_sequence[0][2])
        self.assertEqual(body["status"]["privacyStatus"], "public")
        self.assertEqual(body["snippet"]["title"], self.message.raw_text.splitlines()[0])
        self.assertTrue(body["snippet"]["description"].startswith(self.message.raw_text + "\n"))
        self.assertIn("به کانال تلگرام ما بپیوندید\nhttps://t.me/link_Lion", body["snippet"]["description"])
        self.assertTrue(any("YouTube returned private" in str(call) for call in log.call_args_list))
        self.assertEqual(self.log.read_text().splitlines(), [self.filename])

    def test_public_recording_generates_title_description_and_cover(self):
        video = self.videos / "Screen Recording 2026-09-28 172608.mp4"
        write_recording(video)
        cfg = json.loads(self.config.read_text())
        # Existing v5.0 settings had these defaults, even after upgrading the ZIP.
        cfg.update(auto_cover=True, use_ai_ocr=False, telegram_channel="", telegram_owner="")
        self.config.write_text(json.dumps(cfg))
        self.assertEqual(desktop.main(["--auto", "--visibility", "PUBLIC"]), 0)
        body = json.loads(self.http.request_sequence[0][2])
        self.assertEqual(body["status"]["privacyStatus"], "public")
        self.assertEqual(body["snippet"]["title"], "Watercolor basics")
        self.assertIn("Watercolor basics", body["snippet"]["tags"])
        desc = body["snippet"]["description"]
        self.assertIn("#Watercolor_basics", desc)
        for detail in ("Mixing warm and cool colors", "Layering transparent washes", "به کانال تلگرام ما بپیوندید",
                       "https://t.me/link_Lion", "https://t.me/LionXSec"):
            self.assertIn(detail, desc)
        for generic in ("Screen Recording", "MP4", "Download history", "Ask anything"):
            self.assertNotIn(generic, desc)
        self.assertIn("videoId=fixture-id", self.http.request_sequence[2][0])
        with Image.open(self.root / "covers" / (video.stem + ".jpg")) as cover:
            self.assertEqual(cover.size, (1280, 720))
            self.assertTrue(cover.getexif()[305].startswith("YouTubeUploader/"))

    def test_held_recording_does_not_spend_public_batch_slot(self):
        video = self.videos / "Screen Recording 2026-09-28 172608.mp4"
        write_recording(video, blank=True)
        # Old sidecars contained the same placeholder as the YouTube Studio report.
        video.with_suffix(".json").write_text(json.dumps({
            "title": video.stem, "description": video.stem + "\nمشخصات ویدیو: MP4"}))
        next_video = self.videos / "tg_demo_0000007.mp4"
        next_video.write_bytes(self.media)
        next_video.with_suffix(".vtt").write_text("WEBVTT\n\n00:00:00.000 --> 00:00:04.000\nPlanting spring bulbs\n")
        self.assertEqual(desktop.main(["--auto", "--count", "1", "--visibility", "PUBLIC"]), 0)
        self.assertEqual(self.log.read_text().splitlines(), [next_video.name])
        self.assertEqual(json.loads(self.http.request_sequence[0][2])["snippet"]["title"], "Watercolor basics")
        self.assertEqual(list((self.root / ".uploading").iterdir()), [])

    def test_music_title_and_unmatched_artwork_cannot_name_a_recording(self):
        video = self.videos / "Rec 0029~2.mp4"
        write_recording(video, blank=True)
        video.with_suffix(".vtt").write_text("WEBVTT\n\n00:00:00.000 --> 00:00:04.000\nA song about tulips\n")
        video.with_suffix(".json").write_text(json.dumps({"source": "telegram_caption", "title": "A song about tulips",
                                                         "description": "A song about tulips"}))
        cfg = desktop.load_config(self.config)
        with self.assertRaises(uploader.MetadataRequired):
            desktop.generate_metadata(video, cfg)
        self.assertTrue(uploader.opaque_title(video.stem))
        self.assertTrue(uploader.opaque_title("Rec 0082"))

    def test_thumbnail_failure_reports_google_reason_and_keeps_upload_record(self):
        video = self.videos / "Rec 0082.mp4"
        video.write_bytes(self.media)
        Image.new("RGB", (1280, 720), "blue").save(video.with_suffix(".webp"))
        self.responses[2] = ({"status": "403"}, json.dumps({"error": {"code": 403, "message": "Thumbnail denied",
                            "errors": [{"reason": "forbidden", "message": "Thumbnail denied"}]}}).encode())
        cfg = desktop.load_config(self.config)
        notes = []
        engine = desktop.UploaderEngine(cfg, log_callback=lambda msg, level: notes.append(msg))
        result = engine.run()
        self.assertEqual((result["uploaded"], result["thumbnail_failed"]), (1, 1))
        self.assertTrue(any("HTTP 403 forbidden" in line for line in notes))
        self.assertIn(video.name, desktop.load_uploaded_log())
        self.assertEqual(engine.run()["uploaded"], 0)

    def test_account_checking_promotion_is_held_before_upload(self):
        video = self.videos / "Rec 0082.mp4"
        video.write_bytes(self.media)
        desktop.save_companion_metadata(video, "SilverBullet config", "Free config with 2 hits", [])
        self.assertEqual(desktop.UploaderEngine(desktop.load_config(self.config)).run()["held"], 1)
        self.assertFalse(self.http.request_sequence)

    def test_telegram_links_fit_description_limit_and_respect_saved_choices(self):
        cfg = desktop.load_config(self.config)
        cfg.update(telegram_owner="", telegram_channel="@demo_channel", use_ai_ocr=False)
        desktop.save_config(cfg, self.config)
        cfg = desktop.load_config(self.config)
        self.assertFalse(cfg["use_ai_ocr"])
        self.assertEqual(cfg["telegram_owner"], "")
        video = self.videos / "presentation.mp4"
        video.write_bytes(self.media)
        url = "https://t.me/demo_channel"
        for description in ("توضیحات\n" + url, "گل " * 1200 + url):
            desktop.save_companion_metadata(video, "نمایش گل‌ها", description, [])
            text = desktop.generate_metadata(video, cfg)[1]
            self.assertEqual(text.count(url), 1)
            self.assertNotIn("LionXSec", text)
            self.assertLessEqual(len(text.encode()), 5000)

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
        (self.videos / self.filename).with_suffix(".txt").write_text("Birds at the lake\nA short field recording.")
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
    def test_native_qr_button_displays_code_and_confirms_connection(self):
        import yt_uploader_gui as gui
        self.telegram_client.is_user_authorized.side_effect = [False, True]
        approved = threading.Event()
        async def wait():
            while not approved.is_set():
                await asyncio.sleep(0.01)
        self.telegram_client.qr_login = AsyncMock(return_value=SimpleNamespace(
            url="tg://login?token=fixture", wait=wait))
        app = gui.UploaderApp()
        self.addCleanup(app.destroy)
        self.addCleanup(approved.set)
        app.show_page("telegram")
        button = next(b for b in app.tg_action_buttons if b.cget("text") == "Login with QR")
        button.invoke()
        deadline = time.monotonic() + 5
        while getattr(app, "_qr_window", None) is None and time.monotonic() < deadline:
            app.update()
            time.sleep(0.01)
        self.assertIsNotNone(app._qr_image_label.cget("image"))
        approved.set()
        while app._mode is not None and time.monotonic() < deadline:
            app.update()
            time.sleep(0.01)
        self.assertIsNone(app._qr_window)
        self.assertEqual(app.tg_status_lbl.cget("text"), "Telegram connected.")

    @unittest.skipUnless(os.name == "nt" or os.environ.get("DISPLAY"), "requires a desktop display")
    def test_native_gui_download_actions(self):
        import yt_uploader_gui as gui
        old_grant = uploader.DEFAULT_TOKEN.read_bytes()
        uploader.DEFAULT_TOKEN.unlink()
        self.enter_patch(desktop, "APP_DIR", self.root)
        chosen = self.root / "Downloads" / "app.json"
        picker = self.enter_patch(gui, "pick_client_file", MagicMock(return_value=str(chosen)))
        self.connect.side_effect = lambda client_file, token: token.write_bytes(old_grant)
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
        picker.assert_called_once_with(parent=app)
        self.connect.assert_called_once_with(chosen, uploader.DEFAULT_TOKEN)

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


class ConnectionSetupTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.token = self.root / "authorization.json"
        for module, name, value in (
            (desktop, "APP_DIR", self.root),
            (desktop, "CONFIG_FILE", self.root / "yt_config.json"),
            (uploader, "DEFAULT_TOKEN", self.token),
        ):
            change = patch.object(module, name, value)
            change.start()
            self.addCleanup(change.stop)
        self.registration = {"installed": {
            "client_id": "fixture.apps.googleusercontent.com", "client_secret": "fixture-desktop",
            "extra_private_field": "must-not-be-in-package",
        }}
        self.creds = uploader.Credentials(
            token="test-access", refresh_token="test-refresh", client_id=self.registration["installed"]["client_id"],
            client_secret="fixture-desktop", token_uri="https://oauth2.googleapis.com/token",
            scopes=uploader.SCOPES, expiry=datetime(2099, 1, 1),
        )

    def test_google_download_name_and_saved_grant(self):
        path = self.root / "client_secret_fixture.apps.googleusercontent.com.json"
        path.write_text(json.dumps(self.registration))
        with patch.object(uploader.InstalledAppFlow, "run_local_server", return_value=self.creds) as browser:
            self.assertEqual(desktop.main(["--setup"]), 0)
            path.unlink()
            self.assertEqual(desktop.main(["--setup"]), 0)
        browser.assert_called_once()
        self.assertTrue(self.token.is_file())

    def test_wrong_file_cancel_and_unattended_run_preserve_queue(self):
        path = self.root / "user.json"
        path.write_text('{"type":"authorized_user","refresh_token":"fixture"}')
        engine = desktop.UploaderEngine(desktop.load_config())
        video = self.root / "clip.mp4"
        video.write_bytes(b"fixture")
        with patch.object(uploader.InstalledAppFlow, "run_local_server") as browser:
            with self.assertRaisesRegex(ValueError, "Desktop app"):
                engine.setup_login(explicit_client=path)
            with self.assertRaisesRegex(uploader.ConnectionRequired, "cancelled"):
                engine.setup_login(choose_client=lambda: "")
            with self.assertRaises(uploader.ConnectionRequired):
                engine.run([video])
            browser.assert_not_called()
        self.assertTrue(video.is_file())
        self.assertFalse(self.token.exists())

    def test_customer_zip_provisions_only_desktop_client(self):
        original = self.root / "application.json"
        original.write_text(json.dumps(self.registration))
        output = self.root / "customer"
        packaging.build(output, archive=True, desktop_client=original)
        with ZipFile(output.with_suffix(".zip")) as z:
            payload = json.loads(z.read(desktop_oauth.PUBLISHER_CLIENT))
            self.assertEqual(set(payload), {"installed"})
            self.assertNotIn("extra_private_field", payload["installed"])
            self.assertNotIn("authorization.json", z.namelist())
        with patch.object(desktop, "APP_DIR", output), patch.object(
            uploader.InstalledAppFlow, "run_local_server", return_value=self.creds
        ) as browser:
            picker = MagicMock(side_effect=AssertionError("Customer should not need a JSON picker"))
            desktop.UploaderEngine(desktop.load_config()).setup_login(choose_client=picker)
            browser.assert_called_once()
            picker.assert_not_called()

    def test_ambiguous_files_require_selection(self):
        for n in ("one", "two"):
            (self.root / f"client_secret_{n}.json").write_text(json.dumps(self.registration))
        with self.assertRaisesRegex(FileNotFoundError, "Several"):
            desktop_oauth.find_client(self.root, "absent.json")


if __name__ == "__main__":
    unittest.main()
