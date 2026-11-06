import base64
from datetime import datetime
from types import SimpleNamespace
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from PIL import Image
import requests

from app import API
from desktop_runtime import desktop_gui,initialize_desktop
from content import ContentError,analyze,caption,fingerprint,prepare,source_text,utf16len,video_frames
from engine import Engine,next_slot,validate_settings
from mtproto_client import MTProto,TelegramError,translate_error,normalize_api_hash,normalize_api_id,normalize_phone,account_label
from storage import Store,SingleInstance,DEFAULTS


class FakeMTProto:
    """Stand-in for the persistent publishing client created from a stored session."""

    def __init__(self,session,api_id,api_hash):
        self.session=session;self.api_id=api_id;self.api_hash=api_hash
        self.deliveries=[];self.failure=None;self.connect_calls=0;self.disconnected=False;self.verified=None
    def connect(self,timeout=45): self.connect_calls+=1;return True
    def verify(self,channel):
        self.verified=channel
        return {'account':'@tester','channel_title':'Example channel','channel_id':'-1001234567890'}
    def send(self,job,text,path,kind):
        if self.failure: raise self.failure
        self.deliveries.append({'channel':job['channel'],'caption':text,'bytes':Path(path).read_bytes(),'kind':kind})
        return {'message_id':100+len(self.deliveries)}
    def disconnect(self): self.disconnected=True


class FakeLoginClient:
    """Stand-in for the throwaway client used while the interactive login runs."""

    def __init__(self,session,api_id,api_hash,behavior=None):
        self.session=session;self.api_id=api_id;self.api_hash=api_hash;self.behavior=behavior or {}
        self.calls=[];self.disconnected=False
    def connect(self,timeout=45): return True
    def code_request(self,phone): self.calls.append(('code',phone));return 'HASH-1'
    def sign_code(self,phone,code,phone_code_hash):
        self.calls.append(('sign_code',phone,code,phone_code_hash))
        if self.behavior.get('password'): return {'password_required':True}
        if self.behavior.get('code_error'): raise self.behavior['code_error']
        return self.behavior.get('user') or SimpleNamespace(first_name='علی',last_name='رضایی',username='ali_test')
    def sign_password(self,password):
        self.calls.append(('sign_password',password))
        if self.behavior.get('password_error'): raise self.behavior['password_error']
        return self.behavior.get('user') or SimpleNamespace(first_name='علی',last_name='رضایی',username='ali_test')
    def session_string(self): return 'SESSION-STRING-ABC'
    def disconnect(self): self.disconnected=True


class PublisherTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.folder=self.root/'input';self.folder.mkdir();self.store=Store(self.root/'data')
        self.now=datetime(2030,1,2,8,58,tzinfo=ZoneInfo('Asia/Tehran')).timestamp()
        self.behavior={}
        self.clients=[]
        def factory(session,api_id,api_hash):
            if session is None: client=FakeLoginClient(session,api_id,api_hash,self.behavior)
            else: client=FakeMTProto(session,api_id,api_hash)
            self.clients.append(client);return client
        self.engine=Engine(self.store,factory,clock=lambda:self.now)
        self.config={**DEFAULTS,'folder':str(self.folder),'channel':'@example_channel','tag':'@example_channel',
                     'website':'https://example.com','ai':False,'auto_watch':False}
        self.engine.save_settings(self.config)
        self.engine.login_start('1234567','a'*32,'09121234567')
        self.engine.login_code('54321')
        self.store.update_settings({'enabled':False})

    def tearDown(self): self.temp.cleanup()
    def source(self,name='lesson.txt',text='عنوان آموزشی\nروش عکاسی در نور طبیعی'):
        path=self.folder/name;path.write_text(text,encoding='utf-8');return path
    def scan(self):
        self.engine._scan()
        with self.store.db() as db: return [dict(row) for row in db.execute('SELECT * FROM jobs ORDER BY id')]
    def due(self):
        self.engine.enable();job=self.store.job(1);self.now=job['scheduled'];return job
    def publisher(self):
        if not any(isinstance(c,FakeMTProto) for c in self.clients): self.engine.account_client()
        client=[c for c in self.clients if isinstance(c,FakeMTProto)][-1]
        client.failure=None
        return client

    def test_txt_copy_preserves_all_source_and_inserts_links_both_positions(self):
        text='دانشگاه و طراحی\r\n'+('متن درس ' * 7000)
        path=self.source(text=text);original=path.read_bytes();job=self.scan()[0]
        for placement in ['header','footer']:
            prepared,kind=prepare(job,{**self.config,'text_placement':placement},self.store.directory/'prepared')
            output=prepared.read_bytes().decode('utf-8-sig')
            self.assertIn(text,output);self.assertEqual(kind,'document')
            self.assertIn('https://example.com',output)
            self.assertTrue(output.startswith('@example_channel') if placement=='header' else output.startswith(text))
            self.assertEqual(path.read_bytes(),original)
        self.assertNotEqual(path,prepared)

    def test_scan_is_deduplicated_and_unknown_svb_is_not_processed(self):
        self.source();self.source('copy.txt');self.source('unknown.svb','opaque format')
        self.assertEqual(len(self.scan()),1);self.assertEqual(len(self.scan()),1)
        self.assertFalse(self.store.settings()['enabled']);self.assertEqual(self.publisher().deliveries,[])

    def test_schedule_slots_use_tehran_timezone_and_do_not_collide(self):
        first=next_slot(self.config,self.now)
        self.assertEqual(datetime.fromtimestamp(first,ZoneInfo('Asia/Tehran')).strftime('%H:%M'),'09:00')
        second=next_slot(self.config,self.now,[first])
        self.assertEqual(datetime.fromtimestamp(second,ZoneInfo('Asia/Tehran')).strftime('%H:%M'),'14:00')
        end=datetime(2030,1,2,22,tzinfo=ZoneInfo('Asia/Tehran')).timestamp()
        self.assertEqual(datetime.fromtimestamp(next_slot(self.config,end),ZoneInfo('Asia/Tehran')).day,3)

    def test_one_send_at_due_time_with_edited_document_and_persian_date(self):
        self.source();self.scan();self.engine.enable()
        self.assertFalse(self.engine.tick());self.now=self.store.job(1)['scheduled']
        self.assertTrue(self.engine.tick());self.assertFalse(self.engine.tick())
        delivery=self.publisher().deliveries[0]
        self.assertEqual(delivery['kind'],'document');self.assertIn('@example_channel',delivery['bytes'].decode('utf-8-sig'))
        self.assertIn('📅',delivery['caption']);self.assertIn('09:00',delivery['caption']);self.assertIn('https://example.com',delivery['caption'])
        self.assertEqual(self.store.job(1)['status'],'sent');self.assertEqual(len(self.publisher().deliveries),1)

    def test_restart_does_not_resend_unknown_delivery(self):
        self.source();self.scan();self.due()
        with self.store.db() as db: db.execute("UPDATE jobs SET status='sending'")
        self.store.recover();self.assertFalse(self.store.settings()['enabled'])
        self.assertEqual(self.store.job(1)['status'],'uncertain')
        self.engine.enable();self.assertFalse(self.engine.tick())
        self.engine.acknowledge(1,'sent');self.assertEqual(self.store.job(1)['status'],'sent')

    def test_interrupted_analysis_can_be_edited_after_restart(self):
        self.source();self.scan()
        with self.store.db() as db: db.execute("UPDATE jobs SET status='review',error='analyzing'")
        self.store.recover();self.engine.edit(1,'عنوان تازه','متن تازه')
        self.assertEqual(self.store.job(1)['status'],'draft')

    def test_ambiguous_timeout_stops_automatic_retries(self):
        self.source();self.scan();self.due();self.publisher().failure=TelegramError('delivery_unknown',uncertain=True)
        self.engine.tick();self.assertEqual(self.store.job(1)['status'],'uncertain')
        self.now+=100000;self.assertFalse(self.engine.tick());self.assertEqual(self.publisher().deliveries,[])

    def test_rate_limit_uses_next_free_schedule_slot(self):
        self.source();self.scan();self.due();self.publisher().failure=TelegramError('telegram_rate_limit',retry_after=90)
        self.engine.tick();job=self.store.job(1)
        self.assertEqual(job['status'],'scheduled');self.assertGreater(job['scheduled'],self.now+90)
        self.assertEqual(datetime.fromtimestamp(job['scheduled'],ZoneInfo('Asia/Tehran')).strftime('%H:%M'),'14:00')

    def test_changed_file_and_sensitive_file_are_never_sent(self):
        path=self.source();self.scan();self.due();path.write_text('Changed data',encoding='utf-8')
        self.engine.tick();self.assertEqual(self.store.job(1)['status'],'review');self.assertEqual(self.publisher().deliveries,[])
        self.source('private.txt','password: synthetic-example-only')
        jobs=self.scan();self.assertTrue(any(j['status']=='blocked' for j in jobs))

    def test_excluding_during_analysis_is_not_undone_by_result(self):
        self.source()
        def analyzing(path,kind,config):
            self.engine.remove(1)
            return {'status':'draft','title':'Title','body':'Body','error':'','analysis':'text'}
        self.engine.analyzer=analyzing;self.scan();self.assertEqual(self.store.job(1)['status'],'excluded')

    def test_blocked_or_unknown_delivery_cannot_be_reset_by_exclusion(self):
        self.source();self.scan()
        for status in ['blocked','uncertain']:
            with self.store.db() as db: db.execute('UPDATE jobs SET status=? WHERE id=1',(status,))
            self.engine.remove(1)
            self.assertEqual(self.store.job(1)['status'],status)
            with self.assertRaisesRegex(ValueError,'job_locked'):
                self.engine.edit(1,'New title','New body')

    def test_config_changed_during_prepare_does_not_send_old_destination(self):
        self.source();self.scan();self.due()
        original=prepare
        def changed(job,config,out):
            result=original(job,config,out)
            self.engine.save_settings({**self.config,'channel':'@new_example_channel'})
            self.store.update_settings({'enabled':True})
            return result
        with patch('engine.prepare',changed): self.engine.tick()
        self.assertEqual(self.publisher().deliveries,[]);self.assertEqual(self.store.job(1)['status'],'draft')

    def test_config_changed_during_verify_is_rejected(self):
        def verify(channel):
            self.engine.save_settings({**self.config,'channel':'@new_example_channel'})
            return {}
        self.publisher().verify=verify
        with self.assertRaisesRegex(ValueError,'changed_during_verification'): self.engine.enable()
        self.assertFalse(self.store.settings()['enabled'])

    def test_missed_slots_are_moved_forward_without_catchup_burst(self):
        self.source();self.scan();self.due();self.now+=3600
        self.assertFalse(self.engine.tick());self.assertGreater(self.store.job(1)['scheduled'],self.now)

    def test_caption_limit_counts_emoji_and_preserves_footer(self):
        text=caption({'title':'عنوان','body':'🦁'*1000,'kind':'photo'},self.config,self.now)
        self.assertLessEqual(utf16len(text),1024);self.assertTrue(text.endswith('https://example.com'))

    def test_native_snapshot_does_not_expose_session_secrets(self):
        snapshot=API(self.store,self.engine).snapshot()
        self.assertTrue(snapshot['settings']['account_set'])
        dumped=json.dumps(snapshot)
        self.assertNotIn('SESSION-STRING-ABC',dumped);self.assertNotIn('a'*32,dumped)
        self.assertNotIn('account-session.dat',dumped);self.assertNotIn('api-credentials.dat',dumped)

    def test_custom_caption_uses_publish_year_and_exact_template_once(self):
        self.source();job=self.scan()[0]
        config={**self.config,'caption_format':'custom','brand':'Example Studio',
                'caption_template':'📄 {title}\n\n{body}\n\n{filename}\n{year} BY {brand}'}
        output=caption({**job,'title':'A lesson {brand}','body':'Original description'},config,self.now)
        self.assertTrue(output.startswith('📄 A lesson {brand}\n\nOriginal description\n\nlesson.txt\n2030 BY Example Studio'))
        self.assertEqual(output.count('📅'),1)
        self.assertTrue(output.endswith('https://example.com'))

    def test_custom_caption_keeps_footer_inside_telegram_limit(self):
        output=caption({'title':'درس','body':'🦁'*500,'filename':'lesson.txt'},
                       {**self.config,'caption_format':'custom','caption_template':'{title}\n{body}\n{body}\n{year}'},self.now)
        self.assertLessEqual(utf16len(output),1024)
        self.assertTrue(output.endswith('https://example.com'))

    def test_template_validation_rejects_unknown_fields_and_expressions(self):
        for template in ['no title','{title}\n{secret}','{title.__class__}','{title}\n{body!r}','{title}\n{broken']:
            with self.subTest(template=template),self.assertRaisesRegex(ValueError,'caption_template_invalid'):
                self.engine.save_settings({**self.config,'caption_template':template})

    def test_email_collections_are_not_turned_into_promotional_posts(self):
        self.source(text='first@example.invalid\nsecond@example.invalid')
        self.engine.save_settings({**self.config,'caption_format':'custom'})
        self.assertEqual(self.scan()[0]['status'],'blocked')
        self.engine.enable();self.assertFalse(self.engine.tick());self.assertEqual(self.publisher().deliveries,[])

    def test_unsaved_caption_preview_does_not_change_job(self):
        self.source();job=self.scan()[0]
        api=API(self.store,self.engine)
        result=api.preview_caption(job['id'],'New heading','New description')
        self.assertTrue(result['ok']);self.assertIn('New heading',result['value'])
        self.assertEqual(self.store.job(job['id'])['title'],job['title'])

    def test_renderer_never_accepts_legacy_fallback(self):
        with patch.dict('os.environ',{}):
            calls=[]
            def initialize(name): calls.append(name);return SimpleNamespace(renderer='mshtml')
            with self.assertRaisesRegex(RuntimeError,'desktop_renderer_unavailable'):
                initialize_desktop(initialize)
            self.assertEqual(calls,[desktop_gui()])
            expected='edgechromium' if desktop_gui()=='edgechromium' else 'qtwebengine'
            self.assertEqual(initialize_desktop(lambda _:SimpleNamespace(renderer=expected)).renderer,expected)

    def test_second_instance_lock_is_rejected(self):
        one=SingleInstance(self.store.directory)
        try:
            with self.assertRaises(RuntimeError): SingleInstance(self.store.directory)
        finally: one.close()

    def test_image_watermark_changes_only_prepared_image(self):
        path=self.folder/'image.png';Image.new('RGB',(640,400),'#304060').save(path)
        original=path.read_bytes();job=self.scan()[0]
        output,kind=prepare(job,{**self.config,'watermark':True},self.store.directory/'prepared')
        self.assertEqual(kind,'photo');self.assertNotEqual(Image.open(output).getpixel((100,380)),Image.open(path).getpixel((100,380)))
        self.assertEqual(path.read_bytes(),original)

    def test_local_vision_receives_images_and_source_as_data(self):
        path=self.folder/'poster.png';Image.new('RGB',(20,20),'blue').save(path)
        def transport(payload):
            self.assertEqual(len(payload['messages'][1]['images']),1)
            self.assertIn('poster.png',payload['messages'][1]['content'])
            return {'message':{'content':json.dumps({'decision':'ready','title':'Poster','body':'Blue study poster'})}}
        result=analyze(path,'photo',{**self.config,'ai':True},transport)
        self.assertEqual(result['status'],'draft');self.assertEqual(result['analysis'],'ollama')


class LoginTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.folder=self.root/'input';self.folder.mkdir();self.store=Store(self.root/'data')
        self.behavior={};self.clients=[];self.now=time.time()
        def factory(session,api_id,api_hash):
            if session is None: client=FakeLoginClient(session,api_id,api_hash,self.behavior)
            else: client=FakeMTProto(session,api_id,api_hash)
            self.clients.append(client);return client
        self.engine=Engine(self.store,factory,clock=lambda:self.now)
        self.config={**DEFAULTS,'folder':str(self.folder),'channel':'@example_channel','ai':False,'auto_watch':False}
        self.engine.save_settings(self.config)

    def tearDown(self): self.temp.cleanup()
    def source(self,name='lesson.txt',text='عنوان آموزشی\nروش عکاسی در نور طبیعی'):
        path=self.folder/name;path.write_text(text,encoding='utf-8');return path

    def test_full_login_normalizes_input_and_persists_protected_session(self):
        result=self.engine.login_start(' ۱۲۳۴۵۶۷ ','A'*32,'0912 123 4567')
        self.assertEqual(result,{'phone':'+989121234567'})
        login=[c for c in self.clients if isinstance(c,FakeLoginClient)][0]
        self.assertEqual(login.api_id,'1234567');self.assertEqual(login.api_hash,'a'*32)
        self.assertEqual(login.calls[0],('code','+989121234567'))
        self.assertFalse(self.store.has_session())
        outcome=self.engine.login_code('۵۴۳۲۱')
        self.assertEqual(outcome,{'account':'@ali_test'})
        self.assertEqual(login.calls[1],('sign_code','+989121234567','54321','HASH-1'))
        self.assertTrue(login.disconnected)
        self.assertTrue(self.store.has_session());self.assertEqual(self.store.session(),'SESSION-STRING-ABC')
        credentials=self.store.credentials()
        self.assertEqual(credentials,{'api_id':'1234567','api_hash':'a'*32,'phone':'+989121234567'})
        settings=self.store.settings()
        self.assertEqual(settings['account_name'],'علی رضایی');self.assertEqual(settings['account_username'],'ali_test')
        self.assertFalse(settings['enabled'])
        result=API(self.store,self.engine).login_code('99999')
        self.assertFalse(result['ok']);self.assertEqual(result['error'],'login_not_started')

    def test_two_step_password_flow_finishes_login(self):
        self.behavior['password']=True
        self.engine.login_start('1234567','a'*32,'+989121234567')
        outcome=self.engine.login_code('54321')
        self.assertEqual(outcome,{'password_required':True})
        self.assertFalse(self.store.has_session())
        self.behavior['password']=False
        outcome=self.engine.login_password('top-secret')
        self.assertEqual(outcome,{'account':'@ali_test'})
        self.assertEqual([c for c in self.clients if isinstance(c,FakeLoginClient)][0].calls[-1],('sign_password','top-secret'))
        self.assertTrue(self.store.has_session())

    def test_wrong_code_and_password_surface_stable_errors(self):
        self.behavior['code_error']=TelegramError('login_code_invalid')
        self.engine.login_start('1234567','a'*32,'+989121234567')
        with self.assertRaisesRegex(TelegramError,'login_code_invalid'): self.engine.login_code('00000')
        self.behavior.clear();self.behavior['password']=True
        self.engine.login_code('54321')
        self.behavior['password']=False;self.behavior['password_error']=TelegramError('login_password_invalid')
        with self.assertRaisesRegex(TelegramError,'login_password_invalid'): self.engine.login_password('bad')
        self.assertFalse(self.store.has_session())

    def test_invalid_credentials_are_rejected_without_network(self):
        for api_id,api_hash,phone,expected in [('abcd','a'*32,'+989121234567','api_id_invalid'),
                                               ('1234567','short','+989121234567','api_hash_invalid'),
                                               ('1234567','a'*32,'12','phone_invalid')]:
            with self.subTest(expected=expected),self.assertRaisesRegex(ValueError,expected):
                self.engine.login_start(api_id,api_hash,phone)
        self.assertEqual(self.clients,[])

    def test_login_busy_cancel_and_restart(self):
        self.engine.login_start('1234567','a'*32,'+989121234567')
        with self.assertRaisesRegex(ValueError,'login_busy'): self.engine.login_start('1234567','a'*32,'+989121234567')
        login=[c for c in self.clients if isinstance(c,FakeLoginClient)][0]
        self.engine.login_cancel()
        self.assertTrue(login.disconnected)
        with self.assertRaisesRegex(ValueError,'login_not_started'): self.engine.login_code('54321')
        self.engine.login_start('1234567','a'*32,'+989121234567')
        outcome=self.engine.login_code('54321')
        self.assertEqual(outcome,{'account':'@ali_test'})

    def test_disconnect_account_clears_secrets_and_stops_publishing(self):
        self.engine.login_start('1234567','a'*32,'+989121234567')
        self.engine.login_code('54321')
        self.store.update_settings({'enabled':True})
        self.engine.account_client().connect()
        publisher=[c for c in self.clients if isinstance(c,FakeMTProto)][0]
        self.engine.disconnect_account()
        self.assertFalse(self.store.has_session());self.assertEqual(self.store.credentials(),{'api_id':'','api_hash':'','phone':''})
        self.assertFalse(self.store.session_path.exists());self.assertFalse(self.store.credentials_path.exists())
        settings=self.store.settings()
        self.assertEqual(settings['account_name'],'');self.assertFalse(settings['enabled'])
        self.assertTrue(publisher.disconnected)

    def test_enable_requires_logged_in_account(self):
        with self.assertRaisesRegex(TelegramError,'account_not_logged_in'): self.engine.enable()
        api=API(self.store,self.engine)
        result=api.verify();self.assertFalse(result['ok']);self.assertEqual(result['error'],'account_not_logged_in')

    def test_session_invalid_during_send_fails_job_and_resets_client(self):
        self.engine.login_start('1234567','a'*32,'+989121234567')
        self.engine.login_code('54321')
        self.source();self.engine._scan();self.engine.enable()
        job=self.store.job(1);self.now=job['scheduled']
        publisher=[c for c in self.clients if isinstance(c,FakeMTProto)][0]
        publisher.failure=TelegramError('session_invalid')
        self.engine.tick()
        self.assertEqual(self.store.job(1)['status'],'failed')
        self.assertIsNone(self.engine._account);self.assertTrue(publisher.disconnected)

    def test_account_not_logged_in_marks_job_failed(self):
        self.engine.login_start('1234567','a'*32,'+989121234567')
        self.engine.login_code('54321')
        self.engine.disconnect_account()
        self.source();self.engine._scan()
        self.store.update_settings({'enabled':True})
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='scheduled',scheduled=?,channel='@example_channel' WHERE id=1",(self.now,))
        self.engine.tick()
        self.assertEqual(self.store.job(1)['status'],'failed');self.assertEqual(self.store.job(1)['error'],'account_not_logged_in')


class MTProtoTests(unittest.TestCase):
    def entity(self,username='my_channel',broadcast=True,megagroup=False,creator=False):
        return SimpleNamespace(id=-100333444555000,title='My Channel',username=username,
                               broadcast=broadcast,megagroup=megagroup,creator=creator)

    def client(self,entity,dialogs=(),me=None,permissions=None):
        me=me or SimpleNamespace(first_name='سارا',last_name='',username='sara_dev')
        sent=[]
        class FakeAsync:
            async def get_entity(self,handle):
                target=str(handle).lstrip('@').lower()
                if entity.username and entity.username.lower()==target: return entity
                raise ValueError('Could not find the input entity')
            async def iter_dialogs(self,limit=None):
                for dialog in dialogs: yield dialog
            async def get_me(self): return me
            async def get_permissions(self,entity,user): return permissions
            async def send_message(self,entity,text,link_preview=False):
                sent.append(('text',text,link_preview));return SimpleNamespace(id=11)
            async def send_file(self,entity,path,**kwargs):
                sent.append(('file',path,kwargs));return SimpleNamespace(id=12)
        fake=FakeAsync();fake.sent=sent;return fake

    def mtproto(self,fake): return MTProto(None,'1234567','a'*32,client=fake)

    def test_verify_accepts_creator_broadcast_channel(self):
        client=self.client(self.entity(creator=True))
        info=self.mtproto(client).verify('@my_channel')
        self.assertEqual(info,{'account':'@sara_dev','channel_title':'My Channel','channel_id':'-100333444555000'})

    def test_verify_resolves_marked_id_from_dialogs_and_requires_post_right(self):
        entity=self.entity(username='')
        dialog=SimpleNamespace(id=-100333444555000,entity=entity)
        admin=SimpleNamespace(admin_rights=SimpleNamespace(post_messages=True),is_creator=False,is_banned=False,has_left=False)
        info=self.mtproto(self.client(entity,dialogs=[dialog],permissions=admin)).verify('-100333444555000')
        self.assertEqual(info['channel_title'],'My Channel')
        denied=SimpleNamespace(admin_rights=None,is_creator=False,is_banned=False,has_left=False)
        with self.assertRaisesRegex(TelegramError,'account_permission'):
            self.mtproto(self.client(entity,dialogs=[dialog],permissions=denied)).verify('-100333444555000')

    def test_verify_rejects_non_channel_destination(self):
        entity=self.entity(username='my_channel',broadcast=False,megagroup=False)
        with self.assertRaisesRegex(TelegramError,'not_channel'):
            self.mtproto(self.client(entity,permissions=SimpleNamespace(admin_rights=None,is_creator=False))).verify('@my_channel')

    def test_supergroup_membership_allows_posting(self):
        entity=self.entity(username='my_group',broadcast=False,megagroup=True)
        member=SimpleNamespace(admin_rights=None,is_creator=False,is_banned=False,has_left=False)
        info=self.mtproto(self.client(entity,permissions=member)).verify('@my_group')
        self.assertEqual(info['channel_id'],'-100333444555000')

    def test_send_maps_kinds_to_telethon_calls(self):
        entity=self.entity()
        fake=self.client(entity,permissions=SimpleNamespace(admin_rights=SimpleNamespace(post_messages=True),is_creator=False))
        proto=self.mtproto(fake)
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'lesson.txt';path.write_text('body')
            result=proto.send({'channel':'@my_channel'},'caption',path,'document')
            self.assertEqual(result,{'message_id':12})
            proto.send({'channel':'@my_channel'},'caption',path,'photo')
            proto.send({'channel':'@my_channel'},'plain',path,'text')
        self.assertEqual(fake.sent[0][0],'file');self.assertTrue(fake.sent[0][2]['force_document'])
        self.assertFalse(fake.sent[1][2]['force_document']);self.assertIn('caption',fake.sent[1][2])
        self.assertEqual(fake.sent[2][0],'text');self.assertFalse(fake.sent[2][2])

    def test_error_translation_maps_telethon_failures(self):
        from telethon import errors
        flood=errors.FloodWaitError(request=None);flood.seconds=77
        mapping=[(flood,'telegram_rate_limit',77),(errors.PhoneCodeInvalidError(request=None),'login_code_invalid',0),
                 (errors.ApiIdInvalidError(request=None),'api_credentials_invalid',0),
                 (errors.PasswordHashInvalidError(request=None),'login_password_invalid',0),
                 (errors.SessionRevokedError(request=None),'session_invalid',0),
                 (errors.ChatWriteForbiddenError(request=None),'account_permission',0),
                 (OSError('boom'),'connection_failed',0),(ValueError('x'),'telegram_request',0)]
        for error,code,retry_after in mapping:
            with self.subTest(code=code):
                translated=translate_error(error)
                self.assertEqual(translated.code,code);self.assertEqual(translated.retry_after,retry_after)
        self.assertEqual(translate_error(TelegramError('telegram_rate_limit',retry_after=5)).retry_after,5)

    def test_input_normalization(self):
        self.assertEqual(normalize_api_id(' ۱۲۳۴۵۶۷ '),'1234567')
        self.assertEqual(normalize_api_hash('A'*32),'a'*32)
        self.assertEqual(normalize_phone('09121234567'),'+989121234567')
        self.assertEqual(normalize_phone('00989121234567'),'+989121234567')
        self.assertEqual(normalize_phone('+98 912 123 4567'),'+989121234567')
        self.assertEqual(normalize_phone('989121234567'),'+989121234567')
        for value in ['','+','0123','abc']:
            with self.subTest(value=value),self.assertRaises(ValueError): normalize_phone(value)
        for value in ['','x','12345678901','-5']:
            with self.subTest(value=value),self.assertRaises(ValueError): normalize_api_id(value)
        with self.assertRaises(ValueError): normalize_api_hash('zz')

    def test_account_label_prefers_username(self):
        self.assertEqual(account_label(SimpleNamespace(first_name='س',last_name='',username='u1')),'@u1')
        self.assertEqual(account_label(SimpleNamespace(first_name='نام',last_name='خانوادگی',username=None)),'نام خانوادگی')
        self.assertEqual(account_label(None),'')


if __name__=='__main__': unittest.main()
