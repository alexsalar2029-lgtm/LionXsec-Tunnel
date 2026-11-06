"""Record setup, Python import errors and native crashes before the app opens."""
from datetime import datetime
from pathlib import Path
import os
import platform
import subprocess
import sys
import traceback

ROOT = Path(__file__).resolve().parent
LOG_FILE = ROOT / 'startup-error.log'


def run_logged(command, log):
    environment = dict(os.environ)
    environment['PYTHONUTF8'] = '1'
    environment['PYTHONIOENCODING'] = 'utf-8'
    with subprocess.Popen(command, cwd=ROOT, env=environment,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          text=True, encoding='utf-8', errors='replace', bufsize=1) as process:
        for line in process.stdout:
            log.write(line)
            log.flush()
            try:
                print(line, end='', flush=True)
            except UnicodeEncodeError:
                encoding = sys.stdout.encoding or 'ascii'
                print(line.encode(encoding, errors='replace').decode(encoding), end='', flush=True)
        code = process.wait()
    log.write(f'Process exit code: {code} (0x{code & 0xffffffff:08X})\n')
    log.flush()
    return code


def main():
    try:
        with LOG_FILE.open('a', encoding='utf-8') as log:
            log.write(f'\n--- Launch {datetime.now().isoformat(timespec="seconds")} ---\n')
            log.write(f'OS: {platform.system()} {platform.release()} {platform.version()}\n')
            log.write(f'Python: {sys.version.split()[0]}, {64 if sys.maxsize > 2**32 else 32}-bit\n')
            log.flush()
            try:
                code = run_logged([sys.executable, '-u', str(ROOT / 'setup_desktop.py')], log)
                if not code:
                    python = ROOT / '.venv' / ('Scripts/python.exe' if sys.platform == 'win32' else 'bin/python')
                    code = run_logged([str(python), '-u', str(ROOT / 'app.py')], log)
            except Exception:
                traceback.print_exc(file=log)
                traceback.print_exc()
                code = 1
    except OSError as error:
        print(f'Cannot write startup-error.log: {error}', flush=True)
        print('Extract the entire ZIP into a writable folder and run START-LIONXSEC.cmd again.', flush=True)
        return 1
    if code:
        print(f'Startup failed. Full details: {LOG_FILE}', flush=True)
    return 1 if code else 0


if __name__ == '__main__':
    raise SystemExit(main())
