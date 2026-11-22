#!/usr/bin/env python3
"""YouTube desktop uploader, Telegram source and Windows daily scheduler."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import mimetypes
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseUpload
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageStat

import uploader
import desktop_oauth

import video_content

# Older Windows consoles may otherwise reject Persian text and Unicode status icons.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

APP_DIR = Path(__file__).resolve().parent
CONFIG_FILE = APP_DIR / "yt_config.json"
LOG_FILE = APP_DIR / "uploaded_videos.log"
DEFAULT_VIDEO_DIR = "videos"
VIDEO_EXTENSIONS = uploader.EXTENSIONS | {".flv", ".ts", ".3gp", ".mpg"}
IMAGE_EXTENSIONS = [".jpg", ".jpeg", ".png", ".webp"]
VISIBILITY_CHOICES = ["PRIVATE", "UNLISTED", "PUBLIC"]
APP_VERSION = "5.1"
DEFAULT_CONFIG = json.loads((APP_DIR / "yt_config.example.json").read_text(encoding="utf-8"))

class StopRequested(Exception):
    """Raised internally to unwind the upload loop when a stop is requested."""


# ---------------------------------------------------------------------------
# Plain console logging (used by the CLI and as a fallback)
# ---------------------------------------------------------------------------
def log_message(msg: str, level: str = "INFO") -> None:
    """Print formatted logs with timestamps (console fallback)."""
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] [{level}] {msg}"
    try:
        print(line)
    except UnicodeEncodeError:
        enc = (getattr(sys.stdout, "encoding", None) or "utf-8")
        print(line.encode(enc, "replace").decode(enc, "replace"))



def load_config(path=None):
    path = Path(path or CONFIG_FILE)
    config = dict(DEFAULT_CONFIG)
    if path.exists():
        # A damaged settings file must not be silently replaced with defaults.
        saved = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(saved, dict):
            raise ValueError(f"Expected an object in {path}")
        config.update({key: value for key, value in saved.items() if value is not None})
        if "metadata_version" not in saved:
            # Upgrade the old defaults in memory. A subsequent settings save records the version.
            config["use_ai_ocr"] = True
            for field in ("telegram_channel", "telegram_owner"):
                if not str(config[field]).strip():
                    config[field] = DEFAULT_CONFIG[field]
    else:
        if not save_config(config, path):
            raise OSError(f"Cannot create settings in {path.parent}")
    for key in ("video_folder", "cover_dir", "oauth_client_file"):
        config[key] = str(path.parent / (config[key] or DEFAULT_CONFIG[key]))
    return config


def save_config(config, path=None):
    path = Path(path or CONFIG_FILE)
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         delete=False) as tmp:
            json.dump(config, tmp, indent=2, ensure_ascii=False)
        os.replace(tmp.name, path)
        return True
    except OSError as error:
        log_message(f"Could not save settings: {error}", "ERROR")
        return False


def load_uploaded_log(path=None):
    try:
        return set(Path(path or LOG_FILE).read_text(encoding="utf-8-sig").splitlines())
    except FileNotFoundError:
        return set()


def mark_as_uploaded(filename, path=None):
    with Path(path or LOG_FILE).open("a", encoding="utf-8") as log:
        log.write(filename + "\n")


def list_videos(folder) -> list:
    """Return sorted list of video Paths inside *folder*."""
    video_dir = Path(folder)
    if not video_dir.exists():
        return []
    return sorted(
        [p for p in video_dir.iterdir() if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS],
        key=lambda p: p.name.lower(),
    )


def find_companion_image(video_path: Path):
    """Return a sibling image (same stem) to use as a custom thumbnail, if any."""
    for ext in IMAGE_EXTENSIONS:
        candidate = video_path.with_suffix(ext)
        if candidate.exists():
            return candidate
    return None


def generate_thumbnail_image(video_path: Path, size=(220, 124)):
    """Grab a representative middle frame and return a resized PIL.Image (or None)."""
    try:
        for image in video_content.sample_frames(video_path, (0.5,)):
            image.thumbnail(size, Image.Resampling.LANCZOS)
            return image
    except (OSError, ValueError, IndexError, video_content.av.FFmpegError):
        pass
    return None


def save_companion_metadata(video_path: Path, title: str, description: str, tags) -> bool:
    """Persist manual metadata next to the video as ``<stem>.json`` (read on upload)."""
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    data = {"title": title, "description": description, "tags": tags}
    try:
        with open(video_path.with_suffix(".json"), "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return True
    except Exception as e:
        log_message(f"Error writing companion metadata: {e}", "ERROR")
        return False


def public_telegram_url(value):
    match = re.fullmatch(r"(?:@|(?:https?://)?t\.me/)?([a-zA-Z][a-zA-Z0-9_]{2,31})/?", str(value or "").strip())
    if match and match[1].lower() not in {"joinchat", "share", "proxy", "socks", "login", "invite"}:
        return "https://t.me/" + match[1]
    return ""


def analyze_video(video_path, config, log=log_message, *, supplied_title="", supplied_description=""):
    video = Path(video_path)
    embedded = video_content.metadata(video)
    language = config.get("title_language", "auto")
    title = next((v.strip() for v in (supplied_title, embedded.get("title"))
                  if isinstance(v, str) and video_content.substantive(v)), "")
    description = next((v.strip() for v in (supplied_description, embedded.get("description"), embedded.get("comment"))
                        if isinstance(v, str) and video_content.substantive(v)), "")
    if not title and description:
        title = video_content.substantive(description)[0]
    topics = []
    ocr = []
    if not title or not description:
        topics = video_content.subtitle_topics(video)
    wants_ocr_text = any("{ocr_text}" in str(config.get(k, "")) for k in ("title_template", "description_template"))
    if config.get("use_ai_ocr", True) and (wants_ocr_text or (not topics and (not title or not description))):
        log(f"Reading text from video frames: {video.name}")
        ocr = video_content.screen_topics(video, language, log)
    topics = topics or ocr
    if not title and topics:
        title = topics[0]
    if title and not description:
        extra = [line for line in topics if video_content.key(line) != video_content.key(title)]
        if language == "en":
            description = f'This video shows “{title}”.'
            if extra:
                description += "\n\nAlso in the video\n" + "\n".join("• " + line for line in extra)
        else:
            description = f"در این ویدیو «{title}» را می‌بینید."
            if extra:
                description += "\n\nدر بخش‌های دیگر ویدیو\n" + "\n".join("• " + line for line in extra)
    date = time.strftime("%Y-%m-%d %H:%M", time.localtime(video.stat().st_mtime))
    details = " · ".join(filter(None, [video.suffix[1:].upper(), embedded.get("duration"), embedded.get("resolution")]))
    return {
        "date": date, "details": details, "filename": video.name, "stem": video.stem,
        "title": title, "description": description, "year": str(time.localtime().tm_year),
        "ocr_text": "\n".join(ocr), "telegram_channel": public_telegram_url(config.get("telegram_channel")),
        "telegram_owner": public_telegram_url(config.get("telegram_owner")),
    }


def render_template(template, fields, fallback, log):
    # Older templates can reference fields that the desktop uploader no longer supplies.
    unknown = set(re.findall(r"\{(\w+)\}", template)).difference(fields)
    if unknown:
        log(f"Review the template in Settings. Unsupported fields: {', '.join(sorted(unknown))}. "
            "Using the default for this field.", "WARNING")
        return fallback
    return re.sub(r"\{(\w+)\}", lambda match: fields[match[1]], template).strip()


_DEFAULT_ACCENTS = [
    (124, 58, 237), (37, 99, 235), (219, 39, 119), (5, 150, 105),
    (234, 88, 12), (220, 38, 38), (2, 132, 199), (147, 51, 234),
]
_FONT_PATHS = {
    "black": [r"C:\Windows\Fonts\ariblk.ttf", r"C:\Windows\Fonts\impact.ttf", r"C:\Windows\Fonts\arialbd.ttf"],
    "bold": [r"C:\Windows\Fonts\arialbd.ttf", r"C:\Windows\Fonts\segoeuib.ttf"],
    "reg": [r"C:\Windows\Fonts\segoeui.ttf", r"C:\Windows\Fonts\arial.ttf"],
}


def _cover_font(size, kind="black"):
    for p in _FONT_PATHS.get(kind, _FONT_PATHS["black"]):
        try:
            return ImageFont.truetype(p, size)
        except Exception:
            continue
    try:
        return ImageFont.truetype("DejaVuSans-Bold.ttf", size)
    except OSError:
        return ImageFont.load_default()


def _text_w(font, text):
    try:
        b = font.getbbox(text)
        return b[2] - b[0]
    except Exception:
        return len(text) * 10


def _fit_cover_font(text, max_w, start, kind="black", min_size=44):
    size = start
    while size > min_size:
        f = _cover_font(size, kind)
        if f is None or _text_w(f, text) <= max_w:
            return f
        size -= 4
    while size > 12 and _text_w(_cover_font(size, kind), text) > max_w:
        size -= 2
    return _cover_font(size, kind)


def _cover_accent(analysis):
    seed = hashlib.sha256(analysis["title"].encode("utf-8")).digest()[0]
    return _DEFAULT_ACCENTS[seed % len(_DEFAULT_ACCENTS)]


def _cover_crop(img, size):
    tw, th = size
    iw, ih = img.size
    if iw <= 0 or ih <= 0:
        return Image.new("RGB", size, (18, 18, 22))
    scale = max(tw / iw, th / ih)
    nw, nh = max(1, int(iw * scale)), max(1, int(ih * scale))
    img = img.resize((nw, nh), Image.LANCZOS)
    left, top = (nw - tw) // 2, (nh - th) // 2
    return img.crop((left, top, left + tw, top + th))


def _best_frame(video_path, size=(1280, 720)):
    best, best_score = None, -1.0
    try:
        for image in video_content.sample_frames(video_path, (0.2, 0.35, 0.5, 0.65, 0.8)):
            stats = ImageStat.Stat(image.convert("L").resize((160, 90)))
            score = stats.stddev[0] * (1.0 if 35 < stats.mean[0] < 225 else 0.35)
            if score > best_score:
                best, best_score = image, score
    except (OSError, ValueError, IndexError, video_content.av.FFmpegError):
        pass
    return _cover_crop(best, size) if best is not None else None


COVER_STYLES = ["bold", "gaming", "minimal", "spotlight"]


def _cover_bottom(draw, W, H, cfg, accent, margin=74):
    bf = _cover_font(38, "bold")
    tg = public_telegram_url(cfg.get("telegram_channel"))
    tg = tg.replace("https://t.me/", "@").replace("http://t.me/", "@").replace("https://", "").replace("http://", "")
    tri_y = H - 48
    draw.polygon([(margin, tri_y - 15), (margin, tri_y + 15), (margin + 26, tri_y)], fill=accent)
    draw.text((margin + 40, H - 62), tg, font=bf, fill=(255, 255, 255))
    lf = _cover_font(42, "black")
    lx = W - _text_w(lf, "LIONXSEC") - 44
    draw.text((lx, H - 64), "LIONXSEC", font=lf, fill=accent, stroke_width=2, stroke_fill=(0, 0, 0))


def _glow_text(base, xy, text, font, glow, text_color=(255, 255, 255), stroke=3):
    W, H = base.size
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(layer).text(xy, text, font=font, fill=(glow[0], glow[1], glow[2], 255))
    layer = layer.filter(ImageFilter.GaussianBlur(13))
    base.paste(layer, (0, 0), layer)
    base.paste(layer, (0, 0), layer)
    ImageDraw.Draw(base).text(xy, text, font=font, fill=text_color, stroke_width=stroke, stroke_fill=(0, 0, 0))


def _new_burst(draw, cx, cy, r, fill=(239, 68, 68), text="NEW"):
    pts = []
    n = 12
    for i in range(n * 2):
        ang = (math.pi / n) * i - math.pi / 2
        rad = r if i % 2 == 0 else r * 0.6
        pts.append((cx + rad * math.cos(ang), cy + rad * math.sin(ang)))
    draw.polygon(pts, fill=fill)
    nf = _cover_font(max(20, int(r * 0.62)), "black")
    tw = _text_w(nf, text)
    draw.text((cx - tw / 2, cy - r * 0.4), text, font=nf, fill=(255, 255, 255),
              stroke_width=2, stroke_fill=(0, 0, 0))


def _frame_base(video_path, W, H, blur=2):
    bg = _best_frame(video_path, (W, H))
    base = bg.convert("RGB") if bg is not None else Image.new("RGB", (W, H), (18, 18, 24))
    if blur:
        try:
            base = base.filter(ImageFilter.GaussianBlur(blur))
        except Exception:
            pass
    return base


def _left_gradient(base, dark=(8, 8, 12)):
    W, H = base.size
    row = Image.new("L", (W, 1))
    for x in range(W):
        t = x / float(W)
        row.putpixel((x, 0), max(30, int(238 - 205 * min(1.0, max(0.0, (t - 0.04) / 0.72)))))
    base = Image.composite(Image.new("RGB", (W, H), dark), base, row.resize((W, H)))
    col = Image.new("L", (1, H))
    for y in range(H):
        t = y / float(H)
        col.putpixel((0, y), int(210 * max(0.0, (t - 0.74) / 0.26)))
    return Image.composite(Image.new("RGB", (W, H), (0, 0, 0)), base, col.resize((W, H)))


def _dim(base, amount=110):
    W, H = base.size
    return Image.blend(base, Image.new("RGB", (W, H), (6, 6, 10)), amount / 255.0)


def _vignette(base, strength=175):
    W, H = base.size
    mask = Image.new("L", (W, H), 0)
    ImageDraw.Draw(mask).ellipse([-W * 0.18, -H * 0.18, W * 1.18, H * 1.18], fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(150))
    dark = Image.new("RGB", (W, H), (0, 0, 0))
    base = Image.composite(base, dark, mask)
    return _dim(base, 60)


# ---- style builders (each returns a finished RGB image) --------------------
def _cover_bold(video_path, analysis, accent, cfg):
    W, H = 1280, 720
    base = _left_gradient(_frame_base(video_path, W, H))
    draw = ImageDraw.Draw(base)
    draw.rectangle([0, 0, 16, H], fill=accent)
    margin = 74
    brand = analysis["title"].upper()
    tf = _fit_cover_font(brand, W - margin - 330, 180, "black", 60)
    ty = 150
    draw.text((margin + 4, ty + 5), brand, font=tf, fill=(0, 0, 0))
    draw.text((margin, ty), brand, font=tf, fill=(255, 255, 255), stroke_width=3, stroke_fill=(0, 0, 0))
    title_h = tf.getbbox(brand)[3] - tf.getbbox(brand)[1]
    uy = ty + title_h + 34
    draw.rectangle([margin, uy, margin + min(_text_w(tf, brand), W - margin - 330), uy + 14], fill=accent)
    draw.text((margin, uy + 26), analysis["year"], font=_cover_font(66, "black"),
              fill=accent, stroke_width=2, stroke_fill=(0, 0, 0))
    _cover_bottom(draw, W, H, cfg, accent, margin)
    return base


def _cover_gaming(video_path, analysis, accent, cfg):
    W, H = 1280, 720
    base = _dim(_frame_base(video_path, W, H, blur=3), 120)
    # accent diagonal stripes (top-left)
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    for off in (-80, 40, 150):
        ld.polygon([(off, -20), (off + 70, -20), (off + 70 - 320, H * 0.55), (off - 320, H * 0.55)],
                   fill=(accent[0], accent[1], accent[2], 110))
    base.paste(layer, (0, 0), layer)
    draw = ImageDraw.Draw(base)
    margin = 70
    brand = analysis["title"].upper()
    tf = _fit_cover_font(brand, W - margin - 260, 190, "black", 64)
    ty = 150
    _glow_text(base, (margin, ty), brand, tf, accent)
    draw = ImageDraw.Draw(base)
    title_h = tf.getbbox(brand)[3] - tf.getbbox(brand)[1]
    uy = ty + title_h + 40
    draw.text((margin, uy), analysis["year"], font=_cover_font(70, "black"),
              fill=accent, stroke_width=3, stroke_fill=(0, 0, 0))
    # NEW burst top-right
    _new_burst(draw, W - 130, 130, 96, fill=(239, 68, 68))
    _cover_bottom(draw, W, H, cfg, accent, margin)
    return base


def _cover_minimal(video_path, analysis, accent, cfg):
    W, H = 1280, 720
    base = _frame_base(video_path, W, H, blur=4)
    # smooth bottom-up darkening
    col = Image.new("L", (1, H))
    for y in range(H):
        t = y / float(H)
        col.putpixel((0, y), int(255 * min(1.0, max(0.0, (t - 0.25) / 0.75)) ** 1.3))
    base = Image.composite(Image.new("RGB", (W, H), (10, 10, 14)), base, col.resize((W, H)))
    draw = ImageDraw.Draw(base)
    brand = analysis["title"].upper()
    tf = _fit_cover_font(brand, W - 240, 128, "black", 56)
    tw = _text_w(tf, brand)
    bx = (W - tw) // 2
    by = 330
    title_h = tf.getbbox(brand)[3] - tf.getbbox(brand)[1]
    # thin accent line above
    draw.rectangle([(W - tw) // 2, by - 24, (W + tw) // 2, by - 14], fill=accent)
    draw.text((bx, by), brand, font=tf, fill=(255, 255, 255), stroke_width=2, stroke_fill=(0, 0, 0))
    sub = analysis["year"]
    sf = _cover_font(50, "bold")
    sw = _text_w(sf, sub)
    draw.text(((W - sw) // 2, by + title_h + 30), sub,
              font=sf, fill=accent, stroke_width=1, stroke_fill=(0, 0, 0))
    _cover_bottom(draw, W, H, cfg, accent, 60)
    return base


def _cover_spotlight(video_path, analysis, accent, cfg):
    W, H = 1280, 720
    base = _vignette(_frame_base(video_path, W, H, blur=2))
    draw = ImageDraw.Draw(base)
    brand = analysis["title"].upper()
    tf = _fit_cover_font(brand, W - 200, 168, "black", 60)
    tw = _text_w(tf, brand)
    ty = 96
    draw.text(((W - tw) // 2 + 3, ty + 4), brand, font=tf, fill=(0, 0, 0))
    draw.text(((W - tw) // 2, ty), brand, font=tf, fill=(255, 255, 255), stroke_width=4, stroke_fill=(0, 0, 0))
    title_h = tf.getbbox(brand)[3] - tf.getbbox(brand)[1]
    sub = analysis["year"]
    sf = _cover_font(64, "black")
    sw = _text_w(sf, sub)
    draw.rectangle([(W - sw) // 2 - 20, ty + title_h + 34, (W + sw) // 2 + 20, ty + title_h + 34 + 78], fill=accent)
    draw.text(((W - sw) // 2, ty + title_h + 42), sub, font=sf, fill=(255, 255, 255),
              stroke_width=2, stroke_fill=(0, 0, 0))
    _cover_bottom(draw, W, H, cfg, accent, 60)
    return base


_COVER_BUILDERS = {
    "bold": _cover_bold, "gaming": _cover_gaming,
    "minimal": _cover_minimal, "spotlight": _cover_spotlight,
}


def _cover_stamp(title, channel, style):
    contents = json.dumps([title, channel, style], ensure_ascii=False).encode()
    return "YouTubeUploader/" + hashlib.sha256(contents).hexdigest()


def generate_cover(video_path, config, analysis=None, out_dir=None, log=log_message, style=None):
    """Compose a professional 1280x720 YouTube cover. style: bold|gaming|minimal|spotlight."""
    video_path = Path(video_path)
    cfg = config or {}
    if analysis is None:
        _, _, _, analysis = generate_metadata_ex(video_path, cfg, log)
    accent = _cover_accent(analysis)
    style = (style or cfg.get("cover_style", "bold")).lower()
    builder = _COVER_BUILDERS.get(style, _cover_bold)
    try:
        base = builder(video_path, analysis, accent, cfg)
    except Exception as e:
        log(f"Cover style '{style}' failed ({e}); using 'bold'.", "WARNING")
        base = _cover_bold(video_path, analysis, accent, cfg)
    if out_dir is None:
        out_dir = APP_DIR / cfg.get("cover_dir", "covers")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / (video_path.stem + ".jpg")
    exif = Image.Exif()
    exif[305] = _cover_stamp(analysis["title"], cfg.get("telegram_channel"), style)
    base.convert("RGB").save(out, "JPEG", quality=90, exif=exif)
    log(f"\U0001f3a8 Cover generated [{style}]: {out.name}")
    return str(out)


def _read_companion(video, log):
    path = video.with_suffix(".json")
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            title = data.get("title", "")
            description = data.get("description", "")
            tags = data.get("tags", [])
            if isinstance(tags, str):
                tags = [t.strip() for t in tags.split(",") if t.strip()]
            # Earlier Telegram downloads wrote the original filename into a title-only JSON.
            if video.stem.startswith("tg_") and not description and "tags" not in data:
                title = ""
            if title or description:
                log(f"Using metadata from {path.name}")
                return title, description, tags
        except (OSError, ValueError, AttributeError) as error:
            log(f"Could not read {path.name}: {error}", "WARNING")
    path = video.with_suffix(".txt")
    if path.is_file():
        lines = path.read_text(encoding="utf-8-sig").splitlines()
        if lines:
            return lines[0], "\n".join(lines[1:]).strip(), None
    return None


def audience_description(description, channel, owner):
    invites = []
    known = set()
    for label, url in (("برای ویدیوهای بیشتر به کانال تلگرام ما بپیوندید", channel),
                       ("برای ارتباط با ما در تلگرام", owner)):
        if url and url.casefold() not in known:
            invites.append((label, url))
            known.add(url.casefold())
    # Reserve enough UTF-8 bytes for both URLs before checking what survived truncation.
    footer_size = sum(len(f"\n\n{label}\n{url}".encode()) for label, url in invites)
    body = description.encode()[:5000 - footer_size].decode(errors="ignore").rstrip()
    for label, url in invites:
        handle = re.escape(url.rsplit("/", 1)[-1])
        if not re.search(rf"(?<![\w/])(?:https?://t\.me/|@){handle}(?![\w/])", body, re.I):
            body += f"\n\n{label}\n{url}"
    return body


def generate_metadata_ex(video_path, config, log=log_message, *, ignore_companion=False):
    video = Path(video_path)
    record = None if ignore_companion else _read_companion(video, log)
    title, description, tags = record or ("", "", None)
    fields = analyze_video(video, config, log, supplied_title=title, supplied_description=description)
    if not fields["title"]:
        raise uploader.MetadataRequired(
            f"{video.name}: no descriptive caption, subtitle, embedded metadata or readable topic in the frames. "
            "Kept in the queue before upload."
        )
    title, description = fields["title"], fields["description"]
    if not record:
        title = render_template(config.get("title_template") or "{title}", fields, title, log)
        description = render_template(config.get("description_template") or "{description}", fields, description, log)
    if tags is None:
        tags = [tag.strip() for tag in config.get("default_tags", "").split(",") if tag.strip()]
    title = title.replace("<", "").replace(">", "").strip()
    if uploader.opaque_title(title):
        title = fields["title"]
    title = " ".join(title.split())[:100].rstrip()
    description = description.replace("<", "").replace(">", "").strip()
    if not video_content.substantive(description):
        description = fields["description"]
    description = audience_description(description, fields["telegram_channel"], fields["telegram_owner"])
    return title, description, tags, dict(fields, title=title)


def generate_metadata(video_path: Path, config: dict, log=log_message):
    """Read the sidecar override or render metadata using the saved templates."""
    title, description, tags, _ = generate_metadata_ex(video_path, config, log)
    return title, description, tags


def template_metadata(video, config, log):
    return generate_metadata_ex(video, config, log, ignore_companion=True)


def upload_thumbnail(service, config, video, video_id, analysis, log=log_message):
    if not config.get("set_thumbnail"):
        return
    image = find_companion_image(video)
    if image is None:
        cover = Path(config["cover_dir"]) / (video.stem + ".jpg")
        if cover.is_file():
            image = cover
            if config.get("auto_cover"):
                with Image.open(cover) as previous:
                    stamp = str(previous.getexif().get(305, ""))
                expected = _cover_stamp(analysis["title"], config.get("telegram_channel"), config.get("cover_style", "bold").lower())
                if stamp.startswith("YouTubeUploader/") and stamp != expected:
                    image = None
        if image is None and config.get("auto_cover"):
            image = generate_cover(video, config, analysis, log=log)
    if image:
        mime = mimetypes.guess_type(str(image))[0] or "application/octet-stream"
        with Path(image).open("rb") as stream:
            media = MediaIoBaseUpload(stream, mimetype=mime)
            service.thumbnails().set(videoId=video_id, media_body=media).execute(
                num_retries=int(config["max_retries"])
            )


class UploaderEngine:
    """
    Reusable, thread-friendly YouTube upload engine.

    Callbacks (all optional):
        log_callback(msg: str, level: str)
        progress_callback(done: int, total: int, current_name: str)
        status_callback(state: str, info: dict)      # e.g. "login", "uploading", "waiting_login"
    """

    def __init__(self, config: dict, *,
                 log_callback=None, progress_callback=None, status_callback=None,
                 interactive_console: bool = False):
        self.config = config
        self._log_cb = log_callback
        self._progress_cb = progress_callback
        self._status_cb = status_callback
        self.interactive_console = interactive_console

        self.service = None
        self.stop_event = threading.Event()
        self._pause_event = threading.Event()   # set == paused
        self.is_running = False

    # -- callbacks ----------------------------------------------------------
    def log(self, msg: str, level: str = "INFO"):
        if self._log_cb:
            try:
                self._log_cb(msg, level)
            except Exception:
                pass
        else:
            log_message(msg, level)

    def _status(self, state: str, **info):
        if self._status_cb:
            try:
                self._status_cb(state, info)
            except Exception:
                pass

    def _progress(self, done: int, total: int, current_name: str = ""):
        if self._progress_cb:
            try:
                self._progress_cb(done, total, current_name)
            except Exception:
                pass

    # -- control ------------------------------------------------------------
    def request_stop(self):
        self.stop_event.set()
        self._pause_event.clear()

    def pause(self):
        self._pause_event.set()
        self.log("⏸ Paused. Will resume after the current step.", "WARNING")

    def resume(self):
        self._pause_event.clear()
        self.log("▶ Resumed.", "INFO")

    @property
    def is_paused(self) -> bool:
        return self._pause_event.is_set()

    def _check_stop(self):
        if self.stop_event.is_set():
            raise StopRequested()

    def _interruptible_sleep(self, seconds: float):
        """Sleep in small slices, honoring pause and stop."""
        end = time.time() + max(0.0, seconds)
        while True:
            self._check_stop()
            # honor pause without counting it against the sleep budget
            while self._pause_event.is_set():
                self._check_stop()
                time.sleep(0.15)
                end = max(end, time.time())
            if time.time() >= end:
                return
            time.sleep(min(0.15, end - time.time()))

    def close(self):
        if self.service is not None:
            self.service.close()
            self.service = None

    def _channel_service(self, *, authorize=False, choose_client=None, explicit_client=None):
        try:
            return uploader.client(uploader.DEFAULT_TOKEN)
        except uploader.ConnectionRequired:
            if not authorize:
                raise

        if explicit_client is not None:
            client_file = Path(explicit_client)
        else:
            try:
                client_file = desktop_oauth.find_client(APP_DIR, self.config["oauth_client_file"])
            except FileNotFoundError as error:
                if choose_client is None:
                    raise
                self.log(str(error), "INFO")
                chosen = choose_client()
                if not chosen:
                    raise uploader.ConnectionRequired("Channel setup cancelled. " + desktop_oauth.SETUP_HELP) from None
                client_file = Path(chosen)
        self._status("connecting")
        uploader.connect(client_file, uploader.DEFAULT_TOKEN)
        return uploader.client(uploader.DEFAULT_TOKEN)

    def setup_login(self, *, choose_client=None, explicit_client=None):
        with self._channel_service(authorize=True, choose_client=choose_client, explicit_client=explicit_client):
            self.log("YouTube channel connected. Uploads use the saved permission.", "SUCCESS")
        self._status("logged_in")

    def upload_one(self, video, skip_uploaded):
        title, desc, tags, analysis = generate_metadata_ex(video, self.config, self.log)
        uploader.check_public_metadata(video, title, desc, self.config["visibility"])
        snippet = {"title": title, "description": desc, "categoryId": "22"}
        if tags:
            snippet["tags"] = tags
        language = self.config.get("title_language", "auto")
        if language != "auto":
            snippet["defaultLanguage"] = language
        status = {"privacyStatus": self.config["visibility"].lower(),
                  "selfDeclaredMadeForKids": bool(self.config["made_for_kids"])}

        # Claim the original filename, which is also the key in old upload logs.
        # Keep the claim on an ambiguous network result or process interruption.
        claims = Path(LOG_FILE).parent / ".uploading"
        claims.mkdir(exist_ok=True)
        claim = claims / hashlib.sha256(video.name.encode("utf-8")).hexdigest()
        try:
            with claim.open("x", encoding="utf-8") as record:
                record.write(video.name)
        except FileExistsError:
            self.log(f"{video.name} already has an upload in progress or awaiting verification. "
                     f"Check YouTube Studio and {claim}.", "WARNING")
            return "skipped"
        if skip_uploaded and video.name in load_uploaded_log():
            claim.unlink()
            return "skipped"
        try:
            video_id = uploader.upload(
                self.service, video, snippet=snippet, status=status,
                retries=max(0, int(self.config["max_retries"])),
                log=self.log, before_chunk=lambda: self._interruptible_sleep(0),
            )
        except (StopRequested, uploader.MetadataRequired):
            claim.unlink()
            raise
        except HttpError as error:
            if 400 <= error.resp.status < 500 and error.resp.status != 408:
                claim.unlink()
            self.log(f"YouTube upload failed for {video.name} (HTTP {error.resp.status}): {error.reason}", "ERROR")
            return "failed"
        except FileNotFoundError:
            claim.unlink()
            raise
        mark_as_uploaded(video.name)
        claim.unlink()
        self.log(f"Uploaded {video.name}: https://youtu.be/{video_id}", "SUCCESS")
        try:
            upload_thumbnail(self.service, self.config, video, video_id, analysis, self.log)
        except Exception as error:
            self.log(f"Video {video_id} is uploaded. Thumbnail failed ({type(error).__name__}).", "WARNING")
        return "uploaded"

    def run(self, videos=None, skip_uploaded=True, limit=None, *, choose_client=None):
        self.stop_event.clear()
        self._pause_event.clear()
        self.is_running = True
        summary = {"total": 0, "uploaded": 0, "failed": 0, "skipped": 0, "held": 0}
        try:
            videos = list_videos(self.config["video_folder"]) if videos is None else list(map(Path, videos))
            done = load_uploaded_log() if skip_uploaded else set()
            pending = [video for video in videos if video.name not in done]
            summary["skipped"] = len(videos) - len(pending)
            total = len(pending) if limit is None else min(len(pending), max(0, limit))
            summary["total"] = total
            if total:
                self.service = self._channel_service(authorize=choose_client is not None, choose_client=choose_client)
            for index, video in enumerate(pending, 1):
                if index > total:
                    break
                self._interruptible_sleep(0)
                self._status("uploading", name=video.name, index=index, total=total)
                try:
                    outcome = self.upload_one(video, skip_uploaded)
                except StopRequested:
                    raise
                except uploader.MetadataRequired as error:
                    self.log(str(error), "WARNING")
                    outcome = "held"
                    total = min(len(pending), total + 1)
                    summary["total"] = total
                except Exception as error:
                    self.log(f"{video.name}: {type(error).__name__}. Check the upload log "
                             "and YouTube Studio before retrying.", "ERROR")
                    outcome = "failed"
                summary[outcome] += 1
                self._progress(index, total, video.name)
                if outcome == "held":
                    self._status("held", name=video.name)
                if outcome == "uploaded" and index < total:
                    self._interruptible_sleep(int(self.config["upload_delay_seconds"]))
            self._status("finished", summary=summary)
            self.log(f"Uploaded {summary['uploaded']}, held {summary['held']}, "
                     f"skipped {summary['skipped']}, failed {summary['failed']}.")
        except StopRequested:
            self._status("stopped", summary=summary)
        finally:
            self.close()
            self.is_running = False
        return summary

    def dry_run(self, videos=None):
        """Generate metadata for each video WITHOUT opening a browser. Returns list of dicts."""
        folder = self.config.get("video_folder", DEFAULT_VIDEO_DIR)
        if videos is None:
            videos = list_videos(folder)
        results = []
        total = len(videos)
        for idx, video in enumerate(videos, 1):
            if self.stop_event.is_set():
                break
            video = Path(video)
            self.log(f"[Dry-run] Generating metadata {idx}/{total}: {video.name}")
            self._status("uploading", index=idx, total=total, name=video.name)
            try:
                title, description, tags = generate_metadata(video, self.config, self.log)
                results.append({"file": video.name, "title": title, "description": description, "tags": tags})
            except uploader.MetadataRequired as error:
                self.log(str(error), "WARNING")
                results.append({"file": video.name, "held": str(error)})
            self._progress(idx, total, video.name)
        self.log("🏁 Dry-run complete.", "SUCCESS")
        self._status("finished", summary={"total": total})
        return results



TASK_NAME = "sliverYTUploader"


def _pythonw_exe() -> str:
    """Prefer pythonw.exe (no console flash) when available."""
    exe = sys.executable or "python"
    cand = os.path.join(os.path.dirname(exe), "pythonw.exe")
    return cand if os.path.isfile(cand) else exe


def _script_dir() -> str:
    return os.path.dirname(os.path.abspath(__file__))


def scheduled_task_command() -> str:
    """The command Windows Task Scheduler will run each day (opens the GUI in auto-run)."""
    script = os.path.join(_script_dir(), "yt_uploader.py")
    return f'"{_pythonw_exe()}" "{script}" --gui --autorun'


def create_daily_task(time_str: str = "10:00", log=log_message) -> bool:
    """Register a Windows scheduled task that opens the app daily and auto-uploads."""
    if sys.platform != "win32":
        log("Daily task scheduling is only supported on Windows.", "WARNING")
        return False
    try:
        cmd = [
            "schtasks", "/Create", "/SC", "DAILY", "/TN", TASK_NAME,
            "/TR", scheduled_task_command(), "/ST", time_str, "/F",
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode == 0:
            log(f"✅ Daily auto-upload task created for {time_str}.", "SUCCESS")
            return True
        log(f"Could not create task: {res.stderr.strip() or res.stdout.strip()}", "ERROR")
        return False
    except Exception as e:
        log(f"schtasks failed: {e}", "ERROR")
        return False


def delete_daily_task(log=log_message) -> bool:
    if sys.platform != "win32":
        return False
    try:
        res = subprocess.run(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"],
                             capture_output=True, text=True)
        if res.returncode == 0:
            log("Daily auto-upload task removed.", "SUCCESS")
            return True
        log(f"Could not remove task: {res.stderr.strip() or res.stdout.strip()}", "WARNING")
        return False
    except Exception as e:
        log(f"schtasks failed: {e}", "ERROR")
        return False


def daily_task_exists() -> bool:
    if sys.platform != "win32":
        return False
    try:
        res = subprocess.run(["schtasks", "/Query", "/TN", TASK_NAME],
                             capture_output=True, text=True)
        return res.returncode == 0
    except Exception:
        return False


def run_gui(autorun: bool = False):
    """Launch the graphical interface (lazy import to avoid GUI deps in headless use)."""
    try:
        import yt_uploader_gui
    except Exception as e:
        log_message(f"Could not import the GUI module: {e}", "ERROR")
        log_message("Install requirements with:  pip install -r requirements.txt", "INFO")
        sys.exit(1)
    yt_uploader_gui.main(autorun=autorun)


def main(argv=None):
    parser = argparse.ArgumentParser(description="YouTube desktop uploader")
    parser.add_argument("--gui", action="store_true", help="Open the desktop interface")
    parser.add_argument("--autorun", action="store_true", help="Start a daily batch in the GUI")
    parser.add_argument("-f", "--folder", help="Video folder for this run")
    parser.add_argument("--setup", action="store_true", help="Connect your YouTube channel once")
    parser.add_argument("--client-json", help="Google Desktop OAuth client JSON for setup")
    parser.add_argument("--dry-run", action="store_true", help="Preview titles, tags and descriptions")
    parser.add_argument("--auto", action="store_true", help="Upload the next daily batch and exit")
    parser.add_argument("--count", type=int, help="Maximum videos per batch")
    parser.add_argument("--visibility", choices=VISIBILITY_CHOICES)
    parser.add_argument("--no-skip", action="store_true", help="Include previously uploaded filenames")
    parser.add_argument("--schedule", metavar="HH:MM", help="Register the Windows daily task")
    parser.add_argument("--unschedule", action="store_true", help="Remove the Windows daily task")
    parser.add_argument("--tg-login", action="store_true", help="Connect Telegram once")
    parser.add_argument("--tg-download", action="store_true", help="Download new channel videos")
    parser.add_argument("--tg-channel", help="Telegram channel name, link or ID")
    parser.add_argument("--tg-limit", type=int, help="Newest videos to download (0 = all new)")
    parser.add_argument("--tg-upload", action="store_true", help="Upload the downloaded videos")
    args = parser.parse_args(argv)
    if args.tg_upload and not args.tg_download:
        parser.error("--tg-upload requires --tg-download")
    if args.gui or args.autorun:
        run_gui(autorun=args.autorun)
        return 0
    if args.schedule:
        return 0 if create_daily_task(args.schedule) else 1
    if args.unschedule:
        return 0 if delete_daily_task() else 1

    cfg = load_config()
    if args.folder:
        cfg["video_folder"] = str(Path(args.folder).resolve())
    if args.visibility:
        cfg["visibility"] = args.visibility
    engine = UploaderEngine(cfg)
    if args.setup:
        picker = desktop_oauth.pick_client_file if sys.platform == "win32" else None
        engine.setup_login(choose_client=picker, explicit_client=args.client_json)
        return 0
    if args.tg_login:
        import getpass
        import telegram_downloader as tg
        connected = tg.login_from_config(cfg, code_cb=lambda: input("Telegram code: "),
                                         password_cb=lambda: getpass.getpass("Two-step password: "))
        return 0 if connected else 1
    Path(cfg["video_folder"]).mkdir(parents=True, exist_ok=True)
    if args.dry_run:
        for result in engine.dry_run():
            print(json.dumps(result, ensure_ascii=False))
        return 0

    videos = None
    download_errors = []
    if args.tg_download:
        import telegram_downloader as tg

        def report(message, level="INFO"):
            log_message(message, level)
            if level == "ERROR":
                download_errors.append(message)

        videos = tg.download_from_config(cfg, channel=args.tg_channel, limit=args.tg_limit, log=report)
        if not args.tg_upload or not videos:
            return int(bool(download_errors))
    limit = args.count
    if args.auto and limit is None:
        limit = int(cfg["videos_per_day"])
    summary = engine.run(videos=videos, skip_uploaded=not args.no_skip, limit=limit)
    return int(bool(summary["failed"] or download_errors))


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (uploader.ConnectionRequired, OSError, ValueError) as error:
        log_message(str(error), "ERROR")
        sys.exit(1)
