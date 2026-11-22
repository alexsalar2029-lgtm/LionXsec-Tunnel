import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import uploader


class WorkerTests(unittest.TestCase):
    def test_renew_without_browser(self):
        with tempfile.TemporaryDirectory() as d, patch.object(uploader.Credentials, "from_authorized_user_file") as load, patch.object(uploader, "build") as build:
            token = Path(d) / "authorization.json"
            creds = load.return_value
            creds.valid = False
            creds.to_json.return_value = '{"fixture":true}'
            self.assertIs(uploader.client(token), build.return_value)
            creds.refresh.assert_called_once()
            self.assertEqual(token.read_text(), creds.to_json.return_value)

    def test_queue(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            service = MagicMock()
            uploader.scan(root, service)
            (root / "inbox/test.mp4").write_bytes(b"fixture")
            (root / "inbox/test.txt").write_text("Walk by the lake\nA morning walk with the local club.")
            insert = service.videos.return_value.insert
            insert.return_value.next_chunk.side_effect = [(None, None), (None, {"id": "fixture-id"})]
            uploader.scan(root, service)
            uploader.scan(root, service)
            insert.assert_called_once()
            body = insert.call_args.kwargs["body"]
            self.assertEqual(body["status"]["privacyStatus"], "public")
            self.assertTrue(body["snippet"]["description"].startswith("A morning walk with the local club.\n"))
            self.assertIn("https://t.me/link_Lion", body["snippet"]["description"])
            self.assertIn("https://t.me/LionXSec", body["snippet"]["description"])
            self.assertEqual(next(root.glob("done/*/youtube-id.txt")).read_text(), "fixture-id")
