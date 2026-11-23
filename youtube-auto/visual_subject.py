"""Identify a recurring object in sampled video frames using the bundled SqueezeNet."""
from collections import Counter
from functools import lru_cache
from pathlib import Path
from threading import Lock

import av
import cv2
import numpy as np
from PIL import ImageOps, ImageStat

from video_content import sample_frames


_DIR = Path(__file__).resolve().parent / "visual_model"
_LOCK = Lock()
_FA = {"Cat": "گربه", "Dog": "سگ", "Coffee": "فنجان قهوه", "Cup": "فنجان",
       "Acoustic guitar": "گیتار آکوستیک", "Electric guitar": "گیتار الکتریک",
       "Soccer ball": "توپ فوتبال", "Mountain bike": "دوچرخهٔ کوهستان"}


@lru_cache(maxsize=1)
def _classifier():
    net = cv2.dnn.readNetFromCaffe(str(_DIR / "deploy.prototxt"), str(_DIR / "squeezenet_v1.1.caffemodel"))
    labels = [line.split(" ", 1)[1].split(",")[0].capitalize()
              for line in (_DIR / "labels.txt").read_text(encoding="utf-8").splitlines()]
    # Report the visible animal rather than inferring its breed from a few frames.
    labels[151:269] = ["Dog"] * 118
    labels[281:286] = ["Cat"] * 5
    labels[967] = "Coffee"
    return net, labels


def identify(video, language, log):
    votes = Counter()
    sampled = 0
    try:
        for frame in sample_frames(video, (0.08, 0.28, 0.5, 0.72, 0.92)):
            sampled += 1
            if ImageStat.Stat(frame.convert("L").resize((96, 54))).stddev[0] < 18:
                continue
            square = ImageOps.fit(frame.convert("RGB"), (227, 227))
            bgr = cv2.cvtColor(np.asarray(square), cv2.COLOR_RGB2BGR)
            blob = cv2.dnn.blobFromImage(bgr, mean=(104, 117, 123))
            with _LOCK:
                net, labels = _classifier()
                net.setInput(blob)
                scores = net.forward().ravel()
            groups = Counter()
            for index in np.argsort(scores)[-8:]:
                groups[labels[index]] += float(scores[index])
            subject, confidence = groups.most_common(1)[0]
            if confidence >= 0.72:
                votes[subject] += 1
    except (cv2.error, OSError, ValueError, IndexError, av.FFmpegError) as error:
        log(f"Visual recognition unavailable for {video.name} ({type(error).__name__}).", "WARNING")
        return ""
    if not votes:
        return ""
    subject, count = votes.most_common(1)[0]
    if count < 3 or count / sampled < 0.75 or subject in {"Screen", "Monitor", "Web site", "Desktop computer", "Television"}:
        return ""
    return _FA.get(subject, subject) if language in ("fa", "auto") else subject
