"""Actual FFmpeg frame extraction, transcoding and overlay; synthetic local video only."""
import hashlib
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import imageio_ffmpeg
from content import fingerprint,prepare,video_frames,video_info
from storage import DEFAULTS

with tempfile.TemporaryDirectory() as temp:
    root=Path(temp);source=root/'lesson.mov';out=root/'out';out.mkdir()
    cmd=[imageio_ffmpeg.get_ffmpeg_exe(),'-y','-v','error','-f','lavfi','-i',
         'testsrc2=duration=2:size=640x360:rate=10','-c:v','libx264',str(source)]
    subprocess.run(cmd,check=True,timeout=30)
    before=source.read_bytes();frames=video_frames(source);assert len(frames)==3
    job={'id':1,'path':str(source),'kind':'video','digest':fingerprint(source),'title':'Video lesson','body':'An ordinary sample clip'}
    config={**DEFAULTS,'watermark':True,'tag':'@example_channel','website':'https://example.com'}
    output,kind=prepare(job,config,out)
    assert kind=='video' and output.suffix=='.mp4' and output.stat().st_size>1000
    assert video_info(output)['size']==(640,360)
    assert source.read_bytes()==before
    assert video_frames(output)[1]!=frames[1]
    print('PASS: 3 real video frames; MP4 transcode; branding overlay; original preserved.')
