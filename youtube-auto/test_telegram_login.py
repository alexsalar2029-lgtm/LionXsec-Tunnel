"""Exercise Telethon's login responses without contacting an account."""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from telethon import functions, types
from telethon.errors import PhoneCodeInvalidError, SessionPasswordNeededError

import telegram_downloader as tg


class TelegramLoginTests(unittest.TestCase):
    def setUp(self):
        self.client = AsyncMock()
        self.client.is_connected = MagicMock(return_value=True)
        self.client.is_user_authorized.side_effect = [False, True]
        self.messages = []
        client_patch = patch.object(tg.TelegramDownloader, "_client", return_value=self.client)
        client_patch.start()
        self.addCleanup(client_patch.stop)
        self.cfg = {"tg_api_id": "123", "tg_api_hash": "test", "tg_phone": "+۱ ۲۰۲ ۵۵۵ ۰۱۲۳"}

    def login(self, **kwargs):
        return tg.login_from_config(self.cfg, log=lambda message, level="INFO": self.messages.append(message), **kwargs)

    def test_bad_code_then_persian_digits_on_same_challenge(self):
        self.client.return_value = types.auth.SentCode(types.auth.SentCodeTypeApp(5), "fixture-request")
        self.client.sign_in.side_effect = [PhoneCodeInvalidError(None), None]
        prompt = MagicMock(side_effect=["11111", "۱ ٢-۳۴۵"])
        self.assertTrue(self.login(challenge_cb=prompt))
        self.assertEqual(self.client.await_count, 1)
        self.assertIsInstance(self.client.await_args.args[0], functions.auth.SendCodeRequest)
        self.assertEqual(self.client.await_args.args[0].phone_number, "+12025550123")
        self.assertEqual(self.client.sign_in.await_args.kwargs,
                         {"phone": "+12025550123", "code": "12345", "phone_code_hash": "fixture-request"})
        self.assertIn("دستگاه", prompt.call_args_list[0].args[0])
        self.assertIn("قبلی", prompt.call_args_list[1].args[0])
        self.assertNotIn("12345", "".join(self.messages))
        self.client.disconnect.assert_awaited_once()

    def test_resend_respects_server_timer_and_uses_replacement_hash(self):
        self.client.side_effect = [
            types.auth.SentCode(types.auth.SentCodeTypeApp(5), "old", types.auth.CodeTypeSms(), 30),
            types.auth.SentCode(types.auth.SentCodeTypeSms(5), "replacement"),
        ]
        clock = SimpleNamespace(monotonic=MagicMock(side_effect=[100, 105, 131, 132]))
        prompt = MagicMock(side_effect=["/resend", "/resend", "12345"])
        with patch.object(tg, "time", clock):
            self.assertTrue(self.login(challenge_cb=prompt))
        self.assertEqual(self.client.await_count, 2)
        resend = self.client.await_args_list[1].args[0]
        self.assertIsInstance(resend, functions.auth.ResendCodeRequest)
        self.assertEqual(resend.phone_code_hash, "old")
        self.assertEqual(self.client.sign_in.await_args.kwargs["phone_code_hash"], "replacement")
        self.assertIn("پیامک", prompt.call_args.args[0])

    def test_qr_replaces_expired_challenge_then_completes_two_step_login(self):
        shown, listening = [], []
        qr = SimpleNamespace(url="tg://login?token=local-test")
        async def wait():
            listening.append(True)
            await asyncio.sleep(0)
            if len(listening) == 1:
                raise asyncio.TimeoutError
            raise SessionPasswordNeededError(None)
        qr.wait, qr.recreate = wait, AsyncMock()
        self.client.qr_login.return_value = qr
        def display(url):
            if url:
                self.assertTrue(listening)
            shown.append(url)
        self.cfg["tg_phone"] = ""
        self.assertTrue(self.login(qr_cb=display, password_cb=lambda: "local-test-password"))
        self.assertEqual(shown, [qr.url, qr.url, None])
        qr.recreate.assert_awaited_once()
        self.client.assert_not_awaited()
        self.client.sign_in.assert_awaited_once_with(password="local-test-password")
        self.assertFalse(any("local-test" in line for line in self.messages))

    def test_cancel_qr_and_use_existing_grant(self):
        qr = SimpleNamespace(url="tg://login?token=local-test", wait=lambda: asyncio.sleep(20))
        self.client.qr_login.return_value = qr
        displayed = []
        self.assertFalse(self.login(qr_cb=displayed.append, cancelled=lambda: bool(displayed)))
        self.assertEqual(displayed, [qr.url, None])
        self.assertTrue(self.login(challenge_cb=lambda *_: self.fail("unexpected code request")))
        self.client.assert_not_awaited()
