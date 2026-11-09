import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from content import fingerprint,prepare
from engine import Engine
from storage import Store,DEFAULTS
from text_branding import prepare_text

class BrandingTests(unittest.TestCase):
    def test_utf_encodings_remove_other_channels_preserve_source_and_email(self):
        for encoding in ('utf-8-sig','utf-16','cp1256'):
            with self.subTest(encoding=encoding),tempfile.TemporaryDirectory() as d:
                root=Path(d);source=root/'lesson_@other.txt';out=root/'out';out.mkdir()
                text='درس اصلی\nhello@example.org\nعضویت در کانال https://t.me/other\n@other\n@my_channel\nپایان'
                if encoding=='cp1256': text=text.replace('ی','ي')
                source.write_bytes(text.encode(encoding));before=source.read_bytes()
                result=prepare_text(source,{'channel':'@my_channel','tag':'@stale','text_placement':'footer'},out,1)
                body=result.read_text(encoding='utf-8-sig')
                self.assertIn(text.splitlines()[0],body);self.assertIn('hello@example.org',body)
                self.assertNotIn('other',body);self.assertNotIn('عضویت',body);self.assertNotIn('stale',body)
                self.assertEqual(body.count('@my_channel'),1);self.assertTrue(body.rstrip().endswith('@my_channel'))
                self.assertNotIn('@other',result.name);self.assertIn('@my_channel',result.name)
                self.assertEqual(source.read_bytes(),before)

    def test_reference_crossing_large_single_line_chunk_boundary(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);p=root/'large.txt';out=root/'out';out.mkdir()
            p.write_text('x'*65532+' https://t.me/other '+('y'*200000),encoding='utf-8')
            result=prepare_text(p,{'channel':'@my_channel'},out,1).read_text(encoding='utf-8-sig')
            self.assertNotIn('t.me',result);self.assertEqual(result.count('x'),65532)
            self.assertEqual(result.count('y'),200000+1) # my_channel

    def test_stat_fingerprint_does_not_read_file_bytes(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'video.mp4';path.touch()
            with patch.object(Path,'open',side_effect=AssertionError('Must not read bytes')):
                self.assertTrue(fingerprint(path))

    def test_api_outage_only_attempts_once_per_scan_and_keeps_drafts(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);folder=root/'input';folder.mkdir();store=Store(root/'data');engine=Engine(store)
            for i in range(4): (folder/f'{i}.txt').write_text('Sample text')
            engine.save_settings({**DEFAULTS,'folder':str(folder),'ai_provider':'openai'})
            engine.save_openai_key('sk-synthetic-test-only')
            import requests
            with patch('ai_client.requests.request',side_effect=requests.Timeout) as request: engine._scan()
            self.assertEqual(request.call_count,1)
            with store.db() as db: self.assertEqual(db.execute("SELECT count(*) FROM jobs WHERE status='draft'").fetchone()[0],4)
            engine.close()

    def test_sent_file_not_reimported_after_metadata_change(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);folder=root/'input';folder.mkdir();p=folder/'one.txt';p.write_text('First')
            store=Store(root/'data');engine=Engine(store);engine.save_settings({**DEFAULTS,'folder':str(folder),'ai':False})
            engine._scan()
            with store.db() as db: db.execute("UPDATE jobs SET status='sent'")
            p.write_text('Changed');engine._scan(recheck=True)
            with store.db() as db: self.assertEqual(db.execute('SELECT count(*) FROM jobs').fetchone()[0],1)
            self.assertEqual(store.job(1)['status'],'sent');engine.close()
