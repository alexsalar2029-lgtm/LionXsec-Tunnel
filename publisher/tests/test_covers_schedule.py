import base64
from datetime import datetime
import io
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch,Mock
from zoneinfo import ZoneInfo
from PIL import Image,ImageDraw
from ai_client import generate_background,AIError
from covers import make_cover,fit_lines
from engine import next_slot
from storage import DEFAULTS,Store
from mtproto_client import MTProto,TelegramError

class CoverTests(unittest.TestCase):
    def job(self): return {'id':7,'title':'Network Architecture Notes','body':'Routing, DNS and resilient service design.','digest':'topic-a','content_type':'educational'}
    def test_ten_slots_and_next_day(self):
        when=datetime(2030,1,2,8,tzinfo=ZoneInfo('Asia/Tehran')).timestamp();occupied=[]
        for i in range(11): occupied.append(next_slot(DEFAULTS,when,occupied))
        times=[datetime.fromtimestamp(t,ZoneInfo('Asia/Tehran')) for t in occupied]
        self.assertEqual([t.strftime('%H:%M') for t in times[:10]],DEFAULTS['times'])
        self.assertEqual(times[9].strftime('%H:%M'),'21:00');self.assertEqual(times[10].day,3)

    def test_already_sent_posts_count_towards_daily_limit(self):
        zone=ZoneInfo('Asia/Tehran');now=datetime(2030,1,2,16,tzinfo=zone).timestamp()
        occupied=[datetime(2030,1,2,9,i,tzinfo=zone).timestamp() for i in range(10)]
        self.assertEqual(datetime.fromtimestamp(next_slot(DEFAULTS,now,occupied),zone).day,3)

    def test_local_covers_are_distinct_and_long_titles_fit(self):
        with tempfile.TemporaryDirectory() as d:
            job=self.job();config={**DEFAULTS,'cover_mode':'local','tag':'@example_channel'}
            p,source=make_cover(job,config,d,1893571200);first=p.read_bytes()
            with Image.open(p) as image: self.assertEqual(image.size,(1440,960))
            self.assertEqual(source,'local')
            make_cover({**job,'title':'Email Deliverability Guide','digest':'topic-b'},config,d,1893571200)
            self.assertNotEqual(p.read_bytes(),first)
            draw=ImageDraw.Draw(Image.new('RGB',(1440,960)))
            lines,font=fit_lines(draw,'VeryLongUnbrokenTitle'*20,790,4)
            self.assertLessEqual(len(lines),4)
            self.assertTrue(all(draw.textlength(line,font=font)<=790 for line in lines))

    def test_openai_art_is_cached_but_date_and_signature_can_change(self):
        with tempfile.TemporaryDirectory() as d:
            data=io.BytesIO();Image.new('RGB',(80,80),'navy').save(data,'PNG')
            with patch('covers.generate_background',return_value=data.getvalue()) as generate:
                p,source=make_cover(self.job(),DEFAULTS,d,1893571200);before=p.read_bytes()
                p,_=make_cover(self.job(),{**DEFAULTS,'brand':'New Brand'},d,1893657600)
                self.assertEqual(generate.call_count,1);self.assertEqual(source,'openai')
                self.assertNotEqual(p.read_bytes(),before)

    def test_failed_image_api_uses_cached_local_art(self):
        with tempfile.TemporaryDirectory() as d,patch('covers.generate_background',side_effect=AIError('openai_quota_exceeded')) as generate:
            self.assertEqual(make_cover(self.job(),DEFAULTS,d,1893571200)[1],'local-fallback')
            self.assertEqual(make_cover(self.job(),DEFAULTS,d,1893571200)[1],'local-fallback')
            self.assertEqual(generate.call_count,1)

    def test_image_api_contract(self):
        response=Mock(status_code=200);response.json.return_value={'data':[{'b64_json':base64.b64encode(b'fixture').decode()}]}
        with patch('ai_client.requests.request',return_value=response) as request:
            self.assertEqual(generate_background('Topic','gpt-image-1','synthetic-key'),'fixture'.encode())
        self.assertTrue(request.call_args.args[1].endswith('/images/generations'))
        self.assertEqual(request.call_args.kwargs['json']['n'],1)

    def test_cover_and_file_are_linked_and_partial_failure_is_uncertain(self):
        class Client:
            def __init__(self): self.calls=[];self.fail=False
            async def get_entity(self,text): return NS(id=1)
            async def send_file(self,entity,path,**kwargs):
                self.calls.append((path,kwargs))
                if len(self.calls)==2 and self.fail: raise OSError('network')
                return NS(id=100+len(self.calls))
        for fail in (False,True):
            client=Client();client.fail=fail;proto=MTProto(None,'1234','a'*32,client=client)
            job={'channel':'@example_channel','cover_path':'cover.jpg'}
            if fail:
                with self.assertRaises(TelegramError) as caught: proto.send(job,'English caption','file.txt','document')
                self.assertTrue(caught.exception.uncertain)
            else: self.assertEqual(proto.send(job,'English caption','file.txt','document')['message_id'],102)
            self.assertEqual(client.calls[0][0],'cover.jpg')
            self.assertEqual(client.calls[1][1]['reply_to'],101)
            self.assertTrue(client.calls[1][1]['force_document'])

    def test_upgrade_requires_english_reanalysis_without_resetting_sent_history(self):
        with tempfile.TemporaryDirectory() as d:
            store=Store(d)
            with store.db() as db:
                db.execute("DELETE FROM settings WHERE key='english_ten_v1'")
                for i,status in enumerate(('draft','sent'),1):
                    db.execute("INSERT INTO jobs(id,path,digest,filename,kind,size,status,created,updated) VALUES(?,?,?,?,?,1,?,0,0)",(i,str(i),str(i),str(i)+'.txt','text',status))
            upgraded=Store(d)
            self.assertEqual(upgraded.job(1)['error'],'analysis_required')
            self.assertEqual(upgraded.job(2)['status'],'sent')

    def test_ten_slot_migration_is_not_reapplied_to_custom_settings(self):
        with tempfile.TemporaryDirectory() as d:
            store=Store(d);self.assertEqual(len(store.settings()['times']),10)
            store.update_settings({'times':['12:00']})
            self.assertEqual(Store(d).settings()['times'],['12:00'])
