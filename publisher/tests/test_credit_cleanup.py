import tempfile
import unittest
from pathlib import Path
from content import caption, fallback, prepare
from storage import DEFAULTS
from text_branding import clean_references, prepare_text

CREDIT='&#x20;Config By @MrStealer Published: 2026-12-07 • 17:00 (Asia/Tehran)'

class CreditCleanupTests(unittest.TestCase):
    def test_encoded_and_separated_credits(self):
        for value in (CREDIT, '&amp;#x20;Config By &#64;MrStealer', 'Config_By_@MrStealer', 'Config-By-@MrStealer'):
            with self.subTest(value=value):
                self.assertFalse(clean_references(value).strip())
        self.assertEqual(clean_references('Contact help@example.org'), 'Contact help@example.org')

    def test_caption_every_kind_and_custom_template(self):
        for kind in ('text','document','photo','video'):
            for custom in (False,True):
                with self.subTest(kind=kind,custom=custom):
                    settings={**DEFAULTS,'channel':'@my_channel','tag':'@stale',
                              'caption_format':'custom' if custom else 'standard',
                              'caption_template':'{title}\n{body}\n{filename}\n'+CREDIT}
                    job={'kind':kind,'filename':'Guide_Config_By_@MrStealer.txt',
                         'title':'Guide '+CREDIT,'body':'Useful instructions for setting up a local application.\n'+CREDIT}
                    result=caption(job,settings,1794312000)
                    for bad in ('MrStealer','Config By','&#','@stale','2026-12-07'):
                        self.assertNotIn(bad,result)
                    self.assertEqual(result.count('Published:'),1)
                    self.assertEqual(result.count('@my_channel'),1)
                    self.assertIn('Useful instructions',result)
                    self.assertIn('Published by LionXSec',result)

    def test_txt_copy_and_filename(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);out=root/'out';out.mkdir()
            source=root/'Guide_Config_By_@MrStealer.txt'
            source.write_text('Original resource instructions stay intact.\n'+CREDIT,encoding='utf-8')
            before=source.read_bytes()
            result=prepare_text(source,{'channel':'@my_channel'},out,1)
            self.assertEqual(result.name,'1-Guide-@my_channel.txt')
            body=result.read_text(encoding='utf-8-sig')
            self.assertNotIn('Config',body);self.assertNotIn('MrStealer',body)
            self.assertIn('Original resource instructions stay intact.',body)
            self.assertEqual(body.count('@my_channel'),1)
            self.assertEqual(before,source.read_bytes())

    def test_binary_name_cleaned_without_changing_bytes(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);out=root/'out';out.mkdir()
            source=root/'Guide_Config_By_@MrStealer.pdf';source.write_bytes(b'%PDF-example')
            result,kind=prepare({'path':str(source),'kind':'document','id':4},
                                {**DEFAULTS,'channel':'@my_channel'},out)
            self.assertEqual(result.name,'4-Guide-@my_channel.pdf')
            self.assertEqual(result.read_bytes(),source.read_bytes())

    def test_fallback_describes_contents_without_credit(self):
        result=fallback(Path('Guide_Config_By_@MrStealer.txt'),'example.org\nexample.com\n'+CREDIT)
        self.assertEqual(result['title'],'Guide')
        self.assertIn('domain-name list',result['body'])
        self.assertNotIn('MrStealer',result['body'])

    def test_credit_across_stream_boundary(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);out=root/'out';out.mkdir();source=root/'large.txt'
            source.write_text('x'*65529+' Config By @MrStealer '+('y'*200000),encoding='utf-8')
            body=prepare_text(source,{'channel':'@own'},out,1).read_text(encoding='utf-8-sig')
            self.assertNotIn('Config',body);self.assertNotIn('MrStealer',body)
            self.assertEqual(body.count('x'),65529);self.assertEqual(body.count('y'),200000)
