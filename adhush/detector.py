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
    return binary, contrast


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

    def __call__(self, img) -> str:
        if self.api is not None:
            import numpy as np

            img = np.ascontiguousarray(img)
            h, w = img.shape[:2]
            self.api.SetImageBytes(img.tobytes(), w, h, 1, w)
            return self.api.GetUTF8Text()

        import cv2

        ok, buf = cv2.imencode(".png", img)
        if not ok:
            return ""
        out = subprocess.run(
            self.cmd, input=buf.tobytes(), capture_output=True, timeout=self.timeout
        )
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
        return Detection(
            is_ad=any(r.matched for r in results),
            regions=results,
            seconds=time.monotonic() - start,
        )
