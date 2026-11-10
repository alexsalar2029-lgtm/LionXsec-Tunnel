"""Stream TXT copies while replacing Telegram promotion references."""
from pathlib import Path
import re
import html

REFERENCES=re.compile(r'(?i)(?:https?://)?(?:www\.)?(?:t\.me|telegram\.me|telegram\.dog)/[^\s<>"\]\[()]{1,512}|tg://[^\s<>]{1,512}|(?<![\w.+-])@[A-Za-z0-9_]{1,64}')
PROMOTION=re.compile(r'(?i)عضویت|تبلیغ|کانال|چنل|join|subscribe|channel|follow')


# Match a complete promotional credit, not just its @handle.
CREDIT_PREFIX=r'(?:(?:config(?:uration)?|created|made|coded|shared|posted|uploaded|published|provided|edited|designed)[\s_-]+by|credits?\s*:?|by)[\s_:-]*'
CREDIT=re.compile(r'(?i)\b'+CREDIT_PREFIX+r'(?:'+REFERENCES.pattern.replace('(?i)','').replace(r'(?<![\w.+-])','')+r')')
OLD_STAMP=re.compile(r'(?i)\bPublished:\s*\d{4}-\d{2}-\d{2}\s*[•|]\s*\d{2}:\d{2}\s*\([A-Za-z_+\-/]+\)')
EMPTY_CREDIT=re.compile(r'(?im)^\s*(?:(?:config(?:uration)?|created|made|coded|shared|posted|uploaded|provided|edited|designed)\s+by|credits?)\s*[:|=-]?\s*$')


def decode_entities(text):
    for _ in range(3):
        decoded=html.unescape(text)
        if decoded==text: break
        text=decoded
    return text.translate({ord(c):None for c in '\u200b\u200e\u200f\ufeff'}).replace('\xa0',' ')


def clean_references(text):
    text=decode_entities(text)
    lines=[]
    for line in text.splitlines(keepends=True):
        line=CREDIT.sub('',line)
        line=OLD_STAMP.sub('',line)
        if REFERENCES.search(line) and PROMOTION.search(line):
            lines.append('\n' if line.endswith('\n') else '')
        else: lines.append(REFERENCES.sub('',line))
    return EMPTY_CREDIT.sub('', ''.join(lines))


def clean_stem(path):
    stem=Path(path).stem.replace('_',' ')
    stem=clean_references(stem)
    stem=re.sub(r'@[A-Za-z0-9_]{1,64}','',stem)
    stem=re.sub(r'[<>:"/\\|?*\x00-\x1f]',' ',stem)
    return ' '.join(stem.split()).strip(' ._-|')


def branded_filename(path,settings,identifier):
    stem=clean_stem(path) or 'resource'
    own=identity(settings) or settings.get('brand','LionXSec')
    safe_own=re.sub(r'[^\w@-]','_',own).strip('_')
    return str(identifier)+'-'+stem[:100]+'-'+safe_own[:70]+Path(path).suffix


def identity(settings):
    tag=settings.get('tag','').strip();channel=settings.get('channel','').strip()
    # The public destination ID is authoritative; private channels can use their configured public tag.
    return channel if channel.startswith('@') else tag or channel


def encoding_for(path):
    with path.open('rb') as raw: head=raw.read(4)
    if head.startswith((b'\xff\xfe',b'\xfe\xff')): return 'utf-16'
    # Validate in chunks, retaining decoder state across UTF-8 boundaries.
    import codecs
    decoder=codecs.getincrementaldecoder('utf-8-sig')()
    try:
        with path.open('rb') as raw:
            for chunk in iter(lambda:raw.read(262144),b''): decoder.decode(chunk)
        decoder.decode(b'',final=True)
        return 'utf-8-sig'
    except UnicodeError: return 'cp1256'


def prepare_text(path,settings,destination,identifier):
    path=Path(path);own=identity(settings)
    if not own: raise ValueError('channel_missing')
    output=Path(destination)/branded_filename(path,settings,identifier)
    pending=''
    try:
        with path.open('r',encoding=encoding_for(path),newline='') as source,output.open('w',encoding='utf-8-sig',newline='') as target:
            if settings.get('text_placement')=='header': target.write(own+'\n\n')
            while True:
                block=source.read(65536)
                if not block: break
                pending+=block
                boundary=pending.rfind('\n')
                if boundary>=0:
                    target.write(clean_references(pending[:boundary+1]));pending=pending[boundary+1:]
                # Bound memory even for a very large single line; leave overlap for reference tokens.
                while len(pending)>131072:
                    boundary=65536
                    match=next((m for m in REFERENCES.finditer(pending) if m.start()<boundary<m.end()),None)
                    if match: boundary=match.start()
                    # Keep complete credit/entity tokens around the chunk boundary.
                    for pattern in (CREDIT,OLD_STAMP,re.compile(r'&[^;\s]{0,24};')):
                        for token in pattern.finditer(pending):
                            if token.start()<boundary<token.end(): boundary=token.start();break
                    target.write(clean_references(pending[:boundary]));pending=pending[boundary:]
            target.write(clean_references(pending))
            if settings.get('text_placement')!='header': target.write('\n\n'+own+'\n')
        return output
    except Exception:
        output.unlink(missing_ok=True)
        raise
