#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
telegram_downloader.py — pull videos from a Telegram channel into the uploader's
video folder so they can be auto-posted to YouTube.

Design notes
------------
* Uses Telethon (MTProto user client). The user logs in ONCE with their own
  phone number + the login code Telegram sends them; the session is remembered
  in ``tg_session.session`` next to this script, so later downloads are silent.
* The channel can be public (``@name`` / ``https://t.me/name``) or one the
  logged-in account has already joined (private invite links are best-effort
  joined automatically).
* A de-dup log (``telegram_downloaded.log``) records ``channel:message_id`` so a
  video is never downloaded twice; the YouTube side keeps its own
  ``uploaded_videos.log`` so nothing is re-uploaded either.
* Everything is anchored to THIS script's directory so it works no matter which
  working directory the app is launched from (mirrors the yt_uploader fix).

This module is import-safe even when Telethon is not installed: callers should
check :data:`HAS_TELETHON` and show :data:`TELETHON_HINT` if it is False.
"""

from __future__ import annotations

import os
import re
import sys
import asyncio
import json
from pathlib import Path
import tempfile

# cp1256 (Persian Windows console) cannot encode the emoji/arrows used in logs.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Anchor session + de-dup log to this script's folder (CWD-independent).
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TG_SESSION = os.path.join(_BASE_DIR, "tg_session")          # Telethon -> tg_session.session
TG_DL_LOG = os.path.join(_BASE_DIR, "telegram_downloaded.log")

VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv", ".m4v", ".ts", ".3gp", ".mpg", ".mpeg"}

TELETHON_HINT = "The 'telethon' package is required for Telegram downloads.  Install it with:  pip install telethon"

try:
    from telethon import TelegramClient
    from telethon.errors import (
        SessionPasswordNeededError,
        PhoneCodeInvalidError,
        PhoneCodeExpiredError,
        FloodWaitError,
        PhoneNumberInvalidError,
        ApiIdInvalidError,
    )
    from telethon.tl.types import DocumentAttributeVideo, DocumentAttributeFilename
    from telethon.tl.functions.messages import ImportChatInviteRequest
    HAS_TELETHON = True
    _TG_IMPORT_ERROR = ""
except Exception as _e:   # pragma: no cover - environment dependent
    HAS_TELETHON = False
    _TG_IMPORT_ERROR = str(_e)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def _default_log(msg, level="INFO"):
    line = f"[{level}] {msg}"
    try:
        print(line)
    except UnicodeEncodeError:
        enc = (getattr(sys.stdout, "encoding", None) or "utf-8")
        print(line.encode(enc, "replace").decode(enc, "replace"))


def _mask(value: str) -> str:
    """Mask a secret for logging (keep a couple of edge chars)."""
    s = str(value or "")
    if len(s) <= 4:
        return "****"
    return s[:2] + "…" + s[-2:]


def load_downloaded(path=None) -> set:
    path = path or TG_DL_LOG
    if not os.path.isfile(path):
        return set()
    try:
        with open(path, "r", encoding="utf-8") as f:
            return {ln.strip() for ln in f if ln.strip()}
    except Exception:
        return set()


def mark_downloaded(key: str, path=None) -> None:
    path = path or TG_DL_LOG
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(key + "\n")
    except Exception:
        pass


def _channel_slug(channel: str) -> str:
    s = str(channel).strip()
    s = re.sub(r"^https?://", "", s, flags=re.I)
    s = re.sub(r"^t\.me/", "", s, flags=re.I)
    s = s.split("?")[0].strip("/").lstrip("@")
    s = re.sub(r"[^A-Za-z0-9_]+", "_", s)
    return s.strip("_")[:40] or "channel"


def _entity_arg(channel: str):
    """Turn a user-typed channel into something get_entity understands."""
    c = str(channel).strip()
    if re.fullmatch(r"-?\d+", c):          # numeric id, e.g. -1001234567890
        return int(c)
    c = re.sub(r"^https?://", "", c, flags=re.I)
    c = re.sub(r"^t\.me/", "", c, flags=re.I)
    c = c.split("?")[0].strip("/")
    if c.startswith("+") or c.lower().startswith("joinchat/"):
        return "https://t.me/" + c      # private invite link
    return c.lstrip("@")


def _invite_hash(channel: str):
    m = re.search(r"(?:t\.me/|^)(?:joinchat/|\+)([A-Za-z0-9_\-]+)$", str(channel).strip(), re.I)
    return m.group(1) if m else None


def _msg_filename(msg) -> str:
    doc = getattr(msg, "document", None)
    if doc is not None:
        for attr in getattr(doc, "attributes", None) or []:
            if isinstance(attr, DocumentAttributeFilename):
                return attr.file_name or ""
    f = getattr(msg, "file", None)
    if f is not None and getattr(f, "name", None):
        return f.name
    return ""


def _save_caption(dest, msg):
    caption = (getattr(msg, "raw_text", "") or "").strip()
    video = Path(dest)
    if not caption or video.with_suffix(".txt").exists():
        return
    title = caption.splitlines()[0].strip()
    try:
        with video.with_suffix(".json").open("x", encoding="utf-8") as target:
            json.dump({"title": title[:100], "description": caption, "source": "telegram_caption"},
                      target, ensure_ascii=False)
    except FileExistsError:
        pass  # Preserve edits made in the queue.


def _out_ext(msg) -> str:
    name = _msg_filename(msg)
    ext = os.path.splitext(name)[1].lower() if name else ""
    if ext in VIDEO_EXTS:
        return ext
    f = getattr(msg, "file", None)
    if f is not None and getattr(f, "ext", None):
        e = f.ext.lower()
        if e in VIDEO_EXTS:
            return e
    return ".mp4"


def _out_name(slug: str, msg) -> str:
    return f"tg_{slug}_{int(msg.id):07d}{_out_ext(msg)}"


def _size_bytes(msg):
    f = getattr(msg, "file", None)
    if f is not None and getattr(f, "size", None):
        return f.size
    doc = getattr(msg, "document", None)
    if doc is not None and getattr(doc, "size", None):
        return doc.size
    return 0


def _size_str(msg) -> str:
    size = float(_size_bytes(msg) or 0)
    if not size:
        return "?"
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.0f}{unit}"
        size /= 1024
    return f"{size:.1f}TB"


def _is_video(msg, video_only: bool = True) -> bool:
    if getattr(msg, "video", None):
        return True
    doc = getattr(msg, "document", None)
    if doc is not None:
        mt = getattr(doc, "mime_type", "") or ""
        if mt.startswith("video/"):
            return True
        for attr in getattr(doc, "attributes", None) or []:
            if isinstance(attr, DocumentAttributeVideo):
                return True
            if isinstance(attr, DocumentAttributeFilename):
                if os.path.splitext(attr.file_name or "")[1].lower() in VIDEO_EXTS:
                    return True
    if not video_only and getattr(msg, "gif", None):
        return True
    return False


def _parse_proxy(spec: str, log=_default_log):
    """Parse ``host:port`` / ``socks5://user:pass@host:port`` into a PySocks tuple."""
    spec = str(spec or "").strip()
    if not spec:
        return None
    try:
        import socks  # PySocks
    except Exception:
        log("tg_proxy is set but PySocks is not installed — run:  pip install pysocks . "
            "Continuing without a proxy.", "WARNING")
        return None
    m = re.match(r"^(?:(socks5|socks4|http)://)?(?:([^:@/]+):([^@/]+)@)?([^:/]+):(\d+)$", spec, re.I)
    if not m:
        raise ValueError("Invalid Telegram proxy. Use host:port or a SOCKS/HTTP URL.")
    scheme, user, pwd, host, port = m.groups()
    kind = {"socks5": socks.SOCKS5, "socks4": socks.SOCKS4, "http": socks.HTTP}.get(
        (scheme or "socks5").lower(), socks.SOCKS5)
    if user and pwd:
        return (kind, host, int(port), True, user, pwd)
    return (kind, host, int(port))


# ---------------------------------------------------------------------------
# Downloader
# ---------------------------------------------------------------------------
class TelegramDownloader:
    """Thin, thread-safe-enough wrapper around a Telethon user client.

    Each public method spins up its own event loop + client, runs to completion,
    then disconnects — so it is safe to call from a GUI worker thread.
    """

    def __init__(self, api_id, api_hash, session: str = TG_SESSION, proxy=None, log=None):
        self.api_id = int(str(api_id).strip())
        self.api_hash = str(api_hash).strip()
        self.session = session
        self.proxy = proxy
        self.log = log or _default_log

    # -- internal -----------------------------------------------------------
    def _client(self):
        kwargs = {"device_model": "sliver-uploader", "system_version": "win", "app_version": "1.0"}
        if self.proxy:
            kwargs["proxy"] = self.proxy
        return TelegramClient(self.session, self.api_id, self.api_hash, **kwargs)

    def _run(self, coro_factory):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        client = None
        try:
            client = self._client()
            return loop.run_until_complete(coro_factory(client))
        finally:
            try:
                if client is not None and client.is_connected():
                    loop.run_until_complete(client.disconnect())
            except Exception:
                pass
            try:
                loop.run_until_complete(asyncio.sleep(0))
            except Exception:
                pass
            try:
                asyncio.set_event_loop(None)
                loop.close()
            except Exception:
                pass

    # -- auth ---------------------------------------------------------------
    def is_authorized(self) -> bool:
        async def _c(client):
            await client.connect()
            return bool(await client.is_user_authorized())
        try:
            return self._run(_c)
        except Exception as e:
            self.log(f"Telegram connection check failed: {e}", "ERROR")
            return False

    def login(self, phone: str, code_cb, password_cb=None) -> bool:
        """First-time interactive login. ``code_cb``/``password_cb`` may block."""
        phone = str(phone or "").strip()

        async def _c(client):
            await client.connect()
            if await client.is_user_authorized():
                self.log("Already logged in to Telegram.", "SUCCESS")
                return True
            if not phone:
                self.log("A phone number (with country code, e.g. +98…) is required for first-time login.", "ERROR")
                return False
            self.log("Requesting a login code from Telegram…")
            await client.send_code_request(phone)
            code = (code_cb() if code_cb else "") or ""
            code = str(code).strip().replace(" ", "").replace("-", "")
            if not code:
                self.log("No login code entered — login cancelled.", "WARNING")
                return False
            try:
                await client.sign_in(phone=phone, code=code)
            except SessionPasswordNeededError:
                self.log("Two-step verification is enabled — a password is needed.")
                pw = (password_cb() if password_cb else None)
                if not pw:
                    self.log("No 2FA password entered — login cancelled.", "WARNING")
                    return False
                await client.sign_in(password=str(pw))
            ok = bool(await client.is_user_authorized())
            if ok:
                try:
                    me = await client.get_me()
                    uname = getattr(me, "username", None) or "—"
                    self.log(f"✅ Logged in to Telegram as {getattr(me, 'first_name', '') or ''} (@{uname}).", "SUCCESS")
                except Exception:
                    self.log("✅ Logged in to Telegram.", "SUCCESS")
            return ok

        try:
            return self._run(_c)
        except PhoneCodeInvalidError:
            self.log("The login code is invalid — double-check it and try again.", "ERROR")
        except PhoneCodeExpiredError:
            self.log("The login code expired — request a new one and try again.", "ERROR")
        except PhoneNumberInvalidError:
            self.log("The phone number is invalid. Use the full international format, e.g. +98912…", "ERROR")
        except ApiIdInvalidError:
            self.log("API ID / API Hash are invalid. Re-copy them from https://my.telegram.org.", "ERROR")
        except FloodWaitError as e:
            self.log(f"Telegram rate-limited this action — wait {e.seconds}s and retry.", "ERROR")
        except Exception as e:
            self.log(f"Telegram login failed: {e}", "ERROR")
        return False

    # -- download -----------------------------------------------------------
    def download(self, channel: str, out_dir: str, limit: int = 0,
                 video_only: bool = True, log=None, progress=None, stop_check=None):
        """Download up to ``limit`` newest *new* videos from ``channel`` into ``out_dir``.

        ``limit`` of 0 means "all not-yet-downloaded videos". Returns the list of
        saved file paths.
        """
        log = log or self.log
        channel = str(channel).strip()
        slug = _channel_slug(channel)
        os.makedirs(out_dir, exist_ok=True)
        done = load_downloaded()
        saved: list = []

        async def _c(client):
            await client.connect()
            if not await client.is_user_authorized():
                log("Not logged in to Telegram yet — click 'Login to Telegram' first.", "ERROR")
                return saved

            # Best-effort join for private invite links.
            inv = _invite_hash(channel)
            if inv:
                try:
                    await client(ImportChatInviteRequest(inv))
                    log("Joined the private channel via its invite link.")
                except Exception:
                    pass  # already a member / not an invite — ignore

            try:
                entity = await client.get_entity(_entity_arg(channel))
            except Exception as e:
                log(f"Could not open channel '{channel}': {e}\n"
                    f"→ Make sure the link is correct and your account is a member of it.", "ERROR")
                return saved

            title = getattr(entity, "title", None) or getattr(entity, "username", None) or channel
            log(f"Scanning channel: {title}")

            # Private invite links can contain a bearer secret. Keep new media
            # names and public video titles independent of the invite.
            media_slug = f"channel_{entity.id}" if inv else slug
            want = int(limit) if limit and int(limit) > 0 else 0
            candidates = []
            async for msg in client.iter_messages(entity, limit=None):
                if stop_check and stop_check():
                    log("Download stopped by user.", "WARNING")
                    break
                if not _is_video(msg, video_only):
                    continue
                if f"{media_slug}:{msg.id}" in done or f"{slug}:{msg.id}" in done:
                    continue
                candidates.append(msg)
                if want and len(candidates) >= want:
                    break

            if not candidates:
                log("No new videos to download — everything already fetched.", "WARNING")
                return saved

            candidates.reverse()   # oldest-first => chronological filenames & upload order
            total = len(candidates)
            log(f"Found {total} new video(s). Starting download …", "SUCCESS")

            for i, msg in enumerate(candidates, 1):
                if stop_check and stop_check():
                    log("Download stopped by user.", "WARNING")
                    break
                name = _out_name(media_slug, msg)
                dest = os.path.join(out_dir, name)
                expected = _size_bytes(msg)
                if os.path.isfile(dest) and expected and os.path.getsize(dest) == expected:
                    _save_caption(dest, msg)
                    mark_downloaded(f"{media_slug}:{msg.id}")
                    saved.append(dest)
                    if progress:
                        progress(i, total, name)
                    continue

                log(f"[{i}/{total}] ⬇ {name}  ({_size_str(msg)})")
                last = [0.0]

                def _pcb(recv, tot, _name=name):
                    try:
                        if tot and (recv / tot - last[0] >= 0.2 or recv >= tot):
                            last[0] = recv / tot
                            log(f"      {_name}  {int(recv * 100 / tot)}%")
                    except Exception:
                        pass

                partial = None
                try:
                    # The queue and the daily scheduler see only complete videos.
                    with tempfile.NamedTemporaryFile(dir=out_dir, suffix=".part", delete=False) as target:
                        partial = target.name
                        await client.download_media(msg, file=target, progress_callback=_pcb)
                        size = target.tell()
                    if not size or (expected and size != expected):
                        raise OSError("Incomplete Telegram download")
                    _save_caption(dest, msg)
                    os.replace(partial, dest)
                    partial = None
                    mark_downloaded(f"{media_slug}:{msg.id}")
                    saved.append(dest)
                    log(f"      ✓ saved {name}", "SUCCESS")
                except Exception as e:
                    log(f"      ✗ failed {name}: {e}", "ERROR")
                finally:
                    if partial is not None and os.path.exists(partial):
                        os.unlink(partial)
                if progress:
                    progress(i, total, name)

            log(f"🏁 Telegram download finished — {len(saved)} file(s) in '{out_dir}'.", "SUCCESS")
            return saved

        try:
            return self._run(_c)
        except FloodWaitError as e:
            log(f"Telegram rate-limited the download — wait {e.seconds}s and retry.", "ERROR")
            return saved
        except Exception as e:
            log(f"Telegram download failed: {e}", "ERROR")
            return saved


# ---------------------------------------------------------------------------
# Config-driven convenience wrappers (used by the GUI and the CLI)
# ---------------------------------------------------------------------------
def _guard(cfg, log) -> bool:
    if not HAS_TELETHON:
        log(f"{TELETHON_HINT}  ({_TG_IMPORT_ERROR})", "ERROR")
        return False
    if not str(cfg.get("tg_api_id") or "").strip() or not str(cfg.get("tg_api_hash") or "").strip():
        log("Telegram API ID / API Hash are not set. Create them (free) at "
            "https://my.telegram.org → 'API development tools', then enter them in the Telegram tab.", "ERROR")
        return False
    return True


def _make_downloader(cfg, log, proxy=None):
    prx = proxy if proxy is not None else _parse_proxy(cfg.get("tg_proxy", ""), log)
    return TelegramDownloader(cfg.get("tg_api_id"), cfg.get("tg_api_hash"), proxy=prx, log=log)


def login_from_config(cfg, code_cb, password_cb=None, log=None, proxy=None) -> bool:
    log = log or _default_log
    if not _guard(cfg, log):
        return False
    try:
        dl = _make_downloader(cfg, log, proxy)
    except Exception as e:
        log(f"Could not initialise Telegram client (check API ID/Hash): {e}", "ERROR")
        return False
    return dl.login(cfg.get("tg_phone", ""), code_cb, password_cb)


def is_logged_in(cfg, log=None, proxy=None) -> bool:
    log = log or _default_log
    if not _guard(cfg, log):
        return False
    try:
        return _make_downloader(cfg, log, proxy).is_authorized()
    except Exception as e:
        log(f"Telegram auth check failed: {e}", "ERROR")
        return False


def download_from_config(cfg, channel=None, limit=None, log=None, progress=None,
                         stop_check=None, proxy=None):
    log = log or _default_log
    if not _guard(cfg, log):
        return []
    ch = str(channel if channel else cfg.get("tg_channel", "")).strip()
    if not ch:
        log("No Telegram channel specified — enter a channel like @name or https://t.me/name.", "ERROR")
        return []
    lim = cfg.get("tg_download_limit", 10) if limit is None else limit
    try:
        lim = int(lim)
    except Exception:
        lim = 10
    try:
        dl = _make_downloader(cfg, log, proxy)
    except Exception as e:
        log(f"Could not initialise Telegram client (check API ID/Hash): {e}", "ERROR")
        return []
    out_dir = str(cfg.get("video_folder") or "videos")
    return dl.download(ch, out_dir, limit=lim, video_only=bool(cfg.get("tg_video_only", True)),
                       log=log, progress=progress, stop_check=stop_check)


__all__ = [
    "HAS_TELETHON", "TELETHON_HINT", "TelegramDownloader",
    "login_from_config", "is_logged_in", "download_from_config",
    "load_downloaded", "TG_SESSION", "TG_DL_LOG",
]
