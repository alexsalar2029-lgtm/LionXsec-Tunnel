import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from content import companion_files,companion_owner,fingerprint,detect_kind
from text_branding import prepare_text
from storage import DEFAULTS
from mtproto_client import MTProto,TelegramError

class ReleaseTests(unittest.TestCase):
    def test_replacement_preserves_sentence_and_original(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'lesson.txt';out=Path(d)/'out';out.mkdir()
            original='Useful channel instructions: @oldname and https://t.me/oldname\r\nmail@example.com &amp; data'
            p.write_bytes(original.encode())
            result=prepare_text(p,DEFAULTS,out,1).read_bytes().decode('utf-8-sig')
            expected=original.replace('@oldname','https://t.me/link_Lion').replace('https://t.me/oldname','https://t.me/link_Lion')
            self.assertTrue(result.startswith(expected));self.assertEqual(p.read_bytes(),original.encode())

    def test_pairing_and_change_detection(self):
        with tempfile.TemporaryDirectory() as d:
            v=Path(d)/'lesson.mp4';v.write_bytes(b'0000ftypmp42')
            a=Path(d)/'lesson.zip';a.write_bytes(b'PK\x03\x04data')
            other=Path(d)/'other.txt';other.write_text('other')
            self.assertEqual(companion_files(v),[a]);self.assertEqual(companion_owner(a),v)
            self.assertIsNone(companion_owner(other));before=fingerprint(v)
            a.write_bytes(b'PK\x03\x04changed');self.assertNotEqual(fingerprint(v),before)
            (Path(d)/'lesson.mov').write_bytes(b'0000ftyp')
            self.assertEqual(companion_files(v),[])
            self.assertIsNone(companion_owner(a))

    def test_avi_signature(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'unknown.bin';p.write_bytes(b'RIFF0000AVI ')
            self.assertEqual(detect_kind(p),'video')

    def test_video_and_reply_delivery_and_partial_failure(self):
        async def run():
            client=SimpleNamespace(send_file=AsyncMock(return_value=SimpleNamespace(id=123)))
            obj=SimpleNamespace(_client=client,_resolve=AsyncMock(return_value='channel'))
            message=await MTProto._deliver(obj,'channel','caption','video.mp4','video',companions=['lesson.zip'])
            self.assertEqual(message.id,123)
            self.assertTrue(client.send_file.call_args_list[0].kwargs['supports_streaming'])
            self.assertEqual(client.send_file.call_args_list[1].kwargs['reply_to'],123)
            client.send_file.side_effect=[SimpleNamespace(id=123),RuntimeError('failed')]
            with self.assertRaises(TelegramError) as error:
                await MTProto._deliver(obj,'channel','caption','video.mp4','video',companions=['lesson.zip'])
            self.assertTrue(error.exception.uncertain)
        asyncio.run(run())
