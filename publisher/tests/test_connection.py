"""Real MTProto wrapper with Telethon's None-returning connection contract."""
from types import SimpleNamespace
import socket
import tempfile
import unittest
from engine import Engine
from storage import Store
from mtproto_client import MTProto,TelegramError,translate_error

class AsyncClient:
    def __init__(self): self.connected=False;self.calls=[];self.failure=None
    async def connect(self):
        self.calls.append('connect')
        if self.failure: raise self.failure
        self.connected=True
    def is_connected(self): return self.connected
    async def disconnect(self): self.connected=False
    async def send_code_request(self,phone):
        self.calls.append('code');return SimpleNamespace(phone_code_hash='synthetic-hash')

class ConnectionTests(unittest.TestCase):
    def wrapper(self,client): return MTProto(None,'123456','a'*32,client=client)

    def test_success_and_reconnect_with_none_return(self):
        client=AsyncClient();wrapper=self.wrapper(client)
        self.assertTrue(wrapper.connect());self.assertTrue(wrapper.connect())
        self.assertEqual(client.calls,['connect','connect']);wrapper.disconnect()

    def test_none_return_without_connection_is_not_success(self):
        client=AsyncClient();client.is_connected=lambda:False
        with self.assertRaisesRegex(TelegramError,'connection_failed'): self.wrapper(client).connect()

    def test_real_wrapper_allows_engine_to_request_login_code(self):
        with tempfile.TemporaryDirectory() as d:
            client=AsyncClient();store=Store(d)
            engine=Engine(store,mtproto_factory=lambda *args:self.wrapper(client))
            try:
                result=engine.login_start('123456','a'*32,'+989121234567')
                self.assertEqual(result['phone'],'+989121234567')
                self.assertEqual(client.calls,['connect','code'])
                self.assertEqual(engine._login['hash'],'synthetic-hash')
                self.assertFalse(store.has_session())
            finally: engine.close()

    def test_network_failure_allows_retry_without_stale_login(self):
        with tempfile.TemporaryDirectory() as d:
            client=AsyncClient();client.failure=TimeoutError('sensitive-network-details')
            engine=Engine(Store(d),mtproto_factory=lambda *args:self.wrapper(client))
            try:
                with self.assertRaisesRegex(TelegramError,'connection_timeout'):
                    engine.login_start('123456','a'*32,'+989121234567')
                self.assertIsNone(engine._login);self.assertNotIn('code',client.calls)
                client.failure=None
                engine.login_start('123456','a'*32,'+989121234567')
                self.assertIn('code',client.calls)
            finally: engine.close()

    def test_network_errors_have_secret_free_distinct_codes(self):
        for error,code in [(TimeoutError('secret'),'connection_timeout'),
                           (socket.gaierror('secret'),'connection_dns'),
                           (ConnectionRefusedError('secret'),'connection_refused'),
                           (OSError('secret'),'connection_failed')]:
            with self.subTest(code=code): self.assertEqual(str(translate_error(error)),code)
