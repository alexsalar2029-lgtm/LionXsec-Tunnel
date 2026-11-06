"""MTProto connection for the user's own Telegram account.

Login is an interactive three-step flow: app credentials (api_id/api_hash) plus phone
number, then the login code Telegram sends, then the optional two-step verification
password. The accepted session is stored by the caller as a StringSession string.

The Telethon client lives on a private asyncio loop inside a dedicated daemon thread,
because the desktop application is fully threaded and the rest of the engine is
synchronous. All failures surface as TelegramError with stable, secret-free codes so
the engine can reuse its existing retry and review handling.
"""
import asyncio
import concurrent.futures
import re
import threading

from telethon import TelegramClient
from telethon.errors import (ApiIdInvalidError,AuthKeyDuplicatedError,AuthKeyUnregisteredError,
    ChannelPrivateError,ChatAdminRequiredError,ChatWriteForbiddenError,FloodPremiumWaitError,
    FloodWaitError,PasswordHashInvalidError,PhoneCodeExpiredError,PhoneCodeInvalidError,
    PhoneNumberBannedError,PhoneNumberFloodError,PhoneNumberInvalidError,SessionPasswordNeededError,
    SessionRevokedError,SlowModeWaitError,UserBannedInChannelError,UserDeactivatedBanError)
from telethon.sessions import StringSession

APP_VERSION='1.2.2'
UPLOAD_TIMEOUT=900


class TelegramError(Exception):
    def __init__(self,code,retry_after=0,uncertain=False):
        self.code=code;self.retry_after=retry_after;self.uncertain=uncertain
        super().__init__(code)


def translate_error(error):
    """Map Telethon/network exceptions onto stable error codes without leaking secrets."""
    if isinstance(error,TelegramError): return error
    if isinstance(error,(asyncio.TimeoutError,TimeoutError,OSError,concurrent.futures.TimeoutError)):
        return TelegramError('connection_failed')
    if isinstance(error,(FloodWaitError,FloodPremiumWaitError,SlowModeWaitError,PhoneNumberFloodError)):
        return TelegramError('telegram_rate_limit',retry_after=max(1,int(getattr(error,'seconds',60))))
    if isinstance(error,PhoneCodeInvalidError): return TelegramError('login_code_invalid')
    if isinstance(error,PhoneCodeExpiredError): return TelegramError('login_code_expired')
    if isinstance(error,PasswordHashInvalidError): return TelegramError('login_password_invalid')
    if isinstance(error,ApiIdInvalidError): return TelegramError('api_credentials_invalid')
    if isinstance(error,PhoneNumberInvalidError): return TelegramError('phone_invalid')
    if isinstance(error,(PhoneNumberBannedError,UserDeactivatedBanError)): return TelegramError('account_banned')
    if isinstance(error,(SessionRevokedError,AuthKeyUnregisteredError,AuthKeyDuplicatedError)):
        return TelegramError('session_invalid')
    if isinstance(error,(ChannelPrivateError,ChatAdminRequiredError,ChatWriteForbiddenError,
                         UserBannedInChannelError)): return TelegramError('account_permission')
    return TelegramError('telegram_request')


class MTProto:
    """Synchronous façade over one Telethon client. Reused for login and publishing."""

    def __init__(self,session,api_id,api_hash,device_model='LIONXSEC Publisher',client=None):
        self._closed=False
        if client is not None:  # test seam: no loop, no network
            self._client=client;self._loop=None;self._thread=None;return
        self._loop=asyncio.new_event_loop()
        ready=threading.Event()
        self._thread=threading.Thread(target=self._serve,args=(ready,),name='lionxsec-mtproto',daemon=True)
        self._thread.start()
        if not ready.wait(10): raise TelegramError('telegram_unavailable')
        self._client=self.run(self._create(session,api_id,api_hash,device_model),timeout=30)

    def _serve(self,ready):
        asyncio.set_event_loop(self._loop);ready.set();self._loop.run_forever()

    async def _create(self,session,api_id,api_hash,device_model):
        return TelegramClient(StringSession(session or ''),int(api_id),str(api_hash),
                              device_model=device_model,app_version=APP_VERSION,
                              system_version='Desktop',lang_code='fa',system_lang_code='en')

    def run(self,collection,timeout=120):
        if self._loop is None: return asyncio.run(collection)  # injected test client
        future=asyncio.run_coroutine_threadsafe(collection,self._loop)
        try: return future.result(timeout)
        except (concurrent.futures.TimeoutError,asyncio.TimeoutError):
            future.cancel();raise TelegramError('connection_failed') from None

    def _run(self,collection,timeout=120):
        try: return self.run(collection,timeout)
        except TelegramError: raise
        except Exception as error: raise translate_error(error) from None

    # ----- login flow -----------------------------------------------------
    def connect(self,timeout=45):
        try: connected=self.run(self._client.connect(),timeout)
        except TelegramError: raise
        except Exception as error: raise translate_error(error) from None
        if not connected: raise TelegramError('connection_failed')

    def code_request(self,phone):
        sent=self._run(self._client.send_code_request(phone),timeout=60)
        return sent.phone_code_hash

    def sign_code(self,phone,code,phone_code_hash):
        try: return self.run(self._client.sign_in(phone=phone,code=code,phone_code_hash=phone_code_hash),timeout=60)
        except TelegramError: raise
        except Exception as error:
            if isinstance(error,SessionPasswordNeededError): return {'password_required':True}
            raise translate_error(error) from None

    def sign_password(self,password):
        try: return self.run(self._client.sign_in(password=password),timeout=60)
        except TelegramError: raise
        except Exception as error: raise translate_error(error) from None

    def me(self): return self._run(self._client.get_me(),timeout=30)

    async def _session_string(self): return str(self._client.session.save())
    def session_string(self): return self._run(self._session_string(),timeout=30)

    # ----- publishing -----------------------------------------------------
    def verify(self,channel):
        return self._run(self._verify(channel),timeout=180)

    async def _verify(self,channel):
        entity=await self._resolve(channel)
        if not (getattr(entity,'broadcast',False) or getattr(entity,'megagroup',False)):
            raise TelegramError('not_channel')
        if not getattr(entity,'creator',False):
            try: participant=await self._client.get_permissions(entity,await self._client.get_me())
            except Exception: raise TelegramError('account_permission') from None
            if getattr(entity,'broadcast',False):
                rights=getattr(participant,'admin_rights',None)
                if not (getattr(participant,'is_creator',False) or
                        (rights and getattr(rights,'post_messages',False))):
                    raise TelegramError('account_permission')
            elif getattr(participant,'is_banned',False) or getattr(participant,'has_left',False):
                raise TelegramError('account_permission')
        return {'account':account_label(await self._client.get_me()),
                'channel_title':getattr(entity,'title','') or '',
                'channel_id':str(getattr(entity,'id',''))}

    async def _resolve(self,channel):
        """Resolve @username or marked -100 id, populating the session cache from dialogs."""
        text=str(channel).strip()
        try: return await self._client.get_entity(text)
        except TelegramError: raise
        except ValueError: pass  # bare identifier without access hash
        except Exception as error: raise translate_error(error) from None
        numeric=int(text) if re.fullmatch(r'-?\d+',text) else None
        handle=text.lstrip('@').lower()
        target=None
        try:
            async for dialog in self._client.iter_dialogs(limit=500):
                if numeric is not None and dialog.id==numeric: target=dialog.entity;break
                username=getattr(getattr(dialog,'entity',None),'username',None)
                if handle and username and username.lower()==handle: target=dialog.entity;break
        except TelegramError: raise
        except Exception as error: raise translate_error(error) from None
        if target is None: raise TelegramError('not_channel')
        return target

    def send(self,job,caption,path,kind):
        message=self._run(self._deliver(job['channel'],caption,str(path),kind),timeout=UPLOAD_TIMEOUT)
        return {'message_id':int(message.id)}

    async def _deliver(self,channel,caption,path,kind):
        entity=await self._resolve(channel)
        if kind=='text':
            return await self._client.send_message(entity,caption,link_preview=False)
        options={'caption':caption,'force_document':kind=='document'}
        if kind=='video': options['supports_streaming']=True
        return await self._client.send_file(entity,path,**options)

    # ----- lifecycle ------------------------------------------------------
    def disconnect(self):
        if self._closed: return
        self._closed=True
        try: self.run(self._client.disconnect(),timeout=20)
        except Exception: pass
        if self._loop is not None:
            try: self._loop.call_soon_threadsafe(self._loop.stop)
            except Exception: pass


def account_label(me):
    """Stable display label for the logged-in account; never includes the phone number."""
    if not me: return ''
    username=getattr(me,'username','') or ''
    if username: return '@'+username
    name=' '.join(x for x in [getattr(me,'first_name',''),getattr(me,'last_name','')] if x)
    return name.strip() or 'Telegram account'


def normalize_api_id(value):
    text=str(value or '').translate(str.maketrans('۰۱۲۳۴۵۶۷۸۹','0123456789')).strip()
    if not re.fullmatch(r'\d{1,10}',text) or not 1<=int(text)<2**31: raise ValueError('api_id_invalid')
    return text


def normalize_api_hash(value):
    text=str(value or '').strip().lower()
    if not re.fullmatch(r'[0-9a-f]{32}',text): raise ValueError('api_hash_invalid')
    return text


def normalize_phone(value):
    text=str(value or '').translate(str.maketrans('۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩','0123456789'*2)).strip()
    text=re.sub(r'[\s\-().]','',text)
    if text.startswith('00'): text='+'+text[2:]
    elif text.startswith('+'): pass
    elif re.fullmatch(r'0\d{10}',text): text='+98'+text[1:]
    elif text.startswith('0') or not text.startswith('+'): text='+'+text
    if not re.fullmatch(r'\+\d{8,15}',text): raise ValueError('phone_invalid')
    return text
