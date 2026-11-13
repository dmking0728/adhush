"""Finds the on-screen "Ad" badge with OCR on small crops of the camera image.

Every ad-supported streaming app shows a badge such as "Ad 1 of 3 · 0:45"
while an ad plays. Reading just those corners is cheap enough for a
Raspberry Pi Zero, and it does not depend on what the ad itself looks like.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any, Callable

log = logging.getLogger(__name__)


@dataclass
class RegionResult:
    name: str
    text: str = ""
    matched: str | None = None  # the pattern that matched, if any
    skipped: bool = False  # crop was blank, OCR not run


@dataclass
class Detection:
    is_ad: bool
    regions: list[RegionResult] = field(default_factory=list)
    seconds: float = 0.0
    # The badge's own format was read ("1 of 3", "Ad 0:30"), not just a bare
    # "ad", so it is safe to mute on this one check.
    strong: bool = False
    position: tuple[int, int] | None = None  # (ad number, ads in break): "2 of 3"
    remaining: int | None = None  # countdown seconds left in this ad, ":07"
    frame_time: float = 0.0  # time.monotonic() when the frame was grabbed


# OCR reads "1 of 3" as "10f3", "1of3", "iof3"; the countdown as ":07", ".07", " 07".
_DIGIT = {"i": 1, "l": 1, "I": 1}
_POSITION = re.compile(r"([0-9il])\s*[o0]f\s*([0-9il])(?![0-9])\W{0,4}(?:\d?\s*[:.])?\s*(\d{2})?", re.I)
_STRONG = re.compile(
    # "1 of 3" (Max), "Ad 0:30", "1:04 Ad" (Hulu: countdown, then the Ad pill)
    r"[0-9il]\s*[o0]f\s*[0-9il]|\bads?\s*[:\-]?\s*\d{1,2}:\d{2}|\d{1,2}:\d{2}\W{0,3}\s*ads?\b"
    r"|\badvertisement\b",
    re.I,
)


def parse_badge(text: str) -> tuple[tuple[int, int] | None, int | None]:
    """("1 of 3 :07") -> ((1, 3), 7). Either part is None if not readable."""
    m = _POSITION.search(text)
    if not m:
        return None, None
    n, total = (_DIGIT.get(g, None) or int(g) for g in m.group(1, 2))
    position = (n, total) if 0 < n <= total <= 9 else None
    remaining = int(m.group(3)) if m.group(3) and int(m.group(3)) <= 60 else None
    return position, remaining


class TextMatcher:
    def __init__(self, patterns: list[str]):
        self.patterns = [re.compile(p, re.IGNORECASE) for p in patterns]

    @staticmethod
    def normalize(text: str) -> str:
        # OCR often reads the "·" separator or stray marks as punctuation.
        text = re.sub(r"[|•·_~]+", " ", text)
        return re.sub(r"\s+", " ", text).strip()

    def match(self, text: str) -> str | None:
        norm = self.normalize(text)
        for pat in self.patterns:
            if pat.search(norm):
                return pat.pattern
        return None


def crop(frame, box):
    h, w = frame.shape[:2]
    x, y, bw, bh = box
    x0, y0 = max(0, int(x * w)), max(0, int(y * h))
    x1, y1 = min(w, int((x + bw) * w)), min(h, int((y + bh) * h))
    return frame[y0:y1, x0:x1]


def preprocess(img, scale: float):
    """Return (black-text-on-white image, contrast) ready for Tesseract."""
    import cv2

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    contrast = float(gray.std())
    if scale and scale != 1:
        gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    gray = cv2.GaussianBlur(gray, (3, 3), 0)
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    # Badges are usually light text on a dark pill. Tesseract wants dark text
    # on a light page, so flip whenever the image is mostly dark.
    if binary.mean() < 127:
        binary = 255 - binary
    return _flip_pills(binary), contrast


def _flip_pills(binary):
    """Flip solid boxes that have text punched out of them.

    Max shows a dark "Ad" on a yellow pill beside white "3 of 3". After
    thresholding, the pill is a solid black box with white letters inside,
    which Tesseract can't read. Flipping inside the box makes it black-on-white
    like the rest.
    """
    for x, y, w, h in pill_boxes(binary):
        binary[y : y + h, x : x + w] = 255 - binary[y : y + h, x : x + w]
    return binary


def pill_boxes(binary) -> list[tuple[int, int, int, int]]:
    """(x, y, w, h) of solid, wider-than-tall boxes with text punched out."""
    import cv2

    ink = (binary == 0).astype("uint8")
    n, _, stats, _ = cv2.connectedComponentsWithStats(ink, connectivity=8)
    boxes = []
    for i in range(1, n):
        x, y, w, h, area = (int(v) for v in stats[i])
        if w < 12 or h < 12 or w < 1.2 * h:
            continue  # letters ("o", "0") are about as wide as tall; pills are wider
        box = binary[y : y + h, x : x + w]
        fill = area / (w * h)
        holes = (box == 255).mean()
        if fill > 0.7 and holes > 0.05:
            boxes.append((x, y, w, h))
    return boxes


class TesseractOCR:
    """Runs Tesseract on a grayscale crop.

    Uses tesserocr when it is installed (Pi: python3-tesserocr), which keeps the
    model loaded between checks: ~0.5 s per box on a Pi Zero instead of ~7 s,
    since starting the tesseract program reloads the model every time. Falls
    back to running the tesseract program.
    """

    def __init__(self, psm: int = 6, lang: str = "eng", timeout: float = 15):
        self.api = None
        try:
            import tesserocr

            self.api = tesserocr.PyTessBaseAPI(lang=lang, psm=psm)
            log.info("OCR: tesserocr (model stays loaded)")
            return
        except ImportError:
            pass
        except Exception as e:  # e.g. language data not found
            log.warning("tesserocr failed to start (%s); using the tesseract program", e)
        if not shutil.which("tesseract"):
            raise RuntimeError(
                "tesseract is not installed (Pi: sudo apt install tesseract-ocr, "
                "Mac: brew install tesseract)"
            )
        log.info("OCR: tesseract program (slower; install python3-tesserocr to speed up)")
        self.cmd = ["tesseract", "stdin", "stdout", "--psm", str(psm), "-l", lang]
        self.timeout = timeout

    def __call__(self, img, psm: int | None = None) -> str:
        """psm overrides the page mode for this call (8 = a single word)."""
        if self.api is not None:
            import numpy as np

            img = np.ascontiguousarray(img)
            h, w = img.shape[:2]
            old = self.api.GetPageSegMode()
            if psm is not None:
                self.api.SetPageSegMode(psm)
            try:
                self.api.SetImageBytes(img.tobytes(), w, h, 1, w)
                self.api.SetSourceResolution(300)  # else it logs "Estimating resolution" every box
                return self.api.GetUTF8Text()
            finally:
                self.api.SetPageSegMode(old)

        import cv2

        ok, buf = cv2.imencode(".png", img)
        if not ok:
            return ""
        cmd = list(self.cmd)
        if psm is not None:
            cmd[cmd.index("--psm") + 1] = str(psm)
        out = subprocess.run(cmd, input=buf.tobytes(), capture_output=True, timeout=self.timeout)
        return out.stdout.decode("utf-8", "replace")


class AdDetector:
    def __init__(self, cfg: dict[str, Any], ocr: Callable[[Any], str] | None = None):
        self.matcher = TextMatcher(cfg["patterns"])
        self.regions = list(cfg["regions"])
        self.scale = float(cfg.get("ocr_scale", 2.5))
        self.min_contrast = float(cfg.get("min_contrast", 12.0))
        self.ocr = ocr or TesseractOCR(psm=int(cfg.get("tesseract_psm", 6)))

    def detect(self, frame) -> Detection:
        start = time.monotonic()
        results = []
        for region in self.regions:
            res = RegionResult(name=region["name"])
            patch = crop(frame, region["box"])
            if patch.size == 0:
                res.skipped = True
                results.append(res)
                continue
            img, contrast = preprocess(patch, self.scale)
            if contrast < self.min_contrast:
                res.skipped = True
            else:
                res.text = self.ocr(img).strip()
                res.matched = self.matcher.match(res.text)
            results.append(res)
            if res.matched:
                break  # one badge is enough; save the Pi some work
        det = Detection(
            is_ad=any(r.matched for r in results),
            regions=results,
            seconds=time.monotonic() - start,
        )
        for r in results:
            if r.matched:
                text = TextMatcher.normalize(r.text)
                det.strong = bool(_STRONG.search(text))
                det.position, det.remaining = parse_badge(text)
        return det
