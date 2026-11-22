#!/usr/bin/env python3
"""
sliver YouTube Uploader — Graphical Interface (CustomTkinter)
=============================================================
A modern, dark, responsive desktop UI on top of ``yt_uploader.UploaderEngine``.

The queue, Telegram downloads, daily schedule and metadata editor share the
official YouTube OAuth connection. Run with run_gui.bat or yt_uploader.py --gui.
"""

from __future__ import annotations

import os
import re
import sys
import queue
import threading
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog

try:
    import customtkinter as ctk
except ImportError:
    print("customtkinter is required.  Install with:  pip install customtkinter")
    sys.exit(1)

from PIL import Image

import uploader
from desktop_oauth import pick_client_file
try:
    import telegram_downloader as tg_mod
except Exception as _tg_e:   # import-safe even if the module/telethon is missing
    tg_mod = None
    _TG_IMPORT_ERR = str(_tg_e)
from yt_uploader import (
    UploaderEngine, load_config, save_config, DEFAULT_CONFIG,
    list_videos, load_uploaded_log,
    generate_thumbnail_image, find_companion_image, save_companion_metadata,
    template_metadata, VISIBILITY_CHOICES, APP_VERSION, APP_DIR,
    LOG_FILE,
    create_daily_task, delete_daily_task, daily_task_exists, TASK_NAME,
    generate_cover, analyze_video,
)

# ---------------------------------------------------------------------------
# Palette
# ---------------------------------------------------------------------------
BADGE = {
    "pending":   ("#e5e7eb", "#374151"),   # (text bg light-ish, actual we use fg colors below)
}
STATUS_COLORS = {
    "pending":   "#9aa4b2",
    "custom":    "#a78bfa",
    "uploading": "#3b82f6",
    "uploaded":  "#22c55e",
    "failed":    "#ef4444",
    "skipped":   "#f59e0b",
}
LOG_COLORS = {
    "INFO":    "#c9d1d9",
    "SUCCESS": "#3fb950",
    "WARNING": "#d29922",
    "ERROR":   "#f85149",
}
ACCENT_CHOICES = ["blue", "dark-blue", "green"]


# ===========================================================================
# Video queue row
# ===========================================================================
class VideoRow(ctk.CTkFrame):
    def __init__(self, master, video_path: Path, app: "UploaderApp"):
        super().__init__(master, corner_radius=10)
        self.app = app
        self.video_path = video_path
        self.name = video_path.name

        self.grid_columnconfigure(2, weight=1)

        self.var = ctk.BooleanVar(value=True)
        self.check = ctk.CTkCheckBox(self, text="", width=24, variable=self.var,
                                     command=app.update_stats)
        self.check.grid(row=0, column=0, rowspan=2, padx=(10, 6), pady=10)

        # Thumbnail placeholder
        self.thumb = ctk.CTkLabel(self, text="🎬", width=118, height=66,
                                  fg_color=("#d9dde3", "#20242b"), corner_radius=8,
                                  font=ctk.CTkFont(size=26))
        self.thumb.grid(row=0, column=1, rowspan=2, padx=6, pady=8)

        self.name_lbl = ctk.CTkLabel(self, text=self.name, anchor="w",
                                     font=ctk.CTkFont(size=14, weight="bold"))
        self.name_lbl.grid(row=0, column=2, sticky="ew", padx=8, pady=(10, 0))

        self.status_lbl = ctk.CTkLabel(self, text="● Pending", anchor="w",
                                       text_color=STATUS_COLORS["pending"],
                                       font=ctk.CTkFont(size=12))
        self.status_lbl.grid(row=1, column=2, sticky="ew", padx=8, pady=(0, 10))

        self.edit_btn = ctk.CTkButton(self, text="✎ Edit", width=74,
                                      command=lambda: app.open_editor(self.video_path))
        self.edit_btn.grid(row=0, column=3, rowspan=2, padx=(4, 6), pady=10)

        self.play_btn = ctk.CTkButton(self, text="▶", width=40, fg_color="transparent",
                                      border_width=1,
                                      command=lambda: app.open_file(self.video_path))
        self.play_btn.grid(row=0, column=4, rowspan=2, padx=(0, 10), pady=10)

    def set_status(self, text: str, key: str):
        color = STATUS_COLORS.get(key, STATUS_COLORS["pending"])
        self.status_lbl.configure(text=f"● {text}", text_color=color)

    def set_thumbnail(self, pil_image: Image.Image):
        try:
            w, h = pil_image.size
            ctk_img = ctk.CTkImage(light_image=pil_image, dark_image=pil_image, size=(w, h))
            self.thumb.configure(image=ctk_img, text="")
            self.thumb._ctk_img_ref = ctk_img  # keep a reference
        except Exception:
            pass


# ===========================================================================
# Metadata editor dialog
# ===========================================================================
class EditMetadataDialog(ctk.CTkToplevel):
    def __init__(self, app: "UploaderApp", video_path: Path):
        super().__init__(app)
        self.app = app
        self.video_path = video_path
        self._q: queue.Queue = queue.Queue()

        self.title(f"Edit metadata — {video_path.name}")
        self.geometry("660x640")
        self.minsize(520, 520)

        ctk.CTkLabel(self, text=video_path.name,
                     font=ctk.CTkFont(size=16, weight="bold")).pack(anchor="w", padx=18, pady=(16, 8))

        ctk.CTkLabel(self, text="Title", anchor="w").pack(fill="x", padx=18)
        self.title_entry = ctk.CTkEntry(self, placeholder_text="Video title")
        self.title_entry.pack(fill="x", padx=18, pady=(2, 12))

        ctk.CTkLabel(self, text="Description", anchor="w").pack(fill="x", padx=18)
        self.desc_box = ctk.CTkTextbox(self, height=240, wrap="word")
        self.desc_box.pack(fill="both", expand=True, padx=18, pady=(2, 12))

        ctk.CTkLabel(self, text="Tags (comma-separated)", anchor="w").pack(fill="x", padx=18)
        self.tags_entry = ctk.CTkEntry(self, placeholder_text="tag1, tag2, tag3")
        self.tags_entry.pack(fill="x", padx=18, pady=(2, 12))

        self.status_lbl = ctk.CTkLabel(self, text="", text_color=STATUS_COLORS["uploading"], anchor="w")
        self.status_lbl.pack(fill="x", padx=18)

        btns = ctk.CTkFrame(self, fg_color="transparent")
        btns.pack(fill="x", padx=14, pady=14)
        self.ai_btn = ctk.CTkButton(btns, text="Regenerate from template", command=self.regenerate)
        self.ai_btn.pack(side="left", padx=4)
        ctk.CTkButton(btns, text="💾 Save", command=self.save,
                      fg_color=STATUS_COLORS["uploaded"], hover_color="#16a34a").pack(side="right", padx=4)
        ctk.CTkButton(btns, text="Cancel", command=self.destroy,
                      fg_color="transparent", border_width=1).pack(side="right", padx=4)

        self._load_existing()
        self.after(120, self._poll)
        self.transient(app)
        self.grab_set()
        self.focus()

    def _load_existing(self):
        companion = self.video_path.with_suffix(".json")
        if companion.exists():
            try:
                import json
                with open(companion, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.title_entry.insert(0, data.get("title", ""))
                self.desc_box.insert("1.0", data.get("description", ""))
                tags = data.get("tags", [])
                if isinstance(tags, list):
                    tags = ", ".join(tags)
                self.tags_entry.insert(0, tags)
                self.status_lbl.configure(text="Loaded saved metadata.", text_color=STATUS_COLORS["custom"])
            except Exception as e:
                self.status_lbl.configure(text=f"Could not read saved metadata: {e}",
                                          text_color=STATUS_COLORS["failed"])

    def regenerate(self):
        self.ai_btn.configure(state="disabled", text="Analyzing…")
        self.status_lbl.configure(text="Reading the video and your metadata template…",
                                  text_color=STATUS_COLORS["uploading"])
        threading.Thread(target=self._gen_worker, daemon=True).start()

    def _gen_worker(self):
        try:
            cfg = dict(self.app.cfg)
            t, d, tags, _ = template_metadata(
                self.video_path, cfg,
                lambda m, level="INFO": self.app.ui_queue.put(("log", m, level)),
            )
            self._q.put(("ok", t, d, tags))
        except Exception as e:
            self._q.put(("err", str(e)))

    def _poll(self):
        try:
            while True:
                item = self._q.get_nowait()
                if item[0] == "ok":
                    _, t, d, tags = item
                    self.title_entry.delete(0, "end")
                    self.title_entry.insert(0, t)
                    self.desc_box.delete("1.0", "end")
                    self.desc_box.insert("1.0", d)
                    self.tags_entry.delete(0, "end")
                    self.tags_entry.insert(0, ", ".join(tags) if isinstance(tags, list) else tags)
                    self.status_lbl.configure(text="Metadata generated.",
                                              text_color=STATUS_COLORS["uploaded"])
                else:
                    self.status_lbl.configure(text=f"Error: {item[1]}",
                                              text_color=STATUS_COLORS["failed"])
                self.ai_btn.configure(state="normal", text="Regenerate from template")
        except queue.Empty:
            pass
        if self.winfo_exists():
            self.after(150, self._poll)

    def save(self):
        title = self.title_entry.get().strip()
        desc = self.desc_box.get("1.0", "end").strip()
        tags = [t.strip() for t in self.tags_entry.get().split(",") if t.strip()]
        if not title:
            messagebox.showwarning("Missing title", "Please enter a title before saving.")
            return
        if save_companion_metadata(self.video_path, title, desc, tags):
            self.app.ui_queue.put(("log", f"Saved custom metadata for {self.video_path.name}", "SUCCESS"))
            self.app.mark_row_custom(self.video_path.name)
            self.destroy()
        else:
            messagebox.showerror("Save failed", "Could not write the companion metadata file.")


# ===========================================================================
# Main application
# ===========================================================================
class UploaderApp(ctk.CTk):
    def __init__(self, autorun: bool = False):
        super().__init__()
        self.cfg_data = load_config()
        self.cfg = self.cfg_data  # alias used across the app
        self._autorun = autorun
        self._auto_close = False
        self._last_sched_date = None

        ctk.set_appearance_mode(self.cfg.get("appearance_mode", "Dark"))
        try:
            ctk.set_default_color_theme(self.cfg.get("accent_color", "dark-blue"))
        except Exception:
            ctk.set_default_color_theme("dark-blue")

        self.title(f"sliver YouTube Uploader {APP_VERSION}")
        self.geometry("1180x760")
        self.minsize(980, 640)

        self.engine: UploaderEngine | None = None
        self.worker: threading.Thread | None = None
        self.ui_queue: queue.Queue = queue.Queue()
        self.rows: dict[str, VideoRow] = {}
        self.settings_widgets: dict = {}
        self.tg_widgets: dict = {}
        self.tg_action_buttons: list = []
        self._tg_stop = False
        self._tg_auto = False
        self._tg_downloaded: list = []
        self._mode = None  # "upload" | "login" | "dryrun" | "tglogin" | "tgdownload"

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self._build_sidebar()
        self._build_main()
        self._build_pages()

        self.show_page("queue")
        self.refresh_queue()

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(80, self._poll)
        self.after(30000, self._check_schedule)
        if self._autorun or self.cfg.get("auto_start_on_launch", False):
            self.after(1500, self._auto_start_batch)

    # ---------------------------------------------------------------- sidebar
    def _build_sidebar(self):
        bar = ctk.CTkFrame(self, width=214, corner_radius=0)
        bar.grid(row=0, column=0, sticky="nsew")
        bar.grid_rowconfigure(8, weight=1)

        ctk.CTkLabel(bar, text="▶  sliver", font=ctk.CTkFont(size=24, weight="bold")).grid(
            row=0, column=0, padx=22, pady=(22, 0), sticky="w")
        ctk.CTkLabel(bar, text="YouTube Uploader", text_color=("#6b7280", "#9aa4b2"),
                     font=ctk.CTkFont(size=13)).grid(row=1, column=0, padx=22, pady=(0, 18), sticky="w")

        self.nav_buttons = {}
        nav = [("queue", "📋  Queue"), ("telegram", "✈   Telegram"), ("settings", "⚙   Settings"),
               ("schedule", "⏰  Schedule"), ("logs", "📜  Logs"), ("help", "❓  Help")]
        for i, (key, label) in enumerate(nav, start=2):
            b = ctk.CTkButton(bar, text=label, anchor="w", height=42, corner_radius=8,
                              fg_color="transparent", text_color=("#1f2937", "#e5e7eb"),
                              hover_color=("#e5e7eb", "#2b3038"),
                              font=ctk.CTkFont(size=15),
                              command=lambda k=key: self.show_page(k))
            b.grid(row=i, column=0, padx=12, pady=3, sticky="ew")
            self.nav_buttons[key] = b

        # Appearance controls (bottom)
        ctk.CTkLabel(bar, text="Appearance", anchor="w",
                     text_color=("#6b7280", "#9aa4b2")).grid(row=9, column=0, padx=22, pady=(10, 0), sticky="w")
        self.appearance_menu = ctk.CTkOptionMenu(
            bar, values=["Dark", "Light", "System"], command=self._change_appearance)
        self.appearance_menu.set(self.cfg.get("appearance_mode", "Dark"))
        self.appearance_menu.grid(row=10, column=0, padx=16, pady=(2, 6), sticky="ew")

        self.accent_menu = ctk.CTkOptionMenu(
            bar, values=ACCENT_CHOICES, command=self._change_accent)
        self.accent_menu.set(self.cfg.get("accent_color", "dark-blue"))
        self.accent_menu.grid(row=11, column=0, padx=16, pady=(0, 8), sticky="ew")

        ctk.CTkLabel(bar, text=f"engine {APP_VERSION}", text_color=("#9aa4b2", "#4b5563"),
                     font=ctk.CTkFont(size=11)).grid(row=12, column=0, padx=22, pady=(0, 14), sticky="w")

    # ------------------------------------------------------------- main frame
    def _build_main(self):
        self.main = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        self.main.grid(row=0, column=1, sticky="nsew")
        self.main.grid_columnconfigure(0, weight=1)
        self.main.grid_rowconfigure(2, weight=1)

        # ---- Top control bar ----
        top = ctk.CTkFrame(self.main, corner_radius=12)
        top.grid(row=0, column=0, sticky="ew", padx=14, pady=(14, 6))
        top.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(top, text="Folder:", font=ctk.CTkFont(size=13)).grid(
            row=0, column=0, padx=(14, 6), pady=12)
        self.folder_entry = ctk.CTkEntry(top, placeholder_text="videos folder…")
        self.folder_entry.insert(0, self.cfg.get("video_folder", "videos"))
        self.folder_entry.grid(row=0, column=1, sticky="ew", padx=6, pady=12)
        ctk.CTkButton(top, text="Browse", width=76, command=self._browse_folder).grid(
            row=0, column=2, padx=4, pady=12)
        ctk.CTkButton(top, text="↻ Refresh", width=90, command=self._apply_folder_and_refresh).grid(
            row=0, column=3, padx=4, pady=12)
        ctk.CTkButton(top, text="📂 Open", width=76, fg_color="transparent", border_width=1,
                      command=lambda: self.open_file(Path(self.folder_entry.get()))).grid(
            row=0, column=4, padx=(4, 14), pady=12)

        # ---- Action bar ----
        act = ctk.CTkFrame(self.main, corner_radius=12)
        act.grid(row=1, column=0, sticky="ew", padx=14, pady=(0, 6))
        act.grid_columnconfigure(6, weight=1)

        self.login_btn = ctk.CTkButton(act, text="Connect YouTube", width=100, command=self.login)
        self.login_btn.grid(row=0, column=0, padx=(14, 4), pady=12)

        self.start_btn = ctk.CTkButton(act, text="▶ Start", width=110,
                                       fg_color=STATUS_COLORS["uploaded"], hover_color="#16a34a",
                                       command=self.start_upload)
        self.start_btn.grid(row=0, column=1, padx=4, pady=12)

        self.pause_btn = ctk.CTkButton(act, text="⏸ Pause", width=100, state="disabled",
                                       command=self.toggle_pause)
        self.pause_btn.grid(row=0, column=2, padx=4, pady=12)

        self.stop_btn = ctk.CTkButton(act, text="⏹ Stop", width=90, state="disabled",
                                      fg_color=STATUS_COLORS["failed"], hover_color="#b91c1c",
                                      command=self.stop_run)
        self.stop_btn.grid(row=0, column=3, padx=4, pady=12)

        self.dry_btn = ctk.CTkButton(act, text="👁 Preview (dry-run)", width=150,
                                     fg_color="transparent", border_width=1,
                                     command=self.start_dryrun)
        self.dry_btn.grid(row=0, column=4, padx=4, pady=12)

        initial = "Idle." if uploader.DEFAULT_TOKEN.is_file() else "Connect YouTube to authorize this channel."
        self.status_lbl = ctk.CTkLabel(act, text=initial, anchor="e",
                                       font=ctk.CTkFont(size=13))
        self.status_lbl.grid(row=0, column=6, sticky="e", padx=14)

        self.progress = ctk.CTkProgressBar(act)
        self.progress.set(0)
        self.progress.grid(row=1, column=0, columnspan=7, sticky="ew", padx=14, pady=(0, 12))

        # Page container
        self.page_container = ctk.CTkFrame(self.main, corner_radius=12)
        self.page_container.grid(row=2, column=0, sticky="nsew", padx=14, pady=(4, 14))
        self.page_container.grid_columnconfigure(0, weight=1)
        self.page_container.grid_rowconfigure(0, weight=1)

    # ------------------------------------------------------------------ pages
    def _build_pages(self):
        self.pages = {}
        self._build_queue_page()
        self._build_telegram_page()
        self._build_settings_page()
        self._build_schedule_page()
        self._build_logs_page()
        self._build_help_page()

    def _build_queue_page(self):
        page = ctk.CTkFrame(self.page_container, fg_color="transparent")
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(2, weight=1)

        # stat chips
        chips = ctk.CTkFrame(page, fg_color="transparent")
        chips.grid(row=0, column=0, sticky="ew", padx=6, pady=(8, 4))
        self.stat_labels = {}
        for i, (key, text) in enumerate([("total", "Total"), ("pending", "Pending"),
                                         ("uploaded", "Uploaded"), ("failed", "Failed")]):
            chip = ctk.CTkFrame(chips, corner_radius=10)
            chip.grid(row=0, column=i, padx=6, pady=4, sticky="w")
            val = ctk.CTkLabel(chip, text="0", font=ctk.CTkFont(size=20, weight="bold"))
            val.grid(row=0, column=0, padx=(14, 6), pady=(8, 0))
            ctk.CTkLabel(chip, text=text, text_color=("#6b7280", "#9aa4b2"),
                         font=ctk.CTkFont(size=12)).grid(row=1, column=0, padx=14, pady=(0, 8), sticky="w")
            self.stat_labels[key] = val

        # toolbar
        tb = ctk.CTkFrame(page, fg_color="transparent")
        tb.grid(row=1, column=0, sticky="ew", padx=6, pady=(2, 6))
        ctk.CTkButton(tb, text="Select all", width=90, command=lambda: self._set_all_checks(True)).pack(side="left", padx=4)
        ctk.CTkButton(tb, text="Clear", width=70, command=lambda: self._set_all_checks(False)).pack(side="left", padx=4)
        ctk.CTkButton(tb, text="Select pending", width=120, command=self._select_pending).pack(side="left", padx=4)
        ctk.CTkButton(tb, text="🎨 Covers", width=100, command=self.generate_covers).pack(side="left", padx=4)
        ctk.CTkButton(tb, text="↺ Reset upload log", width=150, fg_color="transparent",
                      border_width=1, command=self._reset_upload_log).pack(side="right", padx=4)

        self.queue_scroll = ctk.CTkScrollableFrame(page, label_text="Videos")
        self.queue_scroll.grid(row=2, column=0, sticky="nsew", padx=6, pady=6)
        self.queue_scroll.grid_columnconfigure(0, weight=1)

        self.pages["queue"] = page

    def _build_telegram_page(self):
        page = ctk.CTkScrollableFrame(self.page_container, label_text="Telegram → YouTube")
        page.grid_columnconfigure(1, weight=1)
        w = self.tg_widgets
        r = 0

        def section(title, sub=""):
            nonlocal r
            ctk.CTkLabel(page, text=title, font=ctk.CTkFont(size=15, weight="bold"),
                         anchor="w").grid(row=r, column=0, columnspan=3, sticky="w", padx=8, pady=(14, 2))
            r += 1
            if sub:
                ctk.CTkLabel(page, text=sub, anchor="w", justify="left",
                             text_color=("#6b7280", "#9aa4b2"),
                             font=ctk.CTkFont(size=12)).grid(row=r, column=0, columnspan=3,
                                                             sticky="w", padx=8, pady=(0, 4))
                r += 1

        def entry_row(key, label, placeholder="", show=None):
            nonlocal r
            ctk.CTkLabel(page, text=label, anchor="w").grid(row=r, column=0, sticky="w", padx=8, pady=6)
            e = ctk.CTkEntry(page, placeholder_text=placeholder)
            if show:
                e.configure(show=show)
            val = self.cfg.get(key, "")
            e.insert(0, "" if val is None else str(val))
            e.grid(row=r, column=1, columnspan=2, sticky="ew", padx=8, pady=6)
            w[key] = e
            r += 1
            return e

        def switch_row(key, label):
            nonlocal r
            s = ctk.CTkSwitch(page, text=label)
            if self.cfg.get(key, False):
                s.select()
            s.grid(row=r, column=0, columnspan=2, sticky="w", padx=8, pady=6)
            w[key] = s
            r += 1

        section("🔑 Telegram account",
                "One-time setup. Get a free API ID + API Hash at my.telegram.org → "
                "'API development tools'. Login uses the code Telegram sends to your app.")
        entry_row("tg_api_id", "API ID", "e.g. 1234567")
        entry_row("tg_api_hash", "API Hash", "e.g. 0123456789abcdef…", show="•")
        entry_row("tg_phone", "Phone (intl)", "+98912…")
        entry_row("tg_proxy", "Proxy (optional)", "socks5://127.0.0.1:10808  (if Telegram is filtered)")

        section("📥 Channel & download",
                "The channel to pull videos from. Public @name / t.me link, or any "
                "channel your account has joined. Downloads land in the video folder.")
        entry_row("tg_channel", "Channel", "@mychannel  or  https://t.me/mychannel")
        entry_row("tg_download_limit", "How many newest videos", "10  (0 = all new)")
        switch_row("tg_video_only", "Only download video posts")
        switch_row("tg_auto_upload", "Auto-upload to YouTube right after downloading")

        # action buttons
        btns = ctk.CTkFrame(page, fg_color="transparent")
        btns.grid(row=r, column=0, columnspan=3, sticky="ew", pady=(16, 6))
        r += 1
        b_login = ctk.CTkButton(btns, text="🔐 Login to Telegram", width=170, command=self.tg_login)
        b_login.pack(side="left", padx=6)
        b_dl = ctk.CTkButton(btns, text="⬇ Download", width=130,
                             fg_color=STATUS_COLORS["uploading"], hover_color="#2563eb",
                             command=lambda: self.tg_download(auto=False))
        b_dl.pack(side="left", padx=6)
        b_dlup = ctk.CTkButton(btns, text="⬇▶ Download + Upload", width=190,
                               fg_color=STATUS_COLORS["uploaded"], hover_color="#16a34a",
                               command=lambda: self.tg_download(auto=True))
        b_dlup.pack(side="left", padx=6)
        b_save = ctk.CTkButton(btns, text="💾 Save", width=90, fg_color="transparent",
                               border_width=1, command=self.save_tg_settings)
        b_save.pack(side="left", padx=6)
        self.tg_action_buttons = [b_login, b_dl, b_dlup, b_save]

        self.tg_status_lbl = ctk.CTkLabel(page, text="", anchor="w", justify="left",
                                          text_color=("#6b7280", "#9aa4b2"),
                                          font=ctk.CTkFont(size=12))
        self.tg_status_lbl.grid(row=r, column=0, columnspan=3, sticky="w", padx=8, pady=(2, 8))
        r += 1

        if tg_mod is None or not getattr(tg_mod, "HAS_TELETHON", False):
            self.tg_status_lbl.configure(
                text="⚠ The 'telethon' package is not installed. Run:  pip install telethon  "
                     "then restart the app.",
                text_color=STATUS_COLORS["failed"])

        self.pages["telegram"] = page

    def _build_settings_page(self):
        page = ctk.CTkScrollableFrame(self.page_container, label_text="Settings")
        page.grid_columnconfigure(1, weight=1)
        w = self.settings_widgets
        r = 0

        def section(title):
            nonlocal r
            ctk.CTkLabel(page, text=title, font=ctk.CTkFont(size=15, weight="bold"),
                         anchor="w").grid(row=r, column=0, columnspan=3, sticky="w", padx=8, pady=(14, 4))
            r += 1

        def entry_row(key, label, placeholder="", browse=False, is_dir=False):
            nonlocal r
            ctk.CTkLabel(page, text=label, anchor="w").grid(row=r, column=0, sticky="w", padx=8, pady=6)
            e = ctk.CTkEntry(page, placeholder_text=placeholder)
            val = self.cfg.get(key, "")
            e.insert(0, "" if val is None else str(val))
            e.grid(row=r, column=1, sticky="ew", padx=8, pady=6)
            if browse:
                ctk.CTkButton(page, text="…", width=40,
                              command=lambda e=e, is_dir=is_dir: self._browse_into(e, is_dir)).grid(
                    row=r, column=2, padx=(0, 8), pady=6)
            w[key] = e
            r += 1

        def switch_row(key, label):
            nonlocal r
            s = ctk.CTkSwitch(page, text=label)
            if self.cfg.get(key, False):
                s.select()
            s.grid(row=r, column=0, columnspan=2, sticky="w", padx=8, pady=6)
            w[key] = s
            r += 1

        section("📁 Content")
        entry_row("video_folder", "Video folder", "videos", browse=True, is_dir=True)
        entry_row("cover_dir", "Covers folder", "covers", browse=True, is_dir=True)
        entry_row("telegram_channel", "Telegram channel", "https://t.me/…")
        entry_row("telegram_owner", "Telegram owner", "https://t.me/…")

        section("📝 Metadata templates")
        entry_row("title_template", "Title template", "{title}")
        ctk.CTkLabel(page, text="Description template", anchor="w").grid(row=r, column=0, sticky="nw", padx=8, pady=6)
        desc = ctk.CTkTextbox(page, height=150, wrap="word")
        desc.insert("1.0", self.cfg.get("description_template", ""))
        desc.grid(row=r, column=1, columnspan=2, sticky="ew", padx=8, pady=6)
        w["description_template"] = desc
        r += 1
        entry_row("default_tags", "Default tags", "tag1, tag2, …")

        section("📤 Upload options")
        ctk.CTkLabel(page, text="Visibility", anchor="w").grid(row=r, column=0, sticky="w", padx=8, pady=6)
        vis = ctk.CTkOptionMenu(page, values=VISIBILITY_CHOICES)
        vis.set(str(self.cfg.get("visibility", "PUBLIC")).upper())
        vis.grid(row=r, column=1, sticky="w", padx=8, pady=6)
        w["visibility"] = vis
        r += 1
        ctk.CTkLabel(page, text="Metadata language", anchor="w").grid(row=r, column=0, sticky="w", padx=8, pady=6)
        tlang = ctk.CTkOptionMenu(page, values=["auto", "en", "it", "fr", "de", "es", "pt", "tr", "ru", "ar", "cs", "fa"])
        tlang.set(str(self.cfg.get("title_language", "auto")))
        tlang.grid(row=r, column=1, sticky="w", padx=8, pady=6)
        w["title_language"] = tlang
        r += 1
        ctk.CTkLabel(page, text="Cover style", anchor="w").grid(row=r, column=0, sticky="w", padx=8, pady=6)
        cstyle = ctk.CTkOptionMenu(page, values=["bold", "gaming", "minimal", "spotlight"])
        cstyle.set(str(self.cfg.get("cover_style", "bold")))
        cstyle.grid(row=r, column=1, sticky="w", padx=8, pady=6)
        w["cover_style"] = cstyle
        r += 1
        entry_row("upload_delay_seconds", "Delay between uploads (sec)", "60")
        entry_row("max_retries", "Network retries per upload chunk", "1")
        switch_row("made_for_kids", "Mark videos as 'Made for kids'")
        switch_row("set_thumbnail", "Use companion image as custom thumbnail")
        switch_row("auto_cover", "Auto-generate a professional cover / thumbnail")
        switch_row("use_ai_ocr", "Extract on-screen text for the {ocr_text} template field (WinOCR)")

        section("YouTube connection")
        entry_row("oauth_client_file", "Desktop OAuth client JSON", "client_secret.json", browse=True)

        # save / reset buttons
        btns = ctk.CTkFrame(page, fg_color="transparent")
        btns.grid(row=r, column=0, columnspan=3, sticky="ew", pady=16)
        ctk.CTkButton(btns, text="💾 Save settings", command=self.save_settings,
                      fg_color=STATUS_COLORS["uploaded"], hover_color="#16a34a").pack(side="left", padx=6)
        ctk.CTkButton(btns, text="Reset to defaults", fg_color="transparent", border_width=1,
                      command=self.reset_settings).pack(side="left", padx=6)
        self.settings_saved_lbl = ctk.CTkLabel(btns, text="", text_color=STATUS_COLORS["uploaded"])
        self.settings_saved_lbl.pack(side="left", padx=10)

        self.pages["settings"] = page

    def _build_schedule_page(self):
        page = ctk.CTkScrollableFrame(self.page_container, label_text="Daily auto-upload")
        page.grid_columnconfigure(1, weight=1)
        self.sched_widgets = {}
        r = 0

        def head(txt, sub=""):
            nonlocal r
            ctk.CTkLabel(page, text=txt, font=ctk.CTkFont(size=16, weight="bold"),
                         anchor="w").grid(row=r, column=0, columnspan=3, sticky="w", padx=8, pady=(14, 0))
            r += 1
            if sub:
                ctk.CTkLabel(page, text=sub, text_color=("#6b7280", "#9aa4b2"), anchor="w",
                             justify="left").grid(row=r, column=0, columnspan=3, sticky="w", padx=8, pady=(0, 6))
                r += 1

        def field(label, key, placeholder=""):
            nonlocal r
            ctk.CTkLabel(page, text=label, anchor="w").grid(row=r, column=0, sticky="w", padx=8, pady=6)
            e = ctk.CTkEntry(page, placeholder_text=placeholder, width=140)
            e.insert(0, str(self.cfg.get(key, "")))
            e.grid(row=r, column=1, sticky="w", padx=8, pady=6)
            self.sched_widgets[key] = e
            r += 1

        head("How many & when",
             "Each daily run opens the app and uploads the given number of pending videos.")
        field("Videos per day", "videos_per_day", "5")
        field("Daily time (HH:MM, 24h)", "daily_time", "10:00")

        head("Behaviour")
        for key, lbl in [("auto_start_on_launch", "Auto-start a batch when the app opens (used by the daily task)"),
                         ("auto_close_when_done", "Close the app automatically after an auto batch finishes"),
                         ("run_daily_while_open", "Also run daily while the app stays open (in-app timer)")]:
            s = ctk.CTkSwitch(page, text=lbl)
            if self.cfg.get(key, False):
                s.select()
            s.grid(row=r, column=0, columnspan=3, sticky="w", padx=8, pady=6)
            self.sched_widgets[key] = s
            r += 1
        # keep a direct handle for the in-app scheduler check
        self.sched_switch = self.sched_widgets["run_daily_while_open"]

        ctk.CTkButton(page, text="💾 Save schedule settings", command=self.save_schedule_settings,
                      fg_color=STATUS_COLORS["uploaded"], hover_color="#16a34a").grid(
            row=r, column=0, columnspan=2, sticky="w", padx=8, pady=(10, 4))
        r += 1

        head("Windows daily task",
             "Registers a Windows Scheduled Task so the app opens & uploads every day even if it is closed.\n"
             "Connect YouTube once under the Windows account that runs this task.")
        self.task_status = ctk.CTkLabel(page, text="", anchor="w")
        self.task_status.grid(row=r, column=0, columnspan=3, sticky="w", padx=8, pady=(0, 4))
        r += 1
        btns = ctk.CTkFrame(page, fg_color="transparent")
        btns.grid(row=r, column=0, columnspan=3, sticky="w", padx=6, pady=6)
        ctk.CTkButton(btns, text="✅ Enable daily task", command=self.enable_daily_task).pack(side="left", padx=4)
        ctk.CTkButton(btns, text="🗑 Disable daily task", fg_color=STATUS_COLORS["failed"],
                      hover_color="#b91c1c", command=self.disable_daily_task).pack(side="left", padx=4)
        ctk.CTkButton(btns, text="↻ Check", fg_color="transparent", border_width=1,
                      command=self.refresh_task_status).pack(side="left", padx=4)
        r += 1

        self.pages["schedule"] = page
        self.refresh_task_status()

    def _build_logs_page(self):
        page = ctk.CTkFrame(self.page_container, fg_color="transparent")
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(1, weight=1)

        tb = ctk.CTkFrame(page, fg_color="transparent")
        tb.grid(row=0, column=0, sticky="ew", padx=6, pady=6)
        ctk.CTkButton(tb, text="Clear", width=80, command=self._clear_logs).pack(side="left", padx=4)
        ctk.CTkButton(tb, text="💾 Save log", width=100, command=self._save_logs).pack(side="left", padx=4)
        self.autoscroll = ctk.CTkSwitch(tb, text="Auto-scroll")
        self.autoscroll.select()
        self.autoscroll.pack(side="right", padx=8)

        self.log_box = ctk.CTkTextbox(page, wrap="word", font=ctk.CTkFont(family="Consolas", size=12))
        self.log_box.grid(row=1, column=0, sticky="nsew", padx=6, pady=6)
        # colour tags (best-effort)
        for level, color in LOG_COLORS.items():
            try:
                self.log_box.tag_config(level, foreground=color)
            except Exception:
                pass
        self.log_box.configure(state="disabled")

        self.pages["logs"] = page

    def _build_help_page(self):
        page = ctk.CTkScrollableFrame(self.page_container, label_text="Help")
        text = (
            "sliver YouTube Uploader — quick guide\n"
            "──────────────────────────────────────\n\n"
            "1)  Put your video files in the folder shown in the top bar\n"
            "    (default: the 'videos' folder next to this script).\n"
            "    Optional companion files next to each video:\n"
            "      • <name>.json  → {\"title\":..,\"description\":..,\"tags\":[..]}  (manual metadata)\n"
            "      • <name>.jpg/.png → used as the custom thumbnail.\n\n"
            "2)  Click Connect YouTube to authorize your channel once. Choose your Google\n"
            "    Desktop OAuth client JSON if prompted. Uploads reuse the saved permission.\n\n"
            "3)  Review the queue. Tick the videos you want to upload. Use ✎ Edit to\n"
            "    fine-tune a video's title/description/tags, or regenerate to\n"
            "    apply your templates. Optional WinOCR supplies the {ocr_text} field.\n\n"
            "4)  Click ▶ Start. Watch progress in the bar and the Logs tab. You can\n"
            "    ⏸ Pause / ▶ Resume or ⏹ Stop at any time.\n\n"
            "5)  👁 Preview (dry-run) generates metadata for every video WITHOUT opening\n"
            "    a browser — handy to check titles before publishing.\n\n"
            "✈  Telegram tab — pull videos straight from a Telegram channel:\n"
            "    • Get a free API ID + API Hash at https://my.telegram.org\n"
            "      ('API development tools'), paste them in, add your phone, and click\n"
            "      🔐 Login to Telegram (one time — enter the code Telegram sends you).\n"
            "    • Put a channel in 'Channel' (@name, a t.me link, or one you've joined),\n"
            "      choose how many newest videos to grab, then ⬇ Download — they land in\n"
            "      the video folder, ready in the Queue. ⬇▶ Download + Upload does both.\n"
            "    • If Telegram is filtered on your network, set a proxy like\n"
            "      socks5://127.0.0.1:10808 in the Proxy field.\n\n"
            "Settings tab controls visibility (Public/Unlisted/Private), audience,\n"
            "thumbnails, retries, cooldown delay, OAuth client file,\n"
            "and the metadata templates. Everything is saved to yt_config.json.\n\n"
            "CLI equivalents:\n"
            "    python yt_uploader.py --setup\n"
            "    python yt_uploader.py --folder \"C:\\MyVideos\" --visibility UNLISTED\n"
            "    python yt_uploader.py --dry-run\n"
            "    python yt_uploader.py --tg-login\n"
            "    python yt_uploader.py --tg-download --tg-channel @name --tg-limit 5\n"
            "    python yt_uploader.py --tg-download --tg-upload\n"
        )
        box = ctk.CTkTextbox(page, wrap="word", height=560, font=ctk.CTkFont(size=13))
        box.insert("1.0", text)
        box.configure(state="disabled")
        box.pack(fill="both", expand=True, padx=6, pady=6)
        self.pages["help"] = page

    # -------------------------------------------------------------- navigation
    def show_page(self, key: str):
        for k, page in self.pages.items():
            page.grid_forget()
        self.pages[key].grid(row=0, column=0, sticky="nsew")
        for k, btn in self.nav_buttons.items():
            btn.configure(fg_color=("#dbe4ff", "#243044") if k == key else "transparent")

    # --------------------------------------------------------- queue handling
    def refresh_queue(self):
        for child in self.queue_scroll.winfo_children():
            child.destroy()
        self.rows.clear()

        folder = Path(self.cfg.get("video_folder", "videos"))
        videos = list_videos(folder)
        uploaded = load_uploaded_log()

        if not videos:
            ctk.CTkLabel(self.queue_scroll,
                         text=f"No videos found in:\n{folder.resolve() if folder else folder}\n\n"
                              "Add .mp4/.mkv/.mov files and click ↻ Refresh.",
                         justify="center", text_color=("#6b7280", "#9aa4b2")).grid(
                row=0, column=0, pady=40)
            self.update_stats()
            return

        for v in videos:
            row = VideoRow(self.queue_scroll, v, self)
            row.grid(sticky="ew", padx=4, pady=4)
            row.grid_columnconfigure(2, weight=1)
            if v.name in uploaded:
                row.set_status("Uploaded", "uploaded")
                row.var.set(False)
            elif v.with_suffix(".json").exists():
                row.set_status("Pending • custom metadata", "custom")
            else:
                row.set_status("Pending", "pending")
            self.rows[v.name] = row

        self.update_stats()
        threading.Thread(target=self._thumb_worker, args=(videos,), daemon=True).start()

    def _thumb_worker(self, videos):
        for v in videos:
            img = None
            companion = find_companion_image(v) or (Path(self.cfg["cover_dir"]) / (v.stem + ".jpg"))
            try:
                if companion.is_file():
                    with Image.open(companion) as original:
                        img = original.copy()
                    img.thumbnail((118, 66), Image.LANCZOS)
                else:
                    img = generate_thumbnail_image(v, size=(118, 66))
            except Exception:
                img = None
            if img is not None:
                self.ui_queue.put(("thumb", v.name, img))

    def update_stats(self):
        uploaded = load_uploaded_log()
        total = len(self.rows)
        up = sum(1 for n in self.rows if n in uploaded)
        failed = sum(1 for r in self.rows.values() if r.status_lbl.cget("text").endswith("Failed"))
        pending = total - up
        self.stat_labels["total"].configure(text=str(total))
        self.stat_labels["uploaded"].configure(text=str(up))
        self.stat_labels["pending"].configure(text=str(max(0, pending)))
        self.stat_labels["failed"].configure(text=str(failed))

    def _set_all_checks(self, value: bool):
        for r in self.rows.values():
            r.var.set(value)
        self.update_stats()

    def _select_pending(self):
        uploaded = load_uploaded_log()
        for name, r in self.rows.items():
            r.var.set(name not in uploaded)
        self.update_stats()

    def mark_row_custom(self, name: str):
        row = self.rows.get(name)
        if row and name not in load_uploaded_log():
            row.set_status("Pending • custom metadata", "custom")

    def _reset_upload_log(self):
        if not messagebox.askyesno("Reset upload log",
                                   "Clear the record of already-uploaded videos?\n"
                                   "(This does NOT delete anything from YouTube — it only lets "
                                   "these videos be uploaded again.)"):
            return
        try:
            Path(LOG_FILE).write_text("", encoding="utf-8")
        except Exception:
            pass
        self.ui_queue.put(("log", "Upload log reset.", "WARNING"))
        self.refresh_queue()

    # -------------------------------------------------------------- editor/file
    def open_editor(self, video_path: Path):
        EditMetadataDialog(self, video_path)

    def open_file(self, path: Path):
        try:
            os.startfile(str(Path(path).resolve()))  # type: ignore[attr-defined]
        except Exception as e:
            messagebox.showinfo("Open", f"Could not open:\n{path}\n\n{e}")

    # ------------------------------------------------------------- folder tools
    def _browse_folder(self):
        d = filedialog.askdirectory(title="Select the videos folder")
        if d:
            self.folder_entry.delete(0, "end")
            self.folder_entry.insert(0, d)
            self._apply_folder_and_refresh()

    def _browse_into(self, entry: ctk.CTkEntry, is_dir: bool):
        path = filedialog.askdirectory() if is_dir else filedialog.askopenfilename()
        if path:
            entry.delete(0, "end")
            entry.insert(0, path)

    def _apply_folder_and_refresh(self):
        self.cfg["video_folder"] = self.folder_entry.get().strip() or "videos"
        if "video_folder" in self.settings_widgets:
            self.settings_widgets["video_folder"].delete(0, "end")
            self.settings_widgets["video_folder"].insert(0, self.cfg["video_folder"])
        self.refresh_queue()

    # ----------------------------------------------------------------- settings
    def _collect_settings(self) -> dict:
        w = self.settings_widgets
        cfg = dict(self.cfg)
        for key, widget in w.items():
            if isinstance(widget, ctk.CTkSwitch):
                cfg[key] = bool(widget.get())
            elif isinstance(widget, ctk.CTkOptionMenu):
                cfg[key] = widget.get()
            elif isinstance(widget, ctk.CTkTextbox):
                cfg[key] = widget.get("1.0", "end").rstrip("\n")
            else:  # CTkEntry
                cfg[key] = widget.get()
        # numeric coercion
        for numkey, default in (("upload_delay_seconds", 60), ("max_retries", 1)):
            try:
                cfg[numkey] = int(str(cfg.get(numkey, default)).strip() or default)
            except ValueError:
                cfg[numkey] = default
        for key in ("video_folder", "cover_dir", "oauth_client_file"):
            cfg[key] = str(APP_DIR / (cfg[key].strip() or DEFAULT_CONFIG[key]))
        cfg["appearance_mode"] = self.appearance_menu.get()
        cfg["accent_color"] = self.accent_menu.get()
        return cfg

    def save_settings(self):
        self.cfg = self._collect_settings()
        self.cfg_data = self.cfg
        # keep the top folder entry in sync
        self.folder_entry.delete(0, "end")
        self.folder_entry.insert(0, self.cfg.get("video_folder", "videos"))
        if save_config(self.cfg):
            self.settings_saved_lbl.configure(text="✓ Saved")
            self.after(2500, lambda: self.settings_saved_lbl.configure(text=""))
            self.ui_queue.put(("log", "Settings saved to yt_config.json", "SUCCESS"))
            self.refresh_queue()
        else:
            self.settings_saved_lbl.configure(text="✗ Save failed", text_color=STATUS_COLORS["failed"])

    def reset_settings(self):
        if not messagebox.askyesno("Reset settings", "Restore all settings to defaults?"):
            return
        self.cfg = dict(DEFAULT_CONFIG)
        self.cfg_data = self.cfg
        save_config(self.cfg)
        # rebuild settings page to reflect defaults
        self.pages["settings"].destroy()
        self.settings_widgets = {}
        self._build_settings_page()
        self.show_page("settings")
        self.folder_entry.delete(0, "end")
        self.folder_entry.insert(0, self.cfg.get("video_folder", "videos"))
        self.refresh_queue()

    # --------------------------------------------------------------- schedule
    def _collect_schedule(self):
        w = self.sched_widgets
        for key, widget in w.items():
            if isinstance(widget, ctk.CTkSwitch):
                self.cfg[key] = bool(widget.get())
            else:
                self.cfg[key] = widget.get()
        try:
            self.cfg["videos_per_day"] = max(1, int(str(self.cfg.get("videos_per_day", 5)).strip() or 5))
        except ValueError:
            self.cfg["videos_per_day"] = 5
        t = str(self.cfg.get("daily_time", "10:00")).strip()
        if not re.match(r"^\d{1,2}:\d{2}$", t):
            t = "10:00"
        self.cfg["daily_time"] = t

    def save_schedule_settings(self):
        self._collect_schedule()
        if save_config(self.cfg):
            self.ui_queue.put(("log", "Schedule settings saved.", "SUCCESS"))
            messagebox.showinfo("Saved", "Schedule settings saved to yt_config.json.")

    def enable_daily_task(self):
        self._collect_schedule()
        save_config(self.cfg)
        ok = create_daily_task(self.cfg.get("daily_time", "10:00"),
                               log=lambda m, l="INFO": self.ui_queue.put(("log", m, l)))
        if ok:
            messagebox.showinfo(
                "Daily task enabled",
                f"The app will open and upload {self.cfg.get('videos_per_day', 5)} video(s) "
                f"every day at {self.cfg.get('daily_time', '10:00')}.\n\n"
                "Tip: make sure you've logged in once so the saved session can upload unattended.")
        else:
            messagebox.showerror("Failed", "Could not create the Windows task. See the Logs tab.")
        self.refresh_task_status()

    def disable_daily_task(self):
        delete_daily_task(log=lambda m, l="INFO": self.ui_queue.put(("log", m, l)))
        self.refresh_task_status()

    def refresh_task_status(self):
        try:
            exists = daily_task_exists()
        except Exception:
            exists = False
        if exists:
            self.task_status.configure(
                text=f"● Daily task ACTIVE  —  {self.cfg.get('daily_time', '10:00')}, "
                     f"{self.cfg.get('videos_per_day', 5)} videos/day",
                text_color=STATUS_COLORS["uploaded"])
        else:
            self.task_status.configure(text="○ Daily task not set.", text_color=STATUS_COLORS["pending"])

    def _check_schedule(self):
        """In-app daily timer (only fires while the app is open)."""
        try:
            armed = "run_daily_while_open" in getattr(self, "sched_widgets", {}) and bool(self.sched_switch.get())
            busy = self.worker and self.worker.is_alive()
            if armed and not busy:
                now = time.strftime("%H:%M")
                today = time.strftime("%Y-%m-%d")
                if now == str(self.cfg.get("daily_time", "10:00")).strip() and self._last_sched_date != today:
                    self._last_sched_date = today
                    self.ui_queue.put(("log", f"⏰ Scheduled daily run triggered at {now}.", "SUCCESS"))
                    self._auto_start_batch()
        except Exception:
            pass
        self.after(30000, self._check_schedule)

    # -------------------------------------------------------------- appearance
    def _change_appearance(self, value: str):
        ctk.set_appearance_mode(value)
        self.cfg["appearance_mode"] = value

    def _change_accent(self, value: str):
        self.cfg["accent_color"] = value
        messagebox.showinfo("Accent theme",
                            "The accent colour is applied the next time you launch the app.")

    # -------------------------------------------------------- engine callbacks
    def _cb_log(self, msg, level="INFO"):
        self.ui_queue.put(("log", msg, level))

    def _cb_progress(self, done, total, name):
        self.ui_queue.put(("progress", done, total, name))

    def _cb_status(self, state, info):
        self.ui_queue.put(("status", state, info))

    def _ensure_engine(self) -> UploaderEngine:
        if self.engine is None:
            self.engine = UploaderEngine(
                self.cfg,
                log_callback=self._cb_log,
                progress_callback=self._cb_progress,
                status_callback=self._cb_status,
            )
        else:
            self.engine.config = self.cfg
        return self.engine

    # ------------------------------------------------------------- run actions
    def _busy(self, busy: bool, mode: str = None):
        self._mode = mode if busy else None
        self.start_btn.configure(state="disabled" if busy else "normal")
        self.dry_btn.configure(state="disabled" if busy else "normal")
        self.login_btn.configure(state="disabled" if busy else "normal")
        self.stop_btn.configure(state="normal" if busy else "disabled")
        self.pause_btn.configure(state="normal" if (busy and mode == "upload") else "disabled",
                                 text="⏸ Pause")
        for b in getattr(self, "tg_action_buttons", []):
            try:
                b.configure(state="disabled" if busy else "normal")
            except Exception:
                pass

    def login(self):
        if self.worker and self.worker.is_alive():
            return
        self.cfg["video_folder"] = self.folder_entry.get().strip() or "videos"
        client_file = APP_DIR / self.settings_widgets["oauth_client_file"].get().strip()
        self.cfg["oauth_client_file"] = str(client_file)
        save_config(self.cfg)
        eng = self._ensure_engine()
        eng.stop_event.clear()
        self._busy(True, "login")
        self.set_status("Checking YouTube connection…")

        def worker():
            try:
                eng.setup_login(choose_client=self._choose_client)
            except Exception as e:
                self.ui_queue.put(("log", f"Connection error: {e}", "ERROR"))
                self.ui_queue.put(("status", "error", {"message": str(e)}))
            finally:
                self.ui_queue.put(("worker_done", "login"))

        self.worker = threading.Thread(target=worker, daemon=True)
        self.worker.start()

    def _selected_videos(self):
        folder = Path(self.cfg.get("video_folder", "videos"))
        return [folder / name for name, r in self.rows.items() if r.var.get()]

    def start_upload(self):
        if self.worker and self.worker.is_alive():
            return
        self.cfg["video_folder"] = self.folder_entry.get().strip() or "videos"
        selected = self._selected_videos()
        if not selected:
            messagebox.showinfo("Nothing selected", "Tick at least one video in the Queue tab.")
            return
        # Never re-upload videos already recorded in uploaded_videos.log.
        uploaded = load_uploaded_log()
        videos = [v for v in selected if v.name not in uploaded]
        already = len(selected) - len(videos)
        if not videos:
            messagebox.showinfo(
                "Already uploaded",
                "All selected videos are already uploaded (recorded in uploaded_videos.log), "
                "so nothing will be re-uploaded.\n\nUse '↺ Reset upload log' if you really want to post them again.")
            return
        msg = (f"Upload {len(videos)} video(s) to your YouTube channel with visibility "
               f"'{self.cfg.get('visibility', 'PUBLIC')}'?")
        if already:
            msg += f"\n\n({already} already-uploaded video(s) will be skipped.)"
        msg += "\n\nMake sure you are logged in to the correct account."
        if not messagebox.askyesno("Confirm upload", msg):
            return

        self._launch_upload(videos, interactive=True)

    def _launch_upload(self, videos, auto_close: bool = False, *, interactive=False):
        if self.worker and self.worker.is_alive():
            return
        self._auto_close = auto_close
        eng = self._ensure_engine()
        self._busy(True, "upload")
        self.progress.set(0)
        self.set_status(f"Starting upload of {len(videos)} video(s)…")
        self.show_page("logs")

        def worker():
            try:
                # skip_uploaded=True: the engine also refuses to re-post anything
                # already in uploaded_videos.log, as a second safety net.
                eng.run(videos=videos, skip_uploaded=True,
                        choose_client=self._choose_client if interactive else None)
            except Exception as e:
                self.ui_queue.put(("log", f"Run error: {e}", "ERROR"))
                self.ui_queue.put(("status", "error", {"message": str(e)}))
            finally:
                self.ui_queue.put(("worker_done", "upload"))

        self.worker = threading.Thread(target=worker, daemon=True)
        self.worker.start()

    def _auto_start_batch(self):
        """Auto-upload the next N pending videos (used by --autorun and the daily timer)."""
        if self.worker and self.worker.is_alive():
            return
        self.cfg["video_folder"] = self.folder_entry.get().strip() or "videos"
        n = int(self.cfg.get("videos_per_day", 5) or 5)
        folder = Path(self.cfg.get("video_folder", "videos"))
        uploaded = load_uploaded_log()
        pending = [folder / name for name in self.rows if name not in uploaded][:n]
        if not pending:
            self.ui_queue.put(("log", "Auto-run: no pending videos to upload.", "WARNING"))
            self.set_status("Auto-run: nothing pending.")
            if self.cfg.get("auto_close_when_done", False):
                self.after(4000, self.destroy)
            return
        self.ui_queue.put(("log", f"⏰ Auto-run: uploading {len(pending)} video(s)…", "SUCCESS"))
        self._launch_upload(pending, auto_close=self.cfg.get("auto_close_when_done", False))

    def generate_covers(self):
        if self.worker and self.worker.is_alive():
            return
        self.cfg["video_folder"] = self.folder_entry.get().strip() or "videos"
        folder = Path(self.cfg.get("video_folder", "videos"))
        vids = self._selected_videos() or [folder / n for n in self.rows]
        if not vids:
            messagebox.showinfo("No videos", "There are no videos to make covers for.")
            return
        eng = self._ensure_engine()
        eng.stop_event.clear()
        self._busy(True, "covers")
        self.progress.set(0)
        self.set_status(f"Generating {len(vids)} cover(s)…")
        self.show_page("logs")

        def worker():
            total = len(vids)
            try:
                for i, v in enumerate(vids, 1):
                    if eng.stop_event.is_set():
                        break
                    try:
                        a = analyze_video(v, self.cfg, log=lambda m, l="INFO": self.ui_queue.put(("log", m, l)))
                        path = generate_cover(v, self.cfg, a,
                                              log=lambda m, l="INFO": self.ui_queue.put(("log", m, l)))
                        if path:
                            try:
                                from PIL import Image as _I
                                im = _I.open(path)
                                im.thumbnail((118, 66), _I.LANCZOS)
                                self.ui_queue.put(("thumb", Path(v).name, im))
                            except Exception:
                                pass
                    except Exception as e:
                        self.ui_queue.put(("log", f"Cover failed for {Path(v).name}: {e}", "ERROR"))
                    self.ui_queue.put(("progress", i, total, Path(v).name))
            finally:
                self.ui_queue.put(("worker_done", "covers"))

        self.worker = threading.Thread(target=worker, daemon=True)
        self.worker.start()

    # ----------------------------------------------------------- Telegram
    def _collect_tg(self):
        """Read the Telegram tab widgets back into the config dict."""
        w = self.tg_widgets
        if not w:
            return
        self.cfg["tg_api_id"] = w["tg_api_id"].get().strip()
        self.cfg["tg_api_hash"] = w["tg_api_hash"].get().strip()
        self.cfg["tg_phone"] = w["tg_phone"].get().strip()
        self.cfg["tg_proxy"] = w["tg_proxy"].get().strip()
        self.cfg["tg_channel"] = w["tg_channel"].get().strip()
        try:
            self.cfg["tg_download_limit"] = max(0, int(str(w["tg_download_limit"].get()).strip() or 10))
        except ValueError:
            self.cfg["tg_download_limit"] = 10
        self.cfg["tg_video_only"] = bool(w["tg_video_only"].get())
        self.cfg["tg_auto_upload"] = bool(w["tg_auto_upload"].get())

    def save_tg_settings(self):
        self._collect_tg()
        if save_config(self.cfg):
            self._set_tg_status("✓ Telegram settings saved to yt_config.json.", "uploaded")
            self.ui_queue.put(("log", "Telegram settings saved.", "SUCCESS"))

    def _set_tg_status(self, text, color_key=None):
        try:
            col = STATUS_COLORS.get(color_key) if color_key else ("#6b7280", "#9aa4b2")
            self.tg_status_lbl.configure(text=text, text_color=col or ("#6b7280", "#9aa4b2"))
        except Exception:
            pass

    def _choose_client(self):
        return self._ask_from_main("Select the Google Desktop OAuth client JSON", "Connect YouTube", kind="choose_client")

    def _ask_from_main(self, prompt, title="Telegram", *, kind="ask"):
        """Ask the user for a value on the main thread (safe to call from a worker)."""
        holder = {"value": None}
        ev = threading.Event()
        self.ui_queue.put((kind, prompt, title, holder, ev))
        ev.wait()
        return holder["value"]

    def _tg_ready(self) -> bool:
        if tg_mod is None or not getattr(tg_mod, "HAS_TELETHON", False):
            messagebox.showerror("Telegram", "Run install_telegram.bat, then reopen the app.")
            return False
        if not self.cfg.get("tg_api_id") or not self.cfg.get("tg_api_hash"):
            messagebox.showwarning(
                "API keys needed",
                "Enter your Telegram API ID and API Hash first.\n\n"
                "Create them for free at https://my.telegram.org → 'API development tools'.")
            self.show_page("telegram")
            return False
        return True

    def tg_login(self):
        if self.worker and self.worker.is_alive():
            return
        self._collect_tg()
        save_config(self.cfg)
        if not self._tg_ready():
            return
        self._busy(True, "tglogin")
        self.set_status("Logging in to Telegram…")
        self._set_tg_status("Opening Telegram login… watch for the code in your Telegram app.")
        self.show_page("logs")

        def code_cb():
            return self._ask_from_main(
                "Enter the Telegram login code\n(sent to your Telegram app / SMS):",
                "Telegram login code")

        def pw_cb():
            return self._ask_from_main(
                "Two-step verification password\n(leave blank if you don't use one):",
                "Telegram 2FA password")

        def worker():
            try:
                ok = tg_mod.login_from_config(
                    self.cfg, code_cb=code_cb, password_cb=pw_cb,
                    log=lambda m, l="INFO": self.ui_queue.put(("log", m, l)))
                self.ui_queue.put(("log",
                                   "Telegram login OK — you can Download now." if ok
                                   else "Telegram login failed. See messages above.",
                                   "SUCCESS" if ok else "ERROR"))
            except Exception as e:
                self.ui_queue.put(("log", f"Telegram login error: {e}", "ERROR"))
            finally:
                self.ui_queue.put(("worker_done", "tglogin"))

        self.worker = threading.Thread(target=worker, daemon=True)
        self.worker.start()

    def tg_download(self, auto: bool = False):
        if self.worker and self.worker.is_alive():
            return
        self._collect_tg()
        self.cfg["video_folder"] = self.folder_entry.get().strip() or "videos"
        save_config(self.cfg)
        if not self._tg_ready():
            return
        if not self.cfg.get("tg_channel"):
            messagebox.showwarning("No channel",
                                   "Enter the Telegram channel to download from "
                                   "(e.g. @name or https://t.me/name).")
            self.show_page("telegram")
            return
        self._tg_auto = auto or self.cfg["tg_auto_upload"]
        self._tg_downloaded = []
        self._tg_stop = False
        self._busy(True, "tgdownload")
        self.progress.set(0)
        lim = self.cfg.get("tg_download_limit", 10)
        self.set_status(f"Downloading from Telegram ({'all new' if not lim else lim})…")
        self._set_tg_status(f"Downloading from {self.cfg.get('tg_channel')} …")
        self.show_page("logs")

        def worker():
            try:
                saved = tg_mod.download_from_config(
                    self.cfg,
                    log=lambda m, l="INFO": self.ui_queue.put(("log", m, l)),
                    progress=lambda i, t, n: self.ui_queue.put(("progress", i, t, n)),
                    stop_check=lambda: self._tg_stop)
                self._tg_downloaded = saved or []
            except Exception as e:
                self.ui_queue.put(("log", f"Telegram download error: {e}", "ERROR"))
                self._tg_downloaded = []
            finally:
                self.ui_queue.put(("worker_done", "tgdownload"))

        self.worker = threading.Thread(target=worker, daemon=True)
        self.worker.start()

    def start_dryrun(self):
        if self.worker and self.worker.is_alive():
            return
        self.cfg["video_folder"] = self.folder_entry.get().strip() or "videos"
        videos = self._selected_videos() or [Path(self.cfg["video_folder"]) / n for n in self.rows]
        if not videos:
            messagebox.showinfo("No videos", "There are no videos to preview.")
            return
        eng = self._ensure_engine()
        eng.stop_event.clear()
        self._busy(True, "dryrun")
        self.progress.set(0)
        self.set_status("Generating metadata preview…")
        self.show_page("logs")

        def worker():
            try:
                results = eng.dry_run(videos=videos)
                for r in results:
                    self.ui_queue.put(("log", f"── {r['file']}", "SUCCESS"))
                    self.ui_queue.put(("log", f"   title: {r['title']}", "INFO"))
                    self.ui_queue.put(("log", f"   tags : {', '.join(r['tags'])}", "INFO"))
            except Exception as e:
                self.ui_queue.put(("log", f"Dry-run error: {e}", "ERROR"))
            finally:
                self.ui_queue.put(("worker_done", "dryrun"))

        self.worker = threading.Thread(target=worker, daemon=True)
        self.worker.start()

    def toggle_pause(self):
        if not self.engine:
            return
        if self.engine.is_paused:
            self.engine.resume()
            self.pause_btn.configure(text="⏸ Pause")
        else:
            self.engine.pause()
            self.pause_btn.configure(text="▶ Resume")

    def stop_run(self):
        self._tg_stop = True
        if self.engine:
            self.engine.request_stop()
        self.set_status("Stopping…")

    # ------------------------------------------------------------------ status
    def set_status(self, text: str):
        self.status_lbl.configure(text=text)

    # -------------------------------------------------------------- UI polling
    def _poll(self):
        try:
            while True:
                item = self.ui_queue.get_nowait()
                kind = item[0]
                if kind == "log":
                    self._append_log(item[1], item[2] if len(item) > 2 else "INFO")
                elif kind == "progress":
                    self._on_progress(item[1], item[2], item[3])
                elif kind == "status":
                    self._on_status(item[1], item[2])
                elif kind == "thumb":
                    row = self.rows.get(item[1])
                    if row:
                        row.set_thumbnail(item[2])
                elif kind in ("ask", "choose_client"):
                    _, prompt, title, holder, ev = item
                    try:
                        if kind == "choose_client":
                            chosen = pick_client_file(parent=self)
                            if chosen:
                                self.cfg["oauth_client_file"] = chosen
                                entry = self.settings_widgets["oauth_client_file"]
                                entry.delete(0, "end")
                                entry.insert(0, chosen)
                                save_config(self.cfg)
                            holder["value"] = chosen
                        else:
                            holder["value"] = simpledialog.askstring(title, prompt, show="*", parent=self)
                    except Exception:
                        holder["value"] = None
                    ev.set()
                elif kind == "worker_done":
                    self._on_worker_done(item[1])
        except queue.Empty:
            pass
        self.after(80, self._poll)

    def _append_log(self, msg: str, level: str = "INFO"):
        stamp = ""
        try:
            self.log_box.configure(state="normal")
            line = f"{msg}\n"
            try:
                self.log_box.insert("end", line, level)
            except Exception:
                self.log_box.insert("end", line)
            if self.autoscroll.get():
                self.log_box.see("end")
            self.log_box.configure(state="disabled")
        except Exception:
            print(f"[{level}] {msg}")

    def _on_progress(self, done, total, name):
        if total:
            self.progress.set(done / total)
            self.set_status(f"{done}/{total}  ·  {name}")
        # reflect per-row status
        row = self.rows.get(name)
        if row and self._mode == "upload":
            uploaded = load_uploaded_log()
            row.set_status("Uploaded" if name in uploaded else "Failed",
                           "uploaded" if name in uploaded else "failed")
        self.update_stats()

    def _on_status(self, state, info):
        if state == "uploading" and self._mode == "upload":
            name = info.get("name", "")
            self.set_status(f"Uploading {info.get('index','')}/{info.get('total','')} · {name}")
            row = self.rows.get(name)
            if row:
                row.set_status("Uploading…", "uploading")
        elif state == "connecting":
            self.set_status("Authorize the channel in your browser…")
        elif state == "logged_in":
            self.set_status("YouTube channel connected.")
        elif state == "error":
            self._auto_close = False
            self.set_status(info["message"])
        elif state in ("finished", "stopped"):
            summ = info.get("summary", {})
            if summ:
                self.set_status(f"Done · uploaded {summ.get('uploaded',0)}, "
                                f"failed {summ.get('failed',0)}, skipped {summ.get('skipped',0)}")

    def _on_worker_done(self, mode):
        self._busy(False)
        if mode == "upload":
            self.refresh_queue()
            if self._auto_close:
                self._auto_close = False
                self.ui_queue.put(("log", "Auto-run finished — closing the app.", "INFO"))
                self.after(4000, self._on_close)
        elif mode == "dryrun":
            self.progress.set(1.0)
            self.set_status("Preview complete — see the Logs tab.")
        elif mode == "covers":
            self.progress.set(1.0)
            self.set_status("Covers generated — saved in the 'covers' folder.")
        elif mode == "tglogin":
            self.set_status("Telegram ready.")
            self._set_tg_status("Telegram login finished — see the Logs tab for the result.")
        elif mode == "tgdownload":
            self.progress.set(1.0)
            self.refresh_queue()
            n = len(getattr(self, "_tg_downloaded", []))
            self.set_status(f"Telegram: {n} new video(s) downloaded.")
            self._set_tg_status(
                f"✓ Downloaded {n} new video(s) into '{self.cfg.get('video_folder', 'videos')}'.",
                "uploaded" if n else None)
            if self._tg_auto and not self._tg_stop and self._tg_downloaded:
                self._tg_auto = False
                uploaded = load_uploaded_log()
                vids = [Path(p) for p in self._tg_downloaded if Path(p).name not in uploaded]
                if vids:
                    self.ui_queue.put(
                        ("log", f"▶ Auto-uploading {len(vids)} freshly downloaded video(s)…", "SUCCESS"))
                    # let this worker fully finish before launching the upload worker
                    self.after(500, lambda v=vids: self._launch_upload(v, interactive=True))
                else:
                    self.ui_queue.put(("log", "Nothing new to upload (already on YouTube).", "WARNING"))
            else:
                self._tg_auto = False

    # -------------------------------------------------------------------- logs
    def _clear_logs(self):
        self.log_box.configure(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.configure(state="disabled")

    def _save_logs(self):
        path = filedialog.asksaveasfilename(defaultextension=".log",
                                            filetypes=[("Log files", "*.log"), ("All files", "*.*")],
                                            initialfile="yt_uploader_session.log")
        if path:
            try:
                Path(path).write_text(self.log_box.get("1.0", "end"), encoding="utf-8")
            except Exception as e:
                messagebox.showerror("Save failed", str(e))

    # ------------------------------------------------------------------- close
    def _on_close(self):
        if self.worker and self.worker.is_alive():
            if not messagebox.askyesno("Quit", "A task is running. Stop it and quit?"):
                return
            if self.engine:
                self.engine.request_stop()
        self._tg_stop = True
        self.destroy()


def main(autorun: bool = False):
    if "--autorun" in sys.argv:
        autorun = True
    app = UploaderApp(autorun=autorun)
    app.mainloop()


if __name__ == "__main__":
    main()
