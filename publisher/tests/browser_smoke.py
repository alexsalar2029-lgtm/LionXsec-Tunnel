"""Visual and DOM QA of the desktop UI with a local API and no live Telegram calls."""
from functools import partial
from http.server import ThreadingHTTPServer,SimpleHTTPRequestHandler
import json
from pathlib import Path
import sys
import tempfile
import threading

from PIL import Image,ImageDraw
from playwright.sync_api import sync_playwright,expect

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from app import API
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
            browser=p.chromium.launch(headless=True,args=['--no-sandbox'])
            page=browser.new_page(viewport={'width':1440,'height':1080},device_scale_factor=1)
            errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
            allowed=['snapshot','detail','preview_caption','thumbnail','edit','save_settings','scan','enable','pause','verify','prepare_copy','remove','reanalyze','acknowledge','login_start','login_code','login_password','login_cancel','disconnect_account']
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
            assert not errors,errors
            browser.close()
            print('PASS: library, settings, account card, login wizard, preview, search, queue, pause, layouts at 900/1100/1440; no JavaScript errors.')
    finally: server.shutdown();server.server_close()
