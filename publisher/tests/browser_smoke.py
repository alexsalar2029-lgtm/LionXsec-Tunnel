"""Visual and DOM QA of the desktop UI with a local API and no live Telegram calls."""
from functools import partial
from http.server import ThreadingHTTPServer,SimpleHTTPRequestHandler
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
from unittest.mock import patch

from PIL import Image,ImageDraw
from playwright.sync_api import sync_playwright,expect

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from app import API
from content import analyze
from engine import Engine
from storage import Store,DEFAULTS

ARTIFACTS=ROOT/'tests/artifacts';ARTIFACTS.mkdir(exist_ok=True)
class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self,*args): pass
class TestAccount:
    def connect(self,timeout=45): return True
    def verify(self,channel): return {'account':'@example_account','channel_title':'کارگاه طراحی','channel_id':'-10011111111'}
    def send(self,*args): raise AssertionError('This UI test does not send Telegram messages')
    def disconnect(self): pass

with tempfile.TemporaryDirectory() as temp:
    base=Path(temp);folder=base/'content';folder.mkdir();store=Store(base/'data');engine=Engine(store,lambda *args:TestAccount())
    engine.save_settings({**DEFAULTS,'folder':str(folder),'ai':False,'auto_watch':False,'channel':'@example_channel',
                          'tag':'@example_channel','website':'https://example.com'})
    store.set_credentials('1234567','a'*32,'+989121234567')
    store.set_session('VISUAL-SESSION')
    store.update_settings({'account_name':'کاربر نمونه','account_username':'example_account','account_phone':'+989121234567'})
    api=API(store,engine)
    server=ThreadingHTTPServer(('127.0.0.1',0),partial(QuietHandler,directory=str(ROOT/'ui')))
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True,executable_path=os.environ.get('CHROMIUM_EXECUTABLE') or shutil.which('chromium'),args=['--no-sandbox'])
            page=browser.new_page(viewport={'width':1440,'height':1080},device_scale_factor=1)
            errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
            allowed=['snapshot','detail','preview_caption','thumbnail','edit','save_settings','scan','enable','pause','verify','prepare_copy','remove','reanalyze','acknowledge','login_start','login_code','login_password','login_cancel','disconnect_account','clear_analysis','save_openai_key','clear_openai_key','test_ai']
            page.expose_function('qaCall',lambda name,args:getattr(api,name)(*args) if name in allowed else None)
            page.add_init_script('window.pywebview={api:Object.fromEntries('+json.dumps(allowed)+'.map(n=>[n,(...a)=>window.qaCall(n,a)]))};')
            page.goto('http://127.0.0.1:'+str(server.server_port),wait_until='networkidle')
            expect(page.locator('#boot')).to_be_hidden()
            assert page.locator('#toggle-run i').bounding_box()['width']==18
            assert page.locator('#toggle-run').bounding_box()['height']<=50
            page.screenshot(path=str(ARTIFACTS/'empty-desktop.png'),full_page=True)
            for i,title in enumerate(['راهنمای عکاسی در نور طبیعی','تمرین طراحی هویت بصری','یادداشت‌های کارگاه تصویرسازی']):
                (folder/f'lesson-{i+1}.txt').write_text(title+'\nمجموعهٔ نکته‌ها و تمرین‌های آموزشی برای کلاس طراحی.',encoding='utf-8')
            for i,color in enumerate(['#3e5364','#4c3d68','#314f4c']):
                image=Image.new('RGB',(840,480),color);draw=ImageDraw.Draw(image)
                draw.ellipse((140,-100,600,360),outline='#b7a3df',width=3);draw.rectangle((280,130,550,400),outline='#cfb99c',width=4)
                image.save(folder/f'study-{i+1}.png')
                Path(str(folder/f'study-{i+1}.png')+'.caption.txt').write_text(['پوستر کارگاه طراحی','مطالعهٔ رنگ و فرم','ایده‌های تصویری هفته'][i]+'\nتمرین بصری با رنگ‌ها و شکل‌های ساده.',encoding='utf-8')
            engine._scan();page.evaluate('refresh()');expect(page.locator('.content-card')).to_have_count(6)
            page.screenshot(path=str(ARTIFACTS/'library-desktop.png'),full_page=True)
            page.locator('.content-card').first.click();expect(page.locator('#post-dialog')).to_be_visible()
            page.locator('#edit-title').fill('پیش‌نمایش بدون ذخیره')
            expect(page.locator('#caption-preview')).to_contain_text('پیش‌نمایش بدون ذخیره')
            page.screenshot(path=str(ARTIFACTS/'post-preview.png'),full_page=True)
            page.keyboard.press('Escape')
            page.locator('[data-page=settings]').click()
            expect(page.locator('#account-connected')).to_be_visible()
            expect(page.locator('#account-name')).to_have_text('کاربر نمونه')
            expect(page.locator('#login-wizard')).to_be_hidden()
            page.locator('[name=caption_format]').select_option('custom')
            page.locator('[name=caption_template]').fill('📄 {title}\n\n{body}\n\n{year} BY {brand}')
            page.locator('#settings-form button[type=submit]').click()
            expect(page.locator('#toast')).to_contain_text('تنظیمات ذخیره شد')
            page.screenshot(path=str(ARTIFACTS/'settings.png'),full_page=True)
            page.locator('#logout-account').click();expect(page.locator('#toast')).to_contain_text('خارج شدی')
            expect(page.locator('#login-wizard')).to_be_visible()
            expect(page.locator('#login-step-keys')).to_be_visible()
            page.locator('#login-send-code').click();expect(page.locator('#login-result')).to_contain_text('api_id')
            expect(page.locator('#login-step-keys')).to_be_visible()
            store.set_credentials('1234567','a'*32,'+989121234567');store.set_session('VISUAL-SESSION')
            store.update_settings({'account_name':'کاربر نمونه','account_username':'example_account','account_phone':'+989121234567'})
            page.evaluate('refresh()')
            expect(page.locator('#account-connected')).to_be_visible()
            for width in [900,1100,1440]:
                page.set_viewport_size({'width':width,'height':950})
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),width
            page.locator('[data-page=library]').click();page.locator('#search').fill('نور طبیعی')
            expect(page.locator('.content-card')).to_have_count(1)
            page.locator('#search').fill('');page.locator('#toggle-run').click()
            expect(page.locator('#state-pill')).to_contain_text('فعال')
            assert page.locator('#toggle-run').bounding_box()['height']<=50
            page.locator('[data-page=queue]').click();expect(page.locator('#queue-rows tr[data-id]')).to_have_count(6)
            page.locator('#toggle-run').click();expect(page.locator('#state-pill')).to_contain_text('متوقف')
            page.locator('[data-page=settings]').click()
            assert page.locator('[name=content_restrictions]').count()==0
            # Configure OpenAI through the real settings and API, using an explicit fake key.
            page.locator('[data-page=settings]').click()
            page.locator('[name=ai_provider]').select_option('openai')
            expect(page.locator('#openai-options')).to_be_visible()
            expect(page.locator('#ollama-options')).to_be_hidden()
            page.locator('[name=ai]').check()
            key='sk-ui-fixture-not-a-real-api-key'
            page.locator('#openai-key').fill(key)
            with patch('engine.check_openai',return_value={'provider':'openai','model':'gpt-4.1-mini'}) as check:
                page.locator('#test-ai').click()
                expect(page.locator('#ai-result')).to_contain_text('OpenAI و مدل')
                check.assert_called_once_with(key,'gpt-4.1-mini')
            expect(page.locator('#openai-key')).to_have_value('')
            expect(page.locator('#openai-key-status')).to_contain_text('کلید ذخیره شده')
            assert key not in page.evaluate('JSON.stringify(state)')
            page.screenshot(path=str(ARTIFACTS/'openai-settings.png'),full_page=True)
            page.locator('#clear-openai-key').click()
            expect(page.locator('#openai-key-status')).to_contain_text('هنوز کلیدی')
            page.locator('#openai-key').fill(key)
            page.locator('#settings-form button[type=submit]').click()
            expect(page.locator('#openai-key-status')).to_contain_text('کلید ذخیره شده')

            # All remote model responses are simulated; real file parsing and API bridge remain active.
            def fixture_analyzer(path,kind,config):
                def transport(payload):
                    value={'decision':'ready','title':'آموزش '+path.stem,'body':'توضیح آموزشی نمونه',
                           'reason':'','content_type':'educational','source_date':''}
                    return {'status':'completed','output':[{'type':'message','content':[
                        {'type':'output_text','text':json.dumps(value,ensure_ascii=False)}]}]}
                return analyze(path,kind,config,transport)
            engine.analyzer=fixture_analyzer
            originals={p:p.read_bytes() for p in folder.iterdir()}
            page.locator('[data-page=library]').click()
            page.locator('#clear-analysis').click();expect(page.locator('#clear-dialog')).to_be_visible()
            page.screenshot(path=str(ARTIFACTS/'clear-results.png'),full_page=True)
            page.locator('#clear-confirm').click()
            expect(page.locator('#clear-dialog')).not_to_be_visible()
            page.wait_for_function("state.jobs.length===6 && state.jobs.every(j=>j.error==='analysis_required')")
            assert all(p.read_bytes()==data for p,data in originals.items())
            page.locator('#scan').click()
            page.wait_for_function("!state.runtime.scanning && state.jobs.length===6 && state.jobs.every(j=>j.analysis==='openai')")
            assert all(j['content_type']=='educational' for j in api.snapshot()['jobs'])
            page.locator('.content-card').first.click()
            expect(page.locator('#detail-type')).to_contain_text('آموزشی')
            page.locator('#clear-post').click()
            expect(page.locator('#detail-error')).to_contain_text('نتیجهٔ قبلی پاک شده')
            expect(page.locator('#save-post')).to_be_disabled()
            expect(page.locator('#reanalyze')).to_be_enabled()
            page.locator('#reanalyze').click()
            page.wait_for_function("!state.runtime.scanning && state.jobs.every(j=>j.status==='draft')")

            blocked=folder/'blocked.txt';blocked.write_text('password: synthetic-example-only')
            engine._scan();page.evaluate('refresh()')
            card=page.locator('.content-card').filter(has_text='blocked.txt')
            expect(card).to_contain_text('پیش‌نویس');card.click()
            expect(page.locator('#save-post')).to_be_enabled()
            expect(page.locator('#reanalyze')).to_be_enabled()
            blocked.write_text('درس اصلاح‌شده\nآموزش طراحی با نور طبیعی',encoding='utf-8')
            page.locator('#reanalyze').click()
            page.wait_for_function("!state.runtime.scanning && state.jobs.length===7 && state.jobs.every(j=>j.status==='draft')")
            page.screenshot(path=str(ARTIFACTS/'openai-library.png'),full_page=True)
            for width in [900,1100,1440]:
                page.set_viewport_size({'width':width,'height':950})
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),width
            assert not errors,errors
            browser.close()
            print('PASS: library, account/login, OpenAI settings and private key, clear/recheck controls, corrected blocked TXT, metadata, queue/pause and responsive layouts; no JavaScript errors. OpenAI and Telegram calls simulated.')
    finally: server.shutdown();server.server_close()
