"""Background intake and one-at-a-time, restart-safe scheduled publication."""
from datetime import datetime,timedelta
from pathlib import Path
import re
import hashlib
import threading
import time
from urllib.parse import urlparse
from zoneinfo import ZoneInfo,ZoneInfoNotFoundError

from content import KINDS,ContentError,analyze,caption,detect_kind,fingerprint,prepare,protected_content,thumbnail,validate_caption_template
from ai_client import check_openai,validate_key
from mtproto_client import MTProto,TelegramError,account_label,normalize_api_hash,normalize_api_id,normalize_phone
from storage import DEFAULTS,atomic_write


def validate_settings(data):
    if not isinstance(data,dict): raise ValueError('invalid_settings')
    value={**DEFAULTS,**{k:v for k,v in data.items() if k in DEFAULTS and k!='enabled'}}
    for key in ['folder','channel','tag','website','timezone','model','brand','ai_provider','openai_model',
                'account_name','account_username','account_phone']:
        if not isinstance(value[key],str): raise ValueError('invalid_settings')
        value[key]=value[key].strip()
    for key,limit in [('account_name',100),('account_username',32),('account_phone',16)]:
        value[key]=value[key][:limit]
    if value['folder'] and not Path(value['folder']).is_dir(): raise ValueError('folder_missing')
    if value['channel'] and not re.fullmatch(r'@[A-Za-z0-9_]{5,32}|-100\d{5,}',value['channel']): raise ValueError('channel_invalid')
    if value['tag'] and not re.fullmatch(r'@[A-Za-z0-9_]{5,32}|https://t\.me/[A-Za-z0-9_+/-]{1,150}',value['tag']): raise ValueError('tag_invalid')
    if value['website']:
        u=urlparse(value['website'])
        if u.scheme not in ('https','http') or not u.hostname or u.username or len(value['website'])>240: raise ValueError('website_invalid')
    if not re.fullmatch(r'[A-Za-z0-9._:/-]{1,100}',value['model']): raise ValueError('model_invalid')
    if value['ai_provider'] not in ('ollama','openai'): raise ValueError('invalid_settings')
    if not re.fullmatch(r'[A-Za-z0-9._-]{1,100}',value['openai_model']): raise ValueError('model_invalid')
    try: ZoneInfo(value['timezone'])
    except (ZoneInfoNotFoundError,ValueError): raise ValueError('timezone_invalid') from None
    times=value['times']
    if not isinstance(times,list) or not 1<=len(times)<=24 or any(not isinstance(t,str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',t) for t in times): raise ValueError('times_invalid')
    value['times']=sorted(set(times))
    for key in ['ai','watermark','recursive','auto_watch','content_restrictions']:
        if type(value[key]) is not bool: raise ValueError('invalid_settings')
    if value['text_placement'] not in ('header','footer'): raise ValueError('invalid_settings')
    if value['caption_format'] not in ('standard','custom'): raise ValueError('invalid_settings')
    if not 1<=len(value['brand'])<=60 or '\n' in value['brand'] or '\r' in value['brand']: raise ValueError('brand_invalid')
    if value['content_restrictions'] and protected_content(value['brand']): raise ValueError('sensitive_content')
    validate_caption_template(value['caption_template'],value['content_restrictions'])
    value['enabled']=False
    return value


def next_slot(settings,after,occupied=()):
    zone=ZoneInfo(settings['timezone']);start=datetime.fromtimestamp(after,zone)
    taken={int(x) for x in occupied}
    for day in range(3670):
        date=start.date()+timedelta(days=day)
        for text in settings['times']:
            hour,minute=map(int,text.split(':'))
            local=datetime(date.year,date.month,date.day,hour,minute,tzinfo=zone)
            timestamp=local.timestamp()
            # Reject nonexistent local times around a DST jump.
            if datetime.fromtimestamp(timestamp,zone).replace(tzinfo=None)!=local.replace(tzinfo=None): continue
            if timestamp>after and int(timestamp) not in taken: return timestamp
    raise ValueError('no_schedule_slot')


class Engine:
    def __init__(self,store,mtproto_factory=MTProto,analyzer=analyze,clock=time.time):
        self.store=store;self.mtproto_factory=mtproto_factory;self.analyzer=analyzer;self.clock=clock
        self.stop_event=threading.Event();self.lock=threading.RLock();self.scan_lock=threading.Lock()
        self.runtime={'scanning':False,'scanned':0,'total':0,'current':'','sending':False}
        self.last_scan=0;self.thread=None;self.revision=0
        self._account=None;self._login=None;self.login_lock=threading.Lock()

    def start_worker(self):
        self.thread=threading.Thread(target=self._loop,name='publisher',daemon=True);self.thread.start()

    def close(self):
        self.stop_event.set()
        try: self.store.update_settings({'enabled':False})
        except Exception: pass
        self.login_cancel();self.close_account()

    # ----- account connection and interactive login -----------------------
    def account_client(self):
        """Persistent MTProto client built from the stored session; created on demand."""
        with self.lock:
            if self._account is None:
                credentials=self.store.credentials()
                if not credentials['api_id'] or not credentials['api_hash'] or not self.store.has_session():
                    raise TelegramError('account_not_logged_in')
                self._account=self.mtproto_factory(self.store.session(),credentials['api_id'],credentials['api_hash'])
            return self._account

    def close_account(self):
        with self.lock: client,self._account=self._account,None
        if client:
            try: client.disconnect()
            except Exception: pass

    def login_start(self,api_id,api_hash,phone):
        identifier=normalize_api_id(api_id);digest=normalize_api_hash(api_hash);number=normalize_phone(phone)
        with self.login_lock:
            if self._login: raise ValueError('login_busy')
            client=self.mtproto_factory(None,identifier,digest)
            self._login={'client':client,'api_id':identifier,'api_hash':digest,'phone':number,'hash':None}
            try:
                client.connect()
                self._login['hash']=client.code_request(number)
            except BaseException:
                self._login=None
                try: client.disconnect()
                except Exception: pass
                raise
        self.store.event('کد ورود به حساب تلگرام ارسال شد.')
        return {'phone':number}

    def login_code(self,code):
        digits=re.sub(r'\D','',str(code).translate(str.maketrans('۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩','0123456789'*2)))
        if not 1<=len(digits)<=10: raise ValueError('login_code_invalid')
        with self.login_lock:
            flow=self._login
            if not flow or not flow['hash']: raise ValueError('login_not_started')
            me=flow['client'].sign_code(flow['phone'],digits,flow['hash'])
            if isinstance(me,dict) and me.get('password_required'): return {'password_required':True}
            self._finish_login(flow,me)
            return {'account':account_label(me)}

    def login_password(self,password):
        if not isinstance(password,str) or not password or len(password)>128: raise ValueError('login_password_invalid')
        with self.login_lock:
            flow=self._login
            if not flow: raise ValueError('login_not_started')
            me=flow['client'].sign_password(password)
            self._finish_login(flow,me)
            return {'account':account_label(me)}

    def _finish_login(self,flow,me):
        session=flow['client'].session_string()
        self.store.set_credentials(flow['api_id'],flow['api_hash'],flow['phone'])
        self.store.set_session(session)
        name=' '.join(x for x in [getattr(me,'first_name',''),getattr(me,'last_name','')] if x).strip()[:100]
        username=(getattr(me,'username','') or '')[:32]
        with self.lock:
            client=flow['client'];self._login=None
            if self._account is not None:
                previous,self._account=self._account,None
            else: previous=None
        try: client.disconnect()
        except Exception: pass
        if previous:
            try: previous.disconnect()
            except Exception: pass
        self.store.update_settings({'account_name':name,'account_username':username,'account_phone':flow['phone']})
        self.store.event('ورود با حساب تلگرام انجام شد؛ کانال مقصد را ذخیره و بررسی کن.')

    def login_cancel(self):
        with self.login_lock: flow,self._login=self._login,None
        if flow:
            try: flow['client'].disconnect()
            except Exception: pass

    def disconnect_account(self):
        self.close_account();self.login_cancel()
        self.store.clear_account()
        self.store.update_settings({'account_name':'','account_username':'','account_phone':'','enabled':False})
        self.store.event('از حساب تلگرام خارج شدی؛ برای انتشار دوباره وارد شو.')

    def _loop(self):
        while not self.stop_event.is_set():
            try:
                config=self.store.settings()
                if config['enabled']:
                    self.tick()
                    if config['auto_watch'] and self.clock()-self.last_scan>60 and not self.runtime['scanning']:
                        self.scan_async()
            except Exception:
                self.store.update_settings({'enabled':False});self.store.event('پردازش متوقف شد؛ تنظیمات و صف را بررسی کنید.','error')
            self.stop_event.wait(3)

    def save_settings(self,data):
        settings=validate_settings(data)
        if settings['folder'] and Path(settings['folder']).resolve().is_relative_to(self.store.directory.resolve()): raise ValueError('folder_in_app_data')
        with self.lock:
            self.revision+=1
            self.store.update_settings(settings)
            with self.store.db() as db:
                db.execute("UPDATE jobs SET status='draft',scheduled=NULL,channel='',error='' WHERE status='scheduled'")
        self.store.event('تنظیمات ذخیره شد. برای انتشار، زمان‌بندی را فعال کنید.')
        return settings

    def verify(self):
        config=self.store.settings()
        if not config['channel']: raise ValueError('channel_missing')
        client=self.account_client();client.connect()
        try: return client.verify(config['channel'])
        except TelegramError as error:
            if error.code=='session_invalid': self.close_account()
            raise

    def save_openai_key(self,key):
        key=validate_key(key)
        with self.lock:
            self.store.set_openai_key(key)
            self.revision+=1;self.store.update_settings({'enabled':False})
        return {'saved':True}

    def clear_openai_key(self):
        with self.lock:
            self.store.clear_openai_key()
            self.revision+=1;self.store.update_settings({'enabled':False})
        return {'saved':False}

    def test_ai(self):
        settings=self.store.settings()
        if settings['ai_provider']=='openai':
            return check_openai(self.store.openai_key(),settings['openai_model'])
        import requests
        try:
            result=requests.get('http://127.0.0.1:11434/api/tags',timeout=8);result.raise_for_status()
            models=[entry['name'] for entry in result.json().get('models',[])]
        except Exception: raise ValueError('ollama_unavailable') from None
        model=settings['model']
        if model not in models and model+':latest' not in models: raise ValueError('model_missing')
        return {'provider':'ollama','model':model}

    def _assign(self,db,config,now):
        occupied=[r[0] for r in db.execute("SELECT scheduled FROM jobs WHERE status='scheduled'")]
        rows=db.execute("SELECT id FROM jobs WHERE status='draft' AND error='' AND title<>'' ORDER BY id").fetchall()
        for row in rows:
            slot=next_slot(config,now,occupied);occupied.append(slot)
            db.execute("UPDATE jobs SET status='scheduled',scheduled=?,channel=?,updated=? WHERE id=?",(slot,config['channel'],now,row[0]))
        return len(rows)

    def enable(self):
        with self.lock:
            if self.runtime['scanning']: raise ValueError('scan_busy')
            revision=self.revision;config=self.store.settings()
        if not config['channel']: raise ValueError('channel_missing')
        self.verify()
        with self.lock:
            if revision!=self.revision: raise ValueError('changed_during_verification')
            config=self.store.settings()
            if not config['folder']: raise ValueError('folder_missing')
            now=self.clock()
            with self.store.db() as db:
                db.execute('BEGIN IMMEDIATE')
                db.execute("UPDATE jobs SET status='draft',scheduled=NULL,error='' WHERE status='scheduled'")
                count=self._assign(db,config,now)
            self.store.update_settings({'enabled':True})
        self.store.event('زمان‌بندی فعال شد؛ '+str(count)+' پست در صف است.')
        return count

    def pause(self):
        with self.lock:
            self.revision+=1;self.store.update_settings({'enabled':False})
        self.store.event('انتشارهای بعدی متوقف شدند. ارسال جاری، در صورت شروع، ممکن است کامل شود.')

    def scan_async(self,only_id=None,recheck=False):
        config=self.store.settings()
        if not config['folder'] or not Path(config['folder']).is_dir(): raise ValueError('folder_missing')
        if only_id is not None:
            prior=self.store.job(only_id)
            if not prior or prior['status'] in ('sent','sending','preparing','uncertain'): raise ValueError('job_locked')
        if not self.scan_lock.acquire(blocking=False): raise ValueError('scan_busy')
        with self.lock:
            if recheck or only_id is not None:
                self.revision+=1;self.store.update_settings({'enabled':False})
            self.runtime.update(scanning=True,scanned=0,total=0,current='')
        def work():
            try: self._scan(only_id,recheck=recheck)
            except Exception: self.store.event('خواندن پوشه کامل نشد؛ مسیر و دسترسی فایل‌ها را بررسی کنید.','error')
            finally:
                self.runtime.update(scanning=False,current='');self.last_scan=self.clock();self.scan_lock.release()
        threading.Thread(target=work,name='intake',daemon=True).start()

    def _scan(self,only_id=None,recheck=False):
        with self.lock: config=self.store.settings();scan_revision=self.revision
        folder=Path(config['folder'])
        if not config['folder'] or not folder.is_dir(): raise ValueError('folder_missing')
        if only_id is not None:
            prior=self.store.job(only_id)
            if not prior or prior['status'] in ('sent','sending','preparing','uncertain'): raise ValueError('job_locked')
            paths=[Path(prior['path'])]
        else:
            source=folder.rglob('*') if config['recursive'] else folder.iterdir()
            paths=sorted((p for p in source if p.is_file() and not p.is_symlink() and not p.name.endswith('.caption.txt') and not p.resolve().is_relative_to(self.store.directory.resolve()) and not any(part in ('.venv','.git') for part in p.parts)),key=lambda p:p.name.lower())
        self.runtime['total']=len(paths);added=0;checked=0;processed=set()
        for index,path in enumerate(paths):
            identifier=only_id
            if self.stop_event.is_set(): break
            self.runtime.update(scanned=index,current=path.name)
            kind=KINDS.get(path.suffix.lower(),'document')
            try:
                size=path.stat().st_size
                if not size: continue
                resolved=str(path.resolve());digest=hashlib.sha256((resolved+'\0'+fingerprint(path)).encode()).hexdigest();now=self.clock()
                with self.lock,self.store.db() as db:
                    if self.revision!=scan_revision: break
                    db.execute('BEGIN IMMEDIATE')
                    prior=(db.execute('SELECT * FROM jobs WHERE id=?',(only_id,)).fetchone() if only_id is not None
                           else db.execute('SELECT * FROM jobs WHERE path=? ORDER BY id DESC LIMIT 1',(resolved,)).fetchone())
                    if prior and prior['status'] in ('sending','preparing','uncertain'): continue
                    if prior and prior['status']=='sent':
                        if only_id is not None or prior['digest']==digest: continue
                        prior=None  # A changed file is a new publication; keep the sent history.
                    if prior and prior['status']=='excluded' and only_id is None: continue
                    if prior:
                        if prior['id'] in processed: continue
                        if not (only_id is not None or recheck or prior['digest']!=digest or prior['path']!=resolved or prior['error']=='analysis_required'): continue
                        identifier=prior['id']
                        db.execute("UPDATE jobs SET status='review',error='analyzing',path=?,digest=?,filename=?,kind=?,size=?,title='',body='',analysis='',content_type='',source_date='',scheduled=NULL,channel='',updated=? WHERE id=?",
                                   (resolved,digest,path.name,kind,size,now,identifier))
                    else:
                        cursor=db.execute("INSERT OR IGNORE INTO jobs(path,digest,filename,kind,size,status,error,created,updated) VALUES(?,?,?,?,?,'review','analyzing',?,?)",(str(path.resolve()),digest,path.name,kind,size,now,now))
                        if not cursor.rowcount: continue
                        identifier=cursor.lastrowid;added+=1
                processed.add(identifier);self._clear_outputs(identifier)
                try:
                    kind=detect_kind(path)
                    with self.store.db() as db: db.execute('UPDATE jobs SET kind=? WHERE id=?',(kind,identifier))
                    thumb=thumbnail(path,kind)
                    if thumb: atomic_write(self.store.directory/'thumbs'/f'{identifier}.jpg',thumb)
                    analysis_settings=dict(config)
                    if config['ai'] and config['ai_provider']=='openai':
                        analysis_settings['_openai_key']=self.store.openai_key()
                    result=self.analyzer(path,kind,analysis_settings)
                except ContentError as error:
                    result={'status':'review','title':path.stem[:100],'body':'','error':error.code,'analysis':''}
                with self.lock,self.store.db() as db:
                    if self.revision!=scan_revision:
                        result={'status':'review','title':'','body':'','error':'analysis_settings_changed','analysis':''}
                    db.execute("UPDATE jobs SET status=?,title=?,body=?,error=?,analysis=?,content_type=?,source_date=?,updated=? WHERE id=? AND status='review' AND error='analyzing'",
                               (result['status'],result['title'],result['body'],result['error'],result['analysis'],result.get('content_type',''),result.get('source_date',''),self.clock(),identifier))
                checked+=1
            except Exception as error:
                if identifier is not None:
                    code=error.code if isinstance(error,ContentError) else 'file_missing' if isinstance(error,FileNotFoundError) else 'analysis_failed'
                    with self.store.db() as db: db.execute("UPDATE jobs SET status='review',error=?,scheduled=NULL WHERE id=? AND status NOT IN ('sent','sending','preparing','uncertain') AND (error='analyzing' OR ?)", (code,identifier,only_id is not None))
            with self.lock:
                current=self.store.settings()
                if current['enabled']:
                    with self.store.db() as db:
                        db.execute('BEGIN IMMEDIATE');self._assign(db,current,self.clock())
        self.runtime['scanned']=len(paths)
        if checked: self.store.event(str(checked)+' فایل بررسی شد؛ '+str(added)+' فایل جدید بود.')

    def _clear_outputs(self,identifier):
        paths=[self.store.directory/'thumbs'/f'{identifier}.jpg']
        outputs=self.store.directory/'prepared'
        paths+=list(outputs.glob(f'{identifier}-*'))+[outputs/f'{identifier}.jpg',outputs/f'{identifier}.mp4']
        for path in paths:
            try: path.unlink(missing_ok=True)
            except OSError: pass

    def clear_analysis(self,identifier=None):
        if not self.scan_lock.acquire(blocking=False): raise ValueError('scan_busy')
        try:
            with self.lock,self.store.db() as db:
                db.execute('BEGIN IMMEDIATE')
                sql="SELECT id FROM jobs WHERE status NOT IN ('sent','sending','preparing','uncertain')"
                rows=db.execute(sql+' AND id=?',(int(identifier),)).fetchall() if identifier is not None else db.execute(sql).fetchall()
                if identifier is not None and not rows: raise ValueError('job_locked')
                self.revision+=1
                db.execute("INSERT OR REPLACE INTO settings VALUES('enabled','false')")
                for row in rows:
                    db.execute("UPDATE jobs SET title='',body='',analysis='',content_type='',source_date='',status='review',error='analysis_required',scheduled=NULL,channel='',updated=? WHERE id=?",(self.clock(),row['id']))
            for row in rows: self._clear_outputs(row['id'])
            self.store.event('نتیجهٔ بررسی '+str(len(rows))+' فایل پاک شد؛ برای تحلیل تازه، بررسی محتوا را بزنید.')
            return {'cleared':len(rows)}
        finally: self.scan_lock.release()

    def edit(self,identifier,title,body):
        if not isinstance(title,str) or not title.strip() or len(title)>100 or not isinstance(body,str) or len(body)>500: raise ValueError('caption_invalid')
        if self.store.settings().get('content_restrictions',True) and protected_content(title+'\n'+body): raise ValueError('sensitive_content')
        with self.lock,self.store.db() as db:
            row=db.execute('SELECT * FROM jobs WHERE id=?',(int(identifier),)).fetchone()
            if not row or row['status'] in ('sent','sending','preparing','blocked','uncertain') or row['error'] in ('analyzing','analysis_required'): raise ValueError('job_locked')
            db.execute("UPDATE jobs SET title=?,body=?,status='draft',error='',scheduled=NULL,updated=? WHERE id=?",(title.strip(),body.strip(),self.clock(),int(identifier)))

    def remove(self,identifier):
        with self.lock,self.store.db() as db:
            db.execute("UPDATE jobs SET status='excluded',scheduled=NULL WHERE id=? AND status NOT IN ('sending','preparing','sent','blocked','uncertain')",(int(identifier),))

    def acknowledge(self,identifier,action):
        if action not in ('sent','retry'): raise ValueError('invalid_action')
        with self.lock,self.store.db() as db:
            row=db.execute('SELECT * FROM jobs WHERE id=?',(int(identifier),)).fetchone()
            if not row or row['status']!='uncertain': raise ValueError('job_locked')
            if action=='sent': db.execute("UPDATE jobs SET status='sent',sent=?,error='' WHERE id=?",(self.clock(),int(identifier)))
            else: db.execute("UPDATE jobs SET status='draft',error='',scheduled=NULL WHERE id=?",(int(identifier),))

    def tick(self):
        now=self.clock()
        with self.lock,self.store.db() as db:
            config=self.store.settings()
            revision=self.revision
            if not config['enabled']: return False
            db.execute('BEGIN IMMEDIATE')
            db.execute("UPDATE jobs SET status='draft',scheduled=NULL,error='' WHERE status='scheduled' AND scheduled<?",(now-300,))
            self._assign(db,config,now)
            row=db.execute("SELECT * FROM jobs WHERE status='scheduled' AND scheduled<=? ORDER BY scheduled,id LIMIT 1",(now,)).fetchone()
            if not row: return False
            job=dict(row);db.execute("UPDATE jobs SET status='preparing',updated=? WHERE id=?",(now,job['id']))
        self.runtime['sending']=True
        try:
            path,kind=prepare(job,config,self.store.directory/'prepared')
            with self.lock:
                if not self.store.settings()['enabled'] or self.stop_event.is_set() or revision!=self.revision:
                    with self.store.db() as db: db.execute("UPDATE jobs SET status='draft',scheduled=NULL WHERE id=?",(job['id'],))
                    return True
                text=caption(job,config,self.clock())
                atomic_write(self.store.directory/'prepared'/f"{job['id']}-caption.txt",text.encode('utf-8-sig'))
                with self.store.db() as db: db.execute("UPDATE jobs SET status='sending',updated=? WHERE id=?",(self.clock(),job['id']))
            try:
                sender=self.account_client();sender.connect()
                result=sender.send(job,text,path,kind)
            except TelegramError as error: raise
            except Exception as error: raise TelegramError('delivery_unknown',uncertain=True) from None
            with self.store.db() as db:
                db.execute("UPDATE jobs SET status='sent',sent=?,message_id=?,error='',updated=? WHERE id=?",(self.clock(),result.get('message_id'),self.clock(),job['id']))
            self.store.event('پست شمارهٔ '+str(job['id'])+' با موفقیت منتشر شد.')
        except ContentError as error:
            with self.store.db() as db: db.execute("UPDATE jobs SET status=?,error=?,updated=? WHERE id=?",('blocked' if error.code.startswith('sensitive_') else 'review',error.code,self.clock(),job['id']))
        except TelegramError as error:
            attempts=job['attempts']+1
            if error.uncertain: status='uncertain'
            elif (error.retry_after or error.code=='connection_failed') and attempts<4: status='scheduled'
            else: status='failed'
            if error.code in ('session_invalid','account_not_logged_in'): self.close_account()
            with self.store.db() as db:
                occupied=[r[0] for r in db.execute("SELECT scheduled FROM jobs WHERE status='scheduled'")]
                slot=next_slot(config,self.clock()+error.retry_after,occupied) if status=='scheduled' else None
                db.execute('UPDATE jobs SET status=?,error=?,attempts=?,scheduled=?,updated=? WHERE id=?',(status,error.code,attempts,slot,self.clock(),job['id']))
            self.store.event('ارسال پست شمارهٔ '+str(job['id'])+' نیاز به بررسی دارد.','warning')
        except Exception:
            with self.store.db() as db:
                current=db.execute('SELECT status FROM jobs WHERE id=?',(job['id'],)).fetchone()[0]
                db.execute('UPDATE jobs SET status=?,error=? WHERE id=?',('uncertain' if current=='sending' else 'failed','delivery_unknown' if current=='sending' else 'prepare_failed',job['id']))
        finally: self.runtime['sending']=False
        return True
