from pathlib import Path
import hashlib
import subprocess
import sys
import venv

from windows_prerequisites import ensure_windows_runtime

ROOT=Path(__file__).resolve().parent
# cryptg is an optional accelerator; the app must still start without it.
HARD_IMPORTS='import webview,requests,PIL,imageio_ffmpeg,jdatetime,pypdf,telethon'
HARD_IMPORTS += '; import importlib.util; assert importlib.util.find_spec("clr") is not None' if sys.platform=='win32' else '; import qtpy; from PySide6 import QtWebEngineWidgets'


def pip_install(python,args):
    return subprocess.call([str(python),'-m','pip','install','--disable-pip-version-check',*args])


def main():
    if not (3,10)<=sys.version_info[:2]<(3,14):
        print('Install Python 3.10 to 3.13 (64-bit), then run START-LIONXSEC.cmd.');return 1
    if sys.maxsize<=2**32:
        print('Install the 64-bit version of Python.');return 1
    if sys.platform=='win32' and sys.getwindowsversion().major<10:
        print('Windows 10/11 or Server 2016+ with Desktop Experience (64-bit) is required.');return 1
    environment=ROOT/'.venv';python=environment/('Scripts/python.exe' if sys.platform=='win32' else 'bin/python')
    if not python.exists():
        print('Creating the local environment...',flush=True);venv.create(environment,with_pip=True)
    digest=hashlib.sha256((ROOT/'requirements.txt').read_bytes()).hexdigest();marker=environment/'.requirements-sha256'
    installed=marker.exists() and marker.read_text()==digest
    if installed:
        installed=subprocess.run([str(python),'-c',HARD_IMPORTS],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode==0
    if not installed:
        print('Installing dependencies. Keep your Internet connection on for this first setup.',flush=True)
        requirements=[line.strip() for line in (ROOT/'requirements.txt').read_text(encoding='utf-8').splitlines() if line.strip() and not line.strip().startswith('#')]
        code=pip_install(python,['-r',str(ROOT/'requirements.txt')])
        if code:
            print('Retrying without the optional cryptg speed-up package...',flush=True)
            code=pip_install(python,[line for line in requirements if not line.lower().startswith('cryptg')])
            if not code:
                accelerator='cryptg==0.6.0' if sys.version_info>=(3,11) else 'cryptg==0.5.2'
                if pip_install(python,[accelerator]):
                    print('Note: the optional cryptg speed-up was skipped; publishing still works, just a little slower.',flush=True)
        if code: return code
        marker.write_text(digest)
    if sys.platform=='win32':
        if ensure_windows_runtime():
            return 1
        check=subprocess.run([str(python),'-c','from desktop_runtime import initialize_desktop; initialize_desktop()'],cwd=ROOT)
        if check.returncode:
            print('WebView2 and .NET were detected, but Windows desktop initialization still failed.',flush=True)
            print('Use a logged-in Windows desktop (not Server Core) and read the exact exception above.',flush=True)
            print('Read startup-error.log for the complete error, then run START-LIONXSEC.cmd again.',flush=True)
            return 1
    print('Opening LIONXSEC Publisher...',flush=True);return 0


if __name__=='__main__': raise SystemExit(main())
