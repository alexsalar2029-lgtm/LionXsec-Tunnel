import tempfile
import unittest
from pathlib import Path
from content import caption, detect_kind, source_text, describe_resource
from storage import DEFAULTS, Store

class SignatureDetectionTests(unittest.TestCase):
    def test_signature_has_date_site_without_clock(self):
        output=caption({'title':'Example','body':'Useful content','filename':'example.txt'},DEFAULTS,1794331800)
        self.assertIn('https://lionxsec.sbs/',output)
        self.assertIn('Published: 2026-11-10',output)
        self.assertNotIn('Asia/Tehran',output)
        self.assertNotRegex(output,r'\d\d:\d\d')

    def test_archive_signature_beats_txt_extension(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'archive.txt';path.write_bytes(b'PK\x03\x04'+b'\0'*30)
            self.assertEqual(detect_kind(path),'document')

    def test_gif_signature_beats_document_extension(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'picture.bin';path.write_bytes(b'GIF89a'+b'\0'*30)
            self.assertEqual(detect_kind(path),'photo')

    def test_structured_text_can_be_analyzed_without_rewriting(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'settings.json';path.write_text('{"name":"demo","enabled":true}')
            self.assertEqual(detect_kind(path),'document')
            text=source_text(path,'document')
            self.assertIn('JSON document containing 2',describe_resource(path,text))
            self.assertEqual(path.read_text(),text)

    def test_csv_and_plain_prose_are_distinct(self):
        self.assertIn('3 columns',describe_resource('table.csv','name,age,city\nAlex,30,London'))
        prose='This is a guide for configuring a local application.'
        self.assertEqual(describe_resource('guide.txt',prose),prose)
        self.assertNotIn('configuration resource',describe_resource('note.txt','A sentence { with a colon: and five more words.'))

    def test_website_migration_only_runs_once(self):
        with tempfile.TemporaryDirectory() as d:
            store=Store(Path(d));self.assertEqual(store.settings()['website'],'https://lionxsec.sbs/')
            with store.db() as db:
                db.execute("UPDATE settings SET value=? WHERE key='website'",('"https://example.org/"',))
            reopened=Store(Path(d));self.assertEqual(reopened.settings()['website'],'https://example.org/')
