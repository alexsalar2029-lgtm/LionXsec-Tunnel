"""Native desktop integration test. All Telegram calls are simulated."""
from pathlib import Path
import sys
import tempfile
import time
import traceback
import json

import webview
from PIL import Image

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from app import API
from desktop_runtime import desktop_gui,initialize_desktop
from engine import Engine
from storage import Store,DEFAULTS


class TestAccount:
    def __init__(self): self.sent=[];self.verified=None
    def connect(self,timeout=45): return True
    def verify(self,channel):
        self.verified=channel
        return {'account':'@example_account','channel_title':'Example classroom','channel_id':'-10011111111'}
    def send(self,job,text,path,kind):
        self.sent.append((job,text,Path(path).read_bytes(),kind));return {'message_id':77}
    def disconnect(self): pass


result={}
with tempfile.TemporaryDirectory() as temp:
    root=Path(temp);folder=root/'input';folder.mkdir();store=Store(root/'data');account=TestAccount()
    (folder/'lesson.txt').write_text('درس عکاسی\nآموزش ترکیب‌بندی و نورپردازی برای دانشجویان',encoding='utf-8')
    Image.new('RGB',(640,400),'#53667d').save(folder/'study-poster.jpg')
    (folder/'unknown.svb').write_text('opaque test sample')
    now=[time.time()];engine=Engine(store,lambda *args:account,clock=lambda:now[0])
    engine.save_settings({**DEFAULTS,'folder':str(folder),'ai':False,'auto_watch':False,
                          'channel':'@example_channel','tag':'@example_channel','website':'https://example.com'})
    store.set_credentials('1234567','a'*32,'+989121234567')
    store.set_session('SMOKE-SESSION')
    store.update_settings({'account_name':'کاربر نمونه','account_username':'example_account','account_phone':'+989121234567'})
    api=API(store,engine)
    backend=initialize_desktop()
    assert backend.renderer==('edgechromium' if desktop_gui()=='edgechromium' else 'qtwebengine')
    window=webview.create_window('LIONXSEC Publisher QA',str(ROOT/'ui/index.html'),js_api=api,width=1360,height=940,hidden=True)
    api._window=window
    def wait(expression,seconds=30):
        until=time.monotonic()+seconds
        while time.monotonic()<until:
            if window.evaluate_js(expression): return
            time.sleep(.1)
        raise AssertionError('Timed out: '+expression)
    def exercise():
        try:
            assert window.events.loaded.wait(30)
            wait("document.getElementById('boot').hidden")
            window.evaluate_js("document.getElementById('scan').click()")
            wait('state.jobs.length===3 && !state.runtime.scanning')
            assert not account.sent
            text_job=next(j for j in api.snapshot()['jobs'] if j['kind']=='text')
            window.evaluate_js(f"document.querySelector('.content-card[data-id=\"{text_job['id']}\"]').click()")
            wait("document.getElementById('post-dialog').open")
            window.evaluate_js("document.getElementById('edit-title').value='درس ویرایش‌شده';document.getElementById('save-post').click()")
            wait("!document.getElementById('save-post').disabled")
            assert store.job(text_job['id'])['title']=='درس ویرایش‌شده'
            window.evaluate_js("document.getElementById('prepare-copy').click()")
            wait("!document.getElementById('prepare-copy').disabled")
            files=list((store.directory/'prepared').glob('*lesson-@example_channel.txt'))
            assert files and '@example_channel' in files[0].read_text(encoding='utf-8-sig')
            window.evaluate_js("document.querySelector('[data-close=\"post-dialog\"]').click();document.getElementById('toggle-run').click()")
            wait('state.settings.enabled && state.counts.scheduled===3')
            now[0]=min(j['scheduled'] for j in api.snapshot()['jobs'])
            assert engine.tick();window.evaluate_js('refresh()')
            wait('state.counts.sent===1')
            assert len(account.sent)==1
            assert 'Published:' in account.sent[0][1] and 'https://example.com' in account.sent[0][1]
            window.evaluate_js("document.getElementById('toggle-run').click()")
            wait('!state.settings.enabled')
            window.evaluate_js("document.querySelector('[data-page=\"settings\"]').click()")
            wait("!document.getElementById('account-connected').hidden && document.getElementById('account-name').textContent==='کاربر نمونه'")
            window.evaluate_js("document.getElementById('settings-form').elements.times.value='۱۰:۳۰, ۱۸:۴۵';document.querySelector('#settings-form button[type=submit]').click()")
            wait("state.settings.times.join(',')==='10:30,18:45'")
            assert not store.settings()['enabled']
            window.evaluate_js("document.getElementById('settings-form').elements.caption_format.value='custom';document.getElementById('settings-form').elements.caption_template.value='📄 {title}\\n{body}\\n{year} BY {brand}';document.querySelector('#settings-form button[type=submit]').click()")
            wait("state.settings.caption_format==='custom'")
            assert 'BY LionXSec' in api.detail(text_job['id'])['caption']
            assert (folder/'lesson.txt').read_text(encoding='utf-8').startswith('درس عکاسی')
            window.evaluate_js("document.querySelector('[data-page=\"library\"]').click();document.getElementById('clear-analysis').click()")
            wait("document.getElementById('clear-dialog').open")
            window.evaluate_js("document.getElementById('clear-confirm').click()")
            wait("state.counts.sent===1 && state.jobs.some(j=>j.error==='analysis_required')")
            window.evaluate_js("document.getElementById('scan').click()")
            wait("!state.runtime.scanning && state.counts.sent===1 && state.counts.draft===2")
            assert len(account.sent)==1
            window.evaluate_js("pywebview.api.save_openai_key('sk-native-fixture-not-a-real-key').then(()=>refresh())")
            wait('state.settings.openai_key_set')
            assert 'sk-native-fixture' not in json.dumps(api.snapshot())
            window.evaluate_js("pywebview.api.clear_openai_key().then(()=>refresh())")
            wait('!state.settings.openai_key_set')
            result.update(ok=True,renderer=backend.renderer,checks=['native startup','real JS bridge','folder scanning','SVB document accepted','caption edit','prepared TXT','time slots','simulated account send','pause','account card state','Persian time input','custom template persistence','clear and rescan preserving sent history','private OpenAI key bridge'])
        except Exception: result.update(ok=False,error=traceback.format_exc())
        finally: window.destroy()
    webview.start(exercise,gui=desktop_gui(),http_server=True,private_mode=True,storage_path=str(root/'webview'))
print(json.dumps(result,ensure_ascii=False))
raise SystemExit(0 if result.get('ok') else 1)
