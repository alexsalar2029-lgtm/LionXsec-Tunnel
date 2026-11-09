"""Stream TXT copies while replacing Telegram promotion references."""
from pathlib import Path
import re

REFERENCES=re.compile(r'(?i)(?:https?://)?(?:www\.)?(?:t\.me|telegram\.me|telegram\.dog)/[^\s<>"\]\[()]{1,512}|tg://[^\s<>]{1,512}|(?<![\w.+-])@[A-Za-z0-9_]{1,64}')
PROMOTION=re.compile(r'(?i)عضویت|تبلیغ|کانال|چنل|join|subscribe|channel|follow')


def clean_references(text):
    lines=[]
    for line in text.splitlines(keepends=True):
        if REFERENCES.search(line) and PROMOTION.search(line):
            lines.append('\n' if line.endswith('\n') else '')
        else: lines.append(REFERENCES.sub('',line))
    return ''.join(lines)


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
    stem=re.sub(r'@[A-Za-z0-9_]{1,64}','',clean_references(path.stem)).strip(' ._-|') or 'content'
    safe_own=re.sub(r'[^\w@-]','_',own).strip('_')
    output=Path(destination)/(str(identifier)+'-'+stem[:100]+'-'+safe_own[:70]+path.suffix)
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
                    target.write(REFERENCES.sub('',pending[:boundary]));pending=pending[boundary:]
            target.write(clean_references(pending))
            if settings.get('text_placement')!='header': target.write('\n\n'+own+'\n')
        return output
    except Exception:
        output.unlink(missing_ok=True)
        raise
