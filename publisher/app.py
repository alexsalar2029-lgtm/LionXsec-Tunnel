"""LIONXSEC Publisher native desktop entry point."""
import base64
from datetime import datetime
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback

from content import caption,prepare,ContentError
from ai_client import AIError
from desktop_runtime import desktop_gui,initialize_desktop,software_environment
from engine import Engine
from mtproto_client import TelegramError
from storage import Store,SingleInstance

ROOT=Path(__file__).resolve().parent
LOG_FILE=ROOT/'startup-error.log'

# Same imports pywebview's qt backend performs; run step by step so the
# exact failing module is visible instead of a generic renderer error.
DESKTOP_PROBE=(
    ('shiboken6 core','import shiboken6; result=getattr(shiboken6,\'__version__\',\'?\')'),
    ('PySide6 QtCore','from PySide6 import QtCore; result=\'Qt \'+QtCore.qVersion()'),
    ('PySide6 QtWidgets','from PySide6 import QtWidgets; result=\'importable\''),
    ('PySide6 QtWebEngine','from PySide6 import QtWebEngineWidgets; result=\'importable\''),
    ('QtPy binding','import qtpy; result=qtpy.API_NAME'),
    ("pywebview qt backend","from webview.guilib import initialize as _init; _b=_init('qt'); result='renderer='+str(getattr(_b,'renderer','?'))"),
)
WINDOWS_PROBE=(
    ('Python .NET bridge','import clr; result="importable"'),
    ('.NET Windows Forms','import clr; clr.AddReference("System.Windows.Forms"); result="importable"'),
    ('WebView2 renderer','from desktop_runtime import initialize_desktop; result="renderer="+initialize_desktop().renderer'),
)


def log_startup(stage):
    try:
        with open(LOG_FILE,'a',encoding='utf-8') as log:
            log.write(f'\n[{datetime.now().isoformat(timespec="seconds")}] startup failed at: {stage}\n')
            log.write(traceback.format_exc())
    except OSError: pass


def diagnose_desktop():
    for title,statement in (WINDOWS_PROBE if desktop_gui()=='edgechromium' else DESKTOP_PROBE):
        box={}
        try: exec(statement,box);detail=str(box.get('result',''))
        except Exception as error: detail=f'{type(error).__name__}: {error}'
        print(('OK   ' if 'result' in box else 'FAIL ')+title+(f' ({detail})' if detail else ''),flush=True)


def desktop_hints(error):
    text=f'{type(error).__name__}: {error}'.lower();hints=[]
    if desktop_gui()=='edgechromium':
        hints.append('Install Microsoft Edge WebView2 Evergreen Runtime (x64): https://developer.microsoft.com/microsoft-edge/webview2/#download')
        hints.append('Use .NET Framework 4.6.2 or later and Windows Server with Desktop Experience, in an interactive desktop session.')
    if not str(ROOT).isascii(): hints.append('The program folder path contains non-Latin characters. Extract the ZIP to a simple path such as C:\\LIONXSEC and run it again.')
    if 'dll load failed' in text or 'shiboken' in text: hints.append('Install the Microsoft Visual C++ 2015-2022 Redistributable (x64) from https://aka.ms/vs/17/release/vc_redist.x64.exe and check that antivirus did not quarantine PySide6 files.')
    if any(word in text for word in ('gpu','dxgi','d3d','angle','egl','glx','vulkan')): hints.append('Start without GPU acceleration: run START-LIONXSEC-SOFTWARE.cmd.')
    if 'platform plugin' in text: hints.append('Another Qt copy (for example Anaconda) on PATH can conflict. Close other programs or clean PATH and try again.')
    return hints


class API:
    def __init__(self,store,engine): self._store=store;self._engine=engine;self._window=None

    def _call(self,function,*args):
        try: return {'ok':True,'value':function(*args)}
        except (ValueError,ContentError,TelegramError,AIError) as error: return {'ok':False,'error':getattr(error,'code',str(error))}
        except Exception: return {'ok':False,'error':'operation_failed'}

    def snapshot(self):
        settings=self._store.settings()
        settings['account_set']=self._store.has_session()
        settings['openai_key_set']=self._store.has_openai_key()
        with self._store.db() as db:
            jobs=[dict(row) for row in db.execute('SELECT * FROM jobs ORDER BY id DESC LIMIT 500')]
            counts=dict(db.execute('SELECT status,count(*) FROM jobs GROUP BY status').fetchall())
            events=[dict(row) for row in db.execute('SELECT * FROM events ORDER BY id DESC LIMIT 20')]
        return {'settings':settings,'jobs':jobs,'counts':counts,'events':events,'runtime':dict(self._engine.runtime),'now':time.time()}

    def save_settings(self,data): return self._call(self._engine.save_settings,data)
    def verify(self): return self._call(self._engine.verify)
    def login_start(self,api_id,api_hash,phone): return self._call(self._engine.login_start,api_id,api_hash,phone)
    def login_code(self,code): return self._call(self._engine.login_code,code)
    def login_password(self,password): return self._call(self._engine.login_password,password)
    def login_cancel(self): return self._call(self._engine.login_cancel)
    def disconnect_account(self): return self._call(self._engine.disconnect_account)
    def scan(self): return self._call(self._engine.scan_async,None,True)
    def clear_analysis(self,identifier=None): return self._call(self._engine.clear_analysis,identifier)
    def save_openai_key(self,key): return self._call(self._engine.save_openai_key,key)
    def clear_openai_key(self): return self._call(self._engine.clear_openai_key)
    def enable(self): return self._call(self._engine.enable)
    def pause(self): return self._call(self._engine.pause)
    def edit(self,identifier,title,body): return self._call(self._engine.edit,identifier,title,body)
    def remove(self,identifier): return self._call(self._engine.remove,identifier)
    def reanalyze(self,identifier): return self._call(self._engine.scan_async,int(identifier))
    def acknowledge(self,identifier,action): return self._call(self._engine.acknowledge,identifier,action)

    def choose_folder(self):
        import webview
        choice=self._window.create_file_dialog(webview.FileDialog.FOLDER)
        return choice[0] if choice else ''

    def thumbnail(self,identifier):
        path=self._store.directory/'thumbs'/f'{int(identifier)}.jpg'
        return 'data:image/jpeg;base64,'+base64.b64encode(path.read_bytes()).decode() if path.exists() else ''

    def detail(self,identifier):
        job=self._store.job(identifier)
        if not job: return {'ok':False,'error':'job_missing'}
        return {'ok':True,'job':job,'caption':caption(job,self._store.settings(),job['scheduled'] or time.time())}

    def preview_caption(self,identifier,title,body):
        def build():
            job=self._store.job(identifier)
            if not job: raise ValueError('job_missing')
            if not isinstance(title,str) or not isinstance(body,str) or len(title)>100 or len(body)>500:
                raise ValueError('caption_invalid')
            return caption({**job,'title':title,'body':body},self._store.settings(),job['scheduled'] or time.time())
        return self._call(build)

    def prepare_copy(self,identifier):
        def build():
            job=self._store.job(identifier)
            if not job or job['status'] in ('blocked','sending','preparing') or job['error'] in ('analyzing','analysis_required'): raise ValueError('job_locked')
            path,kind=prepare(job,self._store.settings(),self._store.directory/'prepared')
            return {'filename':path.name,'kind':kind}
        return self._call(build)

    def open_outputs(self):
        folder=str(self._store.directory/'prepared')
        if sys.platform=='win32': os.startfile(folder)
        elif sys.platform=='darwin': subprocess.Popen(['open',folder])
        else: subprocess.Popen(['xdg-open',folder],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        return {'ok':True}

    def test_ai(self): return self._call(self._engine.test_ai)


def main():
    try: import webview
    except ImportError:
        print('Run START-LIONXSEC.cmd to install dependencies.');return 1
    try: initialize_desktop()
    except Exception as error:
        print(f'Startup error ({type(error).__name__}): {error}',flush=True)
        diagnose_desktop();log_startup('desktop renderer initialization')
        print('The desktop renderer is missing or could not start. Details are above and in startup-error.log next to START-LIONXSEC.cmd.')
        print('If you see a graphics driver error, use START-LIONXSEC-SOFTWARE.cmd.')
        for hint in desktop_hints(error): print('Hint:',hint,flush=True)
        return 1
    store=Store()
    try: instance=SingleInstance(store.directory)
    except RuntimeError:
        print('LIONXSEC Publisher is already running. Open its existing window.');return 1
    store.recover();engine=Engine(store);api=API(store,engine)
    webview.settings['OPEN_EXTERNAL_LINKS_IN_BROWSER']=False
    api._window=webview.create_window('LIONXSEC | Publisher Studio',str(ROOT/'ui/index.html'),
                                     js_api=api,width=1440,height=940,min_size=(900,650),
                                     background_color='#0b0c0f',text_select=True)
    api._window.events.closed+=engine.close
    engine.start_worker()
    try:
        webview.start(gui=desktop_gui(),http_server=True,
                      private_mode=False,storage_path=str(store.directory/'webview'))
    except Exception as error:
        engine.close();instance.close()
        if os.environ.get('LIONXSEC_SOFTWARE_RETRY')!='1':
            print('Retrying once with GPU acceleration disabled...',flush=True)
            retry=software_environment()
            result=subprocess.run([sys.executable,os.path.abspath(__file__)],env=retry)
            return result.returncode or 0
        log_startup('desktop window')
        print('Desktop window could not start. Details are in startup-error.log next to START-LIONXSEC.cmd.')
        print('Use START-LIONXSEC-SOFTWARE.cmd or reinstall through START-LIONXSEC.cmd.')
        for hint in desktop_hints(error): print('Hint:',hint,flush=True)
        return 1
    finally: engine.close();instance.close()
    return 0


if __name__=='__main__': raise SystemExit(main())
