"""Rescanning, protected API settings and OpenAI contract regression tests."""
from datetime import datetime
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

from PIL import Image
import requests

from ai_client import AIError,check_openai,openai_analysis
from app import API
from content import analyze,caption,prepare,sensitive_reason
from engine import Engine
from storage import DEFAULTS,Store


KEY='sk-test-fixture-not-a-real-api-key'


def response_body(**overrides):
    result={'decision':'ready','title':'کارگاه عکاسی','body':'آموزش نور طبیعی',
            'reason':'','content_type':'educational','source_date':'۱۴۰۵/۰۸/۱۶',**overrides}
    return {'status':'completed','output':[{'type':'message','content':[
        {'type':'output_text','text':json.dumps(result,ensure_ascii=False)}]}]}


class RescanTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.folder=self.root/'input';self.folder.mkdir();self.store=Store(self.root/'data')
        self.engine=Engine(self.store)
        self.engine.save_settings({**DEFAULTS,'folder':str(self.folder),'ai':False,'auto_watch':False})
        self.api=API(self.store,self.engine)

    def tearDown(self):
        self.engine.close();self.temp.cleanup()

    def source(self,name='lesson.txt',text='کارگاه عکاسی\nآموزش نور طبیعی در ۱۴۰۵/۰۸/۱۶'):
        path=self.folder/name;path.write_text(text,encoding='utf-8');return path

    def jobs(self):
        with self.store.db() as db: return [dict(row) for row in db.execute('SELECT * FROM jobs ORDER BY id')]

    def wait_scan(self):
        until=time.monotonic()+5
        while self.engine.runtime['scanning'] and time.monotonic()<until: time.sleep(.01)
        self.assertFalse(self.engine.runtime['scanning'])

    def test_manual_scan_reanalyzes_unchanged_file_without_adding_duplicate(self):
        self.source();self.engine._scan();self.engine.edit(1,'Manual title','Manual text')
        self.assertTrue(self.api.scan()['ok']);self.wait_scan()
        self.assertEqual(len(self.jobs()),1)
        self.assertEqual(self.store.job(1)['title'],'کارگاه عکاسی')
        self.assertFalse(self.store.settings()['enabled'])

    def test_background_scan_preserves_manual_caption_and_does_not_repeat_api_calls(self):
        self.source();self.engine._scan();self.engine.edit(1,'Manual title','Manual text')
        analyzer=Mock(side_effect=AssertionError('Existing file must not be sent to AI again'))
        self.engine.analyzer=analyzer;self.engine._scan()
        analyzer.assert_not_called();self.assertEqual(self.store.job(1)['title'],'Manual title')

    def test_blocked_txt_can_be_rechecked_after_source_correction(self):
        path=self.source(text='password: synthetic-example-only');self.engine._scan()
        self.assertEqual(self.store.job(1)['status'],'blocked')
        path.write_text('درس اصلاح‌شده\nمتن عادی آموزشی',encoding='utf-8')
        self.assertTrue(self.api.reanalyze(1)['ok']);self.wait_scan()
        self.assertEqual(len(self.jobs()),1)
        self.assertEqual(self.store.job(1)['status'],'draft')
        self.assertEqual(self.store.job(1)['title'],'درس اصلاح‌شده')

    def test_rechecking_sensitive_txt_keeps_it_blocked(self):
        self.source(text='password: synthetic-example-only');self.engine._scan()
        self.engine._scan(recheck=True)
        self.assertEqual(self.store.job(1)['status'],'blocked')
        self.assertEqual(self.store.job(1)['error'],'sensitive_credentials')

    def test_clear_preserves_sources_and_sent_uncertain_inflight_history(self):
        paths=[self.source(f'lesson-{i}.txt',f'Lesson {i}\nOrdinary notes') for i in range(6)]
        originals=[p.read_bytes() for p in paths];self.engine._scan()
        with self.store.db() as db:
            for identifier,status in enumerate(['draft','blocked','sent','uncertain','sending','preparing'],1):
                db.execute('UPDATE jobs SET status=? WHERE id=?',(status,identifier))
        self.store.update_settings({'enabled':True})
        result=self.api.clear_analysis()
        self.assertEqual(result,{'ok':True,'value':{'cleared':2}})
        self.assertFalse(self.store.settings()['enabled'])
        self.assertEqual([j['status'] for j in self.jobs()][2:],['sent','uncertain','sending','preparing'])
        self.assertEqual([p.read_bytes() for p in paths],originals)
        self.assertEqual(self.store.job(1)['error'],'analysis_required')
        self.assertFalse(self.api.edit(1,'Override','Body')['ok'])
        self.assertFalse(self.api.prepare_copy(1)['ok'])

    def test_clear_sensitive_txt_requires_fresh_analysis_and_does_not_bypass_checks(self):
        self.source(text='password: synthetic-example-only');self.engine._scan()
        self.engine.clear_analysis(1)
        self.assertEqual(self.store.job(1)['title'],'')
        self.assertFalse(self.api.edit(1,'Override','Body')['ok'])
        self.engine._scan();self.assertEqual(self.store.job(1)['status'],'blocked')

    def test_clear_removes_only_generated_files_for_selected_job(self):
        path=self.source();self.engine._scan();job=self.store.job(1)
        output,_=prepare(job,self.store.settings(),self.store.directory/'prepared')
        other=self.store.directory/'prepared'/'10-preserve.txt';other.write_text('Keep')
        self.engine.clear_analysis(1)
        self.assertTrue(path.exists());self.assertFalse(output.exists());self.assertTrue(other.exists())

    def test_clear_while_scanning_is_rejected(self):
        self.engine.scan_lock.acquire()
        try: self.assertEqual(self.api.clear_analysis()['error'],'scan_busy')
        finally: self.engine.scan_lock.release()

    def test_changed_format_and_size_are_detected_on_same_job(self):
        path=self.source();self.engine._scan()
        Image.new('RGB',(24,20),'blue').save(path,format='PNG')
        self.engine._scan(recheck=True);job=self.store.job(1)
        self.assertEqual(len(self.jobs()),1);self.assertEqual(job['kind'],'photo')
        self.assertEqual(job['size'],path.stat().st_size)
        self.assertTrue((self.store.directory/'thumbs'/'1.jpg').exists())

    def test_renamed_unsent_file_reuses_record_and_updates_filename(self):
        path=self.source();self.engine._scan();path.rename(self.folder/'renamed.md')
        self.engine._scan(recheck=True)
        self.assertEqual(len(self.jobs()),1);self.assertEqual(self.store.job(1)['filename'],'renamed.md')

    def test_rescan_never_republishes_sent_or_uncertain_content(self):
        self.source();self.engine._scan()
        for status in ('sent','uncertain'):
            with self.store.db() as db: db.execute('UPDATE jobs SET status=? WHERE id=1',(status,))
            self.engine._scan(recheck=True)
            self.assertEqual(len(self.jobs()),1);self.assertEqual(self.store.job(1)['status'],status)
            self.assertFalse(self.api.reanalyze(1)['ok'])

    def test_settings_change_during_analysis_discards_stale_result(self):
        self.source()
        def analyzing(path,kind,config):
            self.engine.save_settings({**self.store.settings(),'brand':'Changed'})
            return {'status':'draft','title':'Stale','body':'','analysis':'text','error':''}
        self.engine.analyzer=analyzing;self.engine._scan()
        self.assertEqual(self.store.job(1)['error'],'analysis_settings_changed')
        self.assertEqual(self.store.job(1)['title'],'')

    def test_missing_file_has_actionable_recheck_error(self):
        path=self.source();self.engine._scan();path.unlink();self.engine._scan(1)
        self.assertEqual(self.store.job(1)['error'],'file_missing')

    def test_two_public_contacts_in_prose_do_not_block_ordinary_txt(self):
        path=self.source(text='راهنمای کارگاه\nبرای ثبت‌نام به hello@example.org پیام بدهید.\nپشتیبانی با help@example.net در دسترس است.')
        self.engine._scan();self.assertEqual(self.store.job(1)['status'],'draft')
        self.assertFalse(sensitive_reason(path.read_text()))

    def test_openai_key_is_private_removable_and_never_saved_in_settings(self):
        self.assertTrue(self.api.save_openai_key(KEY)['ok'])
        self.assertTrue(self.api.snapshot()['settings']['openai_key_set'])
        self.assertNotIn(KEY,json.dumps(self.api.snapshot()))
        self.engine.save_settings({**self.store.settings(),'openai_api_key':KEY})
        with self.store.db() as db:
            self.assertNotIn(KEY,str([tuple(row) for row in db.execute('SELECT * FROM settings')]))
        self.assertEqual(self.store.openai_key(),KEY)
        self.assertTrue(self.api.clear_openai_key()['ok'])
        self.assertFalse(self.store.has_openai_key())

    def test_openai_key_rejects_header_control_characters(self):
        for value in ('short','sk-a\n'+KEY,'sk-a\0'+KEY):
            self.assertEqual(self.api.save_openai_key(value)['error'],'openai_key_invalid')

    def test_openai_metadata_is_persisted_and_caption_uses_real_publication_date(self):
        self.source();self.engine.save_openai_key(KEY)
        self.engine.save_settings({**self.store.settings(),'ai':True,'ai_provider':'openai'})
        response=Mock(status_code=200);response.json.return_value=response_body()
        with patch('ai_client.requests.request',return_value=response) as request:
            self.engine._scan()
        job=self.store.job(1)
        self.assertEqual(job['content_type'],'educational');self.assertEqual(job['source_date'],'۱۴۰۵/۰۸/۱۶')
        self.assertEqual(job['analysis'],'openai')
        when=datetime(2030,1,2,9,0,tzinfo=ZoneInfo('Asia/Tehran')).timestamp()
        text=caption(job,self.store.settings(),when)
        self.assertIn('آموزشی',text);self.assertIn('۱۴۰۵/۰۸/۱۶',text);self.assertIn('09:00',text)
        self.assertEqual(request.call_args.kwargs['headers']['Authorization'],'Bearer '+KEY)
        self.assertFalse(request.call_args.kwargs['allow_redirects'])

    def test_missing_openai_key_is_reviewable_not_permanently_blocked(self):
        self.source();self.engine.save_settings({**self.store.settings(),'ai':True,'ai_provider':'openai'})
        with patch('ai_client.requests.request') as request: self.engine._scan()
        request.assert_not_called();self.assertEqual(self.store.job(1)['status'],'review')
        self.assertEqual(self.store.job(1)['error'],'openai_key_missing')

    def test_schema_migration_preserves_old_job(self):
        directory=self.root/'legacy';directory.mkdir()
        with sqlite3.connect(directory/'publisher.sqlite3') as db:
            db.execute('CREATE TABLE jobs(id INTEGER PRIMARY KEY,title TEXT)')
            db.execute("INSERT INTO jobs VALUES(7,'Existing title')")
        store=Store(directory);job=store.job(7)
        self.assertEqual(job['title'],'Existing title')
        self.assertEqual(job['content_type'],'');self.assertEqual(job['source_date'],'')


class OpenAIContractTests(unittest.TestCase):
    def test_text_and_image_use_responses_api_structured_output(self):
        captured=[]
        result=openai_analysis('System instructions','poster.png','photo','Description',[b'image'],
            'gpt-4.1-mini',KEY,lambda payload:(captured.append(payload) or response_body()))
        payload=captured[0]
        self.assertEqual(result['content_type'],'educational')
        self.assertFalse(payload['store']);self.assertNotIn(KEY,json.dumps(payload))
        self.assertEqual(payload['text']['format']['type'],'json_schema')
        self.assertTrue(payload['text']['format']['strict'])
        self.assertEqual(payload['input'][0]['content'][1]['type'],'input_image')
        self.assertTrue(payload['input'][0]['content'][1]['image_url'].startswith('data:image/jpeg;base64,'))

    def test_api_errors_are_actionable_and_never_include_provider_body(self):
        for status,expected in [(401,'openai_unauthorized'),(403,'openai_forbidden'),
                                (404,'openai_model_missing'),(429,'openai_rate_limited'),
                                (500,'openai_unavailable'),(302,'openai_request_failed')]:
            response=Mock(status_code=status);response.json.return_value={'error':{'message':KEY}}
            with self.subTest(status=status),patch('ai_client.requests.request',return_value=response):
                with self.assertRaises(AIError) as caught: check_openai(KEY,'gpt-4.1-mini')
                self.assertEqual(caught.exception.code,expected);self.assertNotIn(KEY,str(caught.exception))

    def test_quota_and_network_failures_are_distinct(self):
        response=Mock(status_code=429);response.json.return_value={'error':{'code':'insufficient_quota'}}
        with patch('ai_client.requests.request',return_value=response):
            with self.assertRaisesRegex(AIError,'openai_quota_exceeded'): check_openai(KEY,'gpt-4.1-mini')
        with patch('ai_client.requests.request',side_effect=requests.ConnectionError(KEY)):
            with self.assertRaisesRegex(AIError,'openai_unavailable'): check_openai(KEY,'gpt-4.1-mini')

    def test_refusal_and_incomplete_responses_are_not_ready(self):
        cases=[({'status':'incomplete'},'ai_invalid_response'),
               ({'output':[{'type':'message','content':[{'type':'refusal','refusal':'No'}]}]},'ai_flagged_content')]
        for raw,expected in cases:
            with self.subTest(expected=expected),self.assertRaisesRegex(AIError,expected):
                openai_analysis('Prompt','lesson.txt','text','Text',[],'gpt-4.1-mini',KEY,lambda _:raw)

    def test_source_date_cannot_be_invented_for_txt(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'lesson.txt';path.write_text('An ordinary photography lesson.')
            result=analyze(path,'text',{**DEFAULTS,'ai_provider':'openai'},
                           lambda _:response_body(source_date='2040-12-01'))
            self.assertEqual(result['source_date'],'')

    def test_sensitive_source_is_not_sent_to_openai(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'private.txt';path.write_text('password: synthetic-example-only')
            transport=Mock(side_effect=AssertionError('Sensitive source must stay local'))
            result=analyze(path,'text',{**DEFAULTS,'ai_provider':'openai'},transport)
            self.assertEqual(result['status'],'blocked');transport.assert_not_called()


if __name__=='__main__': unittest.main()
