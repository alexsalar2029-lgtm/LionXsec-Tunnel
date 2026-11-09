"""Local persistence, protected account secrets and serialized job state."""
import base64
import hashlib
import ctypes
from ctypes import wintypes
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time

DEFAULTS = {'folder':'','channel':'','tag':'','website':'','times':['09:00','14:00','20:00'],
            'timezone':'Asia/Tehran','ai':True,'model':'qwen3-vl:4b','watermark':False,
            'recursive':False,'auto_watch':True,'text_placement':'footer','enabled':False,
            'caption_format':'standard','caption_template':'📄 {title}\n\n{body}\n\n{year} BY {brand} 🦁','brand':'LionXsec',
            'account_name':'','account_username':'','account_phone':'',
            'content_restrictions':True,'ai_provider':'ollama','openai_model':'gpt-4.1-mini'}


def data_directory():
    if os.name == 'nt':
        return Path(os.environ.get('LOCALAPPDATA',Path.home()/'AppData/Local'))/'LIONXSEC/Publisher'
    return Path(os.environ.get('XDG_DATA_HOME',Path.home()/'.local/share'))/'lionxsec-publisher'


def atomic_write(path, data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temp=None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent,prefix='.'+path.name,delete=False) as out:
            temp=Path(out.name);out.write(data);out.flush();os.fsync(out.fileno())
        os.replace(temp,path)
    finally:
        if temp: temp.unlink(missing_ok=True)


class _Blob(ctypes.Structure):
    _fields_=[('cbData',wintypes.DWORD),('pbData',ctypes.POINTER(ctypes.c_ubyte))]


def _dpapi(data, decrypt=False):
    buffer=ctypes.create_string_buffer(data)
    source=_Blob(len(data),ctypes.cast(buffer,ctypes.POINTER(ctypes.c_ubyte)))
    target=_Blob()
    crypt=ctypes.WinDLL('crypt32',use_last_error=True)
    pointer=ctypes.POINTER(_Blob)
    crypt.CryptProtectData.argtypes=[pointer,wintypes.LPCWSTR,pointer,ctypes.c_void_p,ctypes.c_void_p,wintypes.DWORD,pointer]
    crypt.CryptProtectData.restype=wintypes.BOOL
    crypt.CryptUnprotectData.argtypes=[pointer,ctypes.POINTER(wintypes.LPWSTR),pointer,ctypes.c_void_p,ctypes.c_void_p,wintypes.DWORD,pointer]
    crypt.CryptUnprotectData.restype=wintypes.BOOL
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.LocalFree.argtypes=[ctypes.c_void_p];kernel.LocalFree.restype=ctypes.c_void_p
    if decrypt:
        ok=crypt.CryptUnprotectData(ctypes.byref(source),None,None,None,None,1,ctypes.byref(target))
    else:
        ok=crypt.CryptProtectData(ctypes.byref(source),'LIONXSEC Publisher',None,None,None,1,ctypes.byref(target))
    if not ok: raise OSError('token_protection_failed')
    try: return ctypes.string_at(target.pbData,target.cbData)
    finally: kernel.LocalFree(target.pbData)


class Store:
    def __init__(self, directory=None):
        self.directory=Path(directory) if directory else data_directory()
        self.directory.mkdir(parents=True,exist_ok=True)
        self.path=self.directory/'publisher.sqlite3'
        self.session_path=self.directory/'account-session.dat'
        self.credentials_path=self.directory/'api-credentials.dat'
        self.openai_key_path=self.directory/'openai-key.dat'
        (self.directory/'prepared').mkdir(exist_ok=True)
        (self.directory/'thumbs').mkdir(exist_ok=True)
        with self.db() as db:
            db.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS jobs(
              id INTEGER PRIMARY KEY, path TEXT NOT NULL,digest TEXT NOT NULL UNIQUE,filename TEXT NOT NULL,
              kind TEXT NOT NULL,size INTEGER NOT NULL,title TEXT NOT NULL DEFAULT '',body TEXT NOT NULL DEFAULT '',
              status TEXT NOT NULL DEFAULT 'draft',scheduled REAL,channel TEXT NOT NULL DEFAULT '',
              sent REAL,message_id INTEGER,attempts INTEGER NOT NULL DEFAULT 0,error TEXT NOT NULL DEFAULT '',
              analysis TEXT NOT NULL DEFAULT '',created REAL NOT NULL,updated REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY,at REAL NOT NULL,level TEXT NOT NULL,text TEXT NOT NULL);
            ''')
            if {'path','digest'} <= {r['name'] for r in db.execute('PRAGMA table_info(jobs)')} and not db.execute("SELECT 1 FROM settings WHERE key='path_digest_v1'").fetchone():
                for row in db.execute('SELECT id,path,digest FROM jobs').fetchall():
                    digest=hashlib.sha256((row['path']+'\0'+row['digest']).encode()).hexdigest()
                    db.execute('UPDATE jobs SET digest=? WHERE id=?',(digest,row['id']))
                db.execute("INSERT INTO settings VALUES('path_digest_v1','true')")
            columns={row['name'] for row in db.execute('PRAGMA table_info(jobs)')}
            for name in ('content_type','source_date'):
                if name not in columns:
                    db.execute(f"ALTER TABLE jobs ADD COLUMN {name} TEXT NOT NULL DEFAULT ''")

    @contextmanager
    def db(self):
        con=sqlite3.connect(self.path,timeout=20);con.row_factory=sqlite3.Row
        try:
            yield con;con.commit()
        except BaseException:
            con.rollback();raise
        finally: con.close()

    def settings(self):
        result=dict(DEFAULTS)
        with self.db() as db:
            for row in db.execute('SELECT key,value FROM settings'):
                if row['key'] in DEFAULTS: result[row['key']]=json.loads(row['value'])
        return result

    def update_settings(self, values):
        with self.db() as db:
            for key,value in values.items():
                if key in DEFAULTS:
                    db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',(key,json.dumps(value)))

    def secret(self, path):
        """Read a DPAPI-protected (or locally chmod'ed) secret file."""
        path=Path(path)
        if not path.exists(): return ''
        value=path.read_bytes()
        if value.startswith(b'DP1:'):
            if os.name!='nt': raise OSError('secret_requires_windows_account')
            return _dpapi(value[4:],True).decode()
        return value.removeprefix(b'LOCAL:').decode()

    def set_secret(self, path, value):
        data=b'DP1:'+_dpapi(value.encode()) if os.name=='nt' else b'LOCAL:'+value.encode()
        atomic_write(path,data)
        if os.name!='nt': Path(path).chmod(0o600)

    def session(self): return self.secret(self.session_path)
    def has_session(self): return self.session_path.exists()
    def set_session(self, value): self.set_secret(self.session_path,value)

    def openai_key(self): return self.secret(self.openai_key_path)
    def has_openai_key(self): return self.openai_key_path.exists()
    def set_openai_key(self, value): self.set_secret(self.openai_key_path,value)
    def clear_openai_key(self): self.openai_key_path.unlink(missing_ok=True)

    def credentials(self):
        raw=self.secret(self.credentials_path)
        try: data=json.loads(raw) if raw else {}
        except ValueError: data={}
        return {'api_id':str(data.get('api_id','')),'api_hash':str(data.get('api_hash','')),
                'phone':str(data.get('phone',''))}

    def set_credentials(self, api_id, api_hash, phone):
        payload=json.dumps({'api_id':str(api_id),'api_hash':str(api_hash),'phone':str(phone)})
        self.set_secret(self.credentials_path,payload)

    def clear_account(self):
        self.session_path.unlink(missing_ok=True)
        self.credentials_path.unlink(missing_ok=True)

    def event(self, message, level='info'):
        with self.db() as db:
            db.execute('INSERT INTO events(at,level,text) VALUES(?,?,?)',(time.time(),level,message[:300]))
            db.execute('DELETE FROM events WHERE id NOT IN(SELECT id FROM events ORDER BY id DESC LIMIT 200)')

    def job(self, identifier):
        with self.db() as db:
            row=db.execute('SELECT * FROM jobs WHERE id=?',(int(identifier),)).fetchone()
            return dict(row) if row else None

    def recover(self):
        with self.db() as db:
            db.execute("UPDATE jobs SET status='uncertain',error='delivery_unknown',updated=? WHERE status='sending'",(time.time(),))
            db.execute("UPDATE jobs SET status='draft',error='interrupted',updated=? WHERE status='preparing'",(time.time(),))
            db.execute("UPDATE jobs SET status='review',error='interrupted',updated=? WHERE error='analyzing'",(time.time(),))
        self.update_settings({'enabled':False})


class SingleInstance:
    def __init__(self, directory):
        self.file=open(Path(directory)/'instance.lock','a+b')
        self.file.seek(0);self.file.write(b'1');self.file.flush();self.file.seek(0)
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(self.file.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(self.file,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            self.file.close();raise RuntimeError('already_running') from None

    def close(self): self.file.close()
