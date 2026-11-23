"""Read local video frames, published subtitles and descriptive container tags."""
import hashlib
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
    r"(?:\s*(?:at|on|از|در))?(?:[\s\d.:()\[\]+~-]|am|pm|mp4)*", re.I)
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


class MetadataRequired(ValueError):
    """This file needs more evidence or a review before automatic publication."""


def check_for_review(text, *, artwork=False):
    words = key(text)
    tool = re.search(r"\b(?:silver|sliver|open)\s*bullet\b", words)
    indicators = re.search(r"\b(?:hits|cpm|combo|free|رایگان)\b", words)
    if tool and (indicators or (artwork and not re.search(r"\b(?:defense|defence|prevention|mitigation|دفاع|پیشگیری)\b", words))):
        raise MetadataRequired("Local review required for material promoting bulk account checks.")


def subject_lines(text):
    """Keep descriptive phrases, discarding recorder labels, greetings and promotional footers."""
    phrases = []
    for line in re.split(r"[\n!?؟]+|(?<=\w)\.(?:\s+|$)", str(text or "")):
        if not substantive(line) or not re.sub(r"(?:#\w+|https?://\S+|@\w+)", "", line).strip(" |,،:-"):
            continue
        line = re.sub(r"https?://\S+|(?<!\w)@\w+|(?<!\w)#[\w\u200c]+", "", line).strip(" |,،:-")
        label = key(line)
        if (evidence_line(line) and len(label.split()) >= 2
                and label not in {"thank you", "thanks for watching", "subscribe to the channel",
                                  "welcome back", "hello everyone", "سلام دوستان", "ممنون از همراهی شما"}
                and not re.match(r"^(?:join (?:our|my) |follow (?:us|me) |عضو کانال|لینک دانلود)", label)):
            line = line[0].upper() + line[1:]
            if line not in phrases:
                phrases.append(line)
    return phrases


_STOPWORDS = set("""a an the this that these those we i my our his your how to in of for with and
or on from by at as into is are was were be will can it its you they about video screen recording
clip episode lesson tutorial today more other showing shows learn what when who then out
در از با و به را این آن برای که یک یا تا هم روی ما شما من خود است های ها می شود
ویدیو فیلم آموزش درباره بخش قسمت بیشتر امروز مشاهده""".split())


def topic_tags(title, context, existing=()):
    """Use a short set of actual topic phrases for both YouTube tags and visible hashtags."""
    tags = []
    seen = set()
    for value in existing:
        tag = str(value).strip().strip("#").strip()
        if tag and key(tag) and key(tag) not in seen:
            tags.append(tag[:60])
            seen.add(key(tag))
    phrases = [title, *context.splitlines()]
    singles = []
    for phrase in phrases:
        words = re.findall(r"[^\W_]+(?:\u200c[^\W_]+)*", phrase)
        keywords = [word for word in words if len(word) > 2 and word.casefold() not in _STOPWORDS
                    and any(c.isalpha() for c in word)]
        if not keywords:
            continue
        # Preserve phrasing for short subjects. Long sentences contribute individual terms.
        if 1 < len(keywords) <= 4:
            tag = " ".join(words[words.index(keywords[0]):len(words) - words[::-1].index(keywords[-1])])
            if key(tag) not in seen:
                tags.append(tag[:60])
                seen.add(key(tag))
        singles.extend(keywords)
    for word in singles:
        if key(word) not in seen:
            tags.append(word)
            seen.add(key(word))
    return tags[:5]


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
            and any(char.isalpha() for char in re.sub(r"(?:https?://\S+|@\w+|#[\w\u200c]+)", "", line))]


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


def cover_topics(path, language):
    """Read only matched original artwork. A generated title cannot corroborate itself."""
    if path is None:
        return []
    try:
        with Image.open(path) as image:
            if str(image.getexif().get(305, "")).startswith("YouTubeUploader/") or min(image.size) < 80:
                return []
            langs = {"en": "eng", "fa": "fas+eng"}.get(language, "eng+fas")
            with tesserocr.PyTessBaseAPI(path=str(MODEL_DIR), lang=langs, psm=tesserocr.PSM.SPARSE_TEXT) as api:
                lines = _screen_lines(api, image.convert("RGB"))
                api.SetImage(image.convert("RGB"))
                full_text = api.GetUTF8Text()
        check_for_review(full_text + "\n" + "\n".join(line.text for line in lines), artwork=True)
        result = []
        for line in sorted(lines, key=lambda s: s.height, reverse=True):
            if (line.height >= 0.026 and line.confidence >= 82
                    and not re.search(r"\b(?:config\s*\d*|hits|cpm|free|com)\b", line.text, re.I)):
                result.extend(t for t in subject_lines(line.text) if t not in result)
        return result[:3]
    except MetadataRequired:
        raise
    except (OSError, ValueError, RuntimeError):
        return []


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
                check_for_review("\n".join(line.text for line in lines))
                labels = {key(line.text) for line in lines}
                prompts = {key(line.text) for line in lines if line.central} & _CHAT_PROMPTS
                if prompts and any("chatgpt" in label.split() for label in labels):
                    chat_frames += 1
                seen = set()
                for line in lines:
                    if not line.central or not evidence_line(line.text):
                        continue
                    label = key(line.text)
                    if (len([w for w in label.split() if any(c.isalpha() for c in w)]) < 2
                            or re.fullmatch(r"(?:(?:\d+|config|free|hits|cpm|job|update|premium)\s*)+", label)):
                        continue
                    seen.add(label)
                    if label not in observations or observations[label].confidence < line.confidence:
                        observations[label] = line
                appearances.update(seen)
    except MetadataRequired:
        raise
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
