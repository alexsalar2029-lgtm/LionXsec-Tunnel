"""Read local video frames, published subtitles and descriptive container tags."""
import hashlib
import html
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import av
import tesserocr
from PIL import Image, ImageOps, ImageStat


MODEL_DIR = Path(__file__).resolve().parent / "tessdata"
SUBTITLE_EXTENSIONS = (".srt", ".vtt")
_RECORDER = re.compile(
    r"(?:screen[ -]*(?:record(?:ing|er)?|capture|shot)|record(?:ing)?|rec|video|clip|capture|"
    r"vid|img|dsc|movie|untitled|bandicam|zoom|ضبط(?:\s*صفحه)?|ویدیو)"
    r"(?:\s*(?:at|on|از|در))?(?:[\s\d.:()\[\]+-]|am|pm|mp4)*", re.I)
_FORMATS = {"mp4", "mkv", "avi", "webm", "mov", "wmv", "m4v", "flv", "mpeg"}
_PRIVATE_LINE = re.compile(
    r"(?:https?://|www\.|[\w.+-]+@[\w.-]+\.\w+|(?:^|\s)@[\w_]+|\b\d{7,}\b|"
    r"\b[a-z\d_-]{24,}\b|(?:password|passcode|token|secret|api[ _-]?(?:key|hash)|"
    r"verification code|رمز عبور|کد ورود)\s*[:=]|[?&]\w+=)", re.I)
_UI = {
    "file", "edit", "view", "help", "home", "tools", "settings", "search", "save", "share",
    "new tab", "new chat", "search chats", "chatgpt", "ask anything", "type a message",
    "where should we begin", "what can i help with", "how can i help you today",
    "google chrome", "sign in", "log in", "download history", "skip to content",
    "enter a prompt here", "send a message", "tell me more", "how can i help",
    "شروع گفتگو", "گفتگوی جدید", "هر چیزی بپرسید", "تنظیمات",
}
_CHAT_PROMPTS = {"where should we begin", "what can i help with", "ask anything",
                 "how can i help you today", "send a message"}


def key(text):
    return " ".join(re.findall(r"[^\W_]+", text.casefold()))


def opaque_title(value):
    text = str(value or "").strip()
    text = re.sub(r"\.(?:mp4|mkv|avi|mov|webm|m4v|wmv)$", "", text, flags=re.I)
    norm = text.replace("_", " ").strip()
    return (not norm or not any(ch.isalpha() for ch in norm)
            or norm.lower() in _FORMATS or bool(_RECORDER.fullmatch(norm))
            or bool(re.fullmatch(r"(?:rec[ -]*)?[a-f\d]{16,}|tg[ -].+[ -]\d+|"
                                 r"(?:ویدیو|video)\s*\|\s*[\d\s:-]+", norm, re.I)))


def substantive(text):
    """Discard the recorder placeholders left in old captions and metadata files."""
    return [line.strip() for line in str(text or "").splitlines()
            if line.strip() and not opaque_title(line)
            and not re.match(r"^(?:مشخصات ویدیو|video (?:details|format)|تلگرام|telegram)\s*[:：]", line.strip(), re.I)
            and not re.match(r"^(?:برای ویدیوهای بیشتر به کانال تلگرام|برای ارتباط با ما در تلگرام|"
                             r"(?:join|follow) (?:our |my )?telegram\b)", line.strip(), re.I)
            and any(char.isalpha() for char in re.sub(r"(?:https?://\S+|@\w+)", "", line))]


def metadata(video):
    try:
        with av.open(str(video), options={"protocol_whitelist": "file"}) as media:
            stream = media.streams.video[0]
            tags = {k.lower(): v.strip() for src in (stream.metadata, media.metadata)
                    for k, v in src.items() if isinstance(v, str)}
            tags["resolution"] = f"{stream.width}×{stream.height}"
            if media.duration:
                minutes, seconds = divmod(round(media.duration / av.time_base), 60)
                tags["duration"] = f"{minutes}:{seconds:02d}"
            return tags
    except (OSError, ValueError, IndexError, av.FFmpegError):
        return {}


def sample_frames(video, positions):
    """Seek in video-stream ticks and decode up to the requested presentation time."""
    with av.open(str(video), options={"protocol_whitelist": "file"}) as media:
        stream = media.streams.video[0]
        start = stream.start_time or 0
        duration = stream.duration
        if not duration and media.duration and stream.time_base:
            duration = int(media.duration / av.time_base / stream.time_base)
        if not duration:
            interval = max(1, round(float(stream.average_rate or 24) * 2))
            for i, frame in enumerate(media.decode(stream)):
                if i % interval == 0:
                    yield frame.to_image()
                    if i // interval + 1 >= len(positions):
                        break
            return
        for portion in positions:
            target = start + int(portion * duration)
            media.seek(target, stream=stream, backward=True)
            frame = None
            for frame in media.decode(stream):
                if frame.pts is None or frame.pts >= target:
                    break
            if frame is not None:
                yield frame.to_image()


def subtitle_topics(video):
    for suffix in SUBTITLE_EXTENSIONS:
        path = video.with_suffix(suffix)
        if not path.is_file():
            continue
        with path.open("rb") as source:
            raw = source.read(256 * 1024)
        encoding = "utf-16" if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
        topics = []
        for cue in re.split(r"\n\s*\n", raw.decode(encoding, errors="replace").replace("\r", "")):
            lines = cue.splitlines()
            timing = next((i for i, s in enumerate(lines) if "-->" in s), None)
            if timing is None:
                continue
            text = html.unescape(re.sub(r"<[^>]*>", "", " ".join(lines[timing + 1:]))).strip()
            if evidence_line(text) and text not in topics:
                topics.append(text)
                if len(topics) == 4:
                    break
        if topics:
            return topics
    return []


def evidence_line(text):
    if not 4 <= len(text) <= 200 or opaque_title(text) or _PRIVATE_LINE.search(text):
        return False
    words = re.findall(r"[^\W\d_]{2,}", text)
    return sum(map(len, words)) >= 6 and key(text) not in _UI


@dataclass(frozen=True)
class ScreenLine:
    text: str
    confidence: float
    height: float
    central: bool
    center_y: float


def _ocr_at_width(api, gray, width):
    if width != gray.width:
        gray = gray.resize((width, max(1, round(gray.height * width / gray.width))), Image.Resampling.BICUBIC)
    api.SetImage(gray)
    api.SetSourceResolution(150)
    # The TSV renderer orders words in reading order for both Latin and RTL lines.
    groups = {}
    for row in api.GetTSVText(0).splitlines():
        fields = row.split("\t", 11)
        if len(fields) != 12 or fields[0] != "5" or not fields[11].strip():
            continue
        x, y, w, h = map(int, fields[6:10])
        groups.setdefault(tuple(fields[2:5]), []).append((fields[11], float(fields[10]), x + w, y, y + h))
    lines = []
    for words in groups.values():
        text = " ".join(word[0] for word in words).translate({0x200E: None, 0x200F: None}).strip()
        confidence = sum(word[1] for word in words) / len(words)
        if confidence < 70:
            continue
        right = max(word[2] for word in words)
        top = min(word[3] for word in words)
        bottom = max(word[4] for word in words)
        # Ignore the capture's browser tabs, address bar, taskbar and side navigation.
        center_y = (top + bottom) / (2 * gray.height)
        central = 0.11 < center_y < 0.91 and right > gray.width * 0.22
        lines.append(ScreenLine(text, confidence, (bottom - top) / gray.height, central, center_y))
    return lines


def _screen_lines(api, image):
    gray = image.convert("L")
    if ImageStat.Stat(gray.resize((96, 54))).median[0] < 100:
        gray = ImageOps.invert(gray)
    width = min(2200, max(gray.width, min(gray.width * 2, 1400)))
    lines = _ocr_at_width(api, gray, width)
    if sum(line.central and evidence_line(line.text) for line in lines) <= 1:
        # Large display type can exceed the recognizer's line size at the first scale.
        small = _ocr_at_width(api, gray, min(800, max(320, gray.width // 2)))
        lines.extend(line for line in small
                     if all(abs(line.center_y - other.center_y) > 0.025 for other in lines))
    return lines


def screen_topics(video, language, log):
    observations = {}
    appearances = Counter()
    chat_frames = 0
    cached = {}
    langs = {"fa": "fas+eng", "en": "eng"}.get(language, "eng+fas")
    try:
        with tesserocr.PyTessBaseAPI(path=str(MODEL_DIR), lang=langs, psm=tesserocr.PSM.SPARSE_TEXT) as api:
            for frame in sample_frames(video, (0.03, 0.16, 0.32, 0.5, 0.67, 0.82, 0.95)):
                digest = hashlib.blake2b(frame.tobytes(), digest_size=16).digest()
                if digest not in cached:
                    cached[digest] = _screen_lines(api, frame)
                lines = cached[digest]
                labels = {key(line.text) for line in lines}
                prompts = {key(line.text) for line in lines if line.central} & _CHAT_PROMPTS
                if prompts and any("chatgpt" in label.split() for label in labels):
                    chat_frames += 1
                seen = set()
                for line in lines:
                    if not line.central or not evidence_line(line.text):
                        continue
                    label = key(line.text)
                    if len(label.split()) < 2 and (line.height < 0.052 or line.confidence < 90):
                        continue
                    seen.add(label)
                    if label not in observations or observations[label].confidence < line.confidence:
                        observations[label] = line
                appearances.update(seen)
    except (OSError, ValueError, IndexError, RuntimeError, av.FFmpegError) as error:
        log(f"Frame analysis unavailable for {video.name} ({type(error).__name__}).", "WARNING")

    def score(label):
        line = observations[label]
        return min(line.height, 0.12) * 600 + min(appearances[label], 4) * 3 + line.confidence / 10

    ranked = sorted(observations, key=score, reverse=True)
    topics = [observations[k].text for k in ranked
              if appearances[k] >= 2 or (observations[k].height >= 0.035 and observations[k].confidence >= 87)]
    if not topics and chat_frames >= 2:
        return ["محیط گفت‌وگوی ChatGPT"] if language != "en" else ["The ChatGPT conversation screen"]
    return topics[:4]
