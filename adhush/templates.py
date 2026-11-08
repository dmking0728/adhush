"""Fast badge check: remember what an app's "Ad" pill looks like, then look
for that exact picture in every frame.

Reading text with Tesseract takes ~1 s per box on a Pi Zero, and it misreads
badges over busy video now and then. Image matching against a remembered pill
takes ~15 ms and doesn't care what's behind the badge, so AdHush can check
several times a second and react to an ad starting or ending almost at once.

Templates are learned automatically: when OCR reads a full badge ("Ad 1 of 3")
in a box, any solid pill in that box whose own text reads "Ad" is saved as a
PNG in the templates folder (next to the config). Delete a PNG to forget it.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Callable

from adhush.detector import crop, pill_boxes, preprocess

log = logging.getLogger(__name__)

AD_WORD = re.compile(r"\bad\b", re.IGNORECASE)
MAX_PER_REGION = 4


def _gray(img):
    import cv2

    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img


class BadgeTemplates:
    def __init__(self, folder: str | Path | None, threshold: float = 0.7, scale: float = 2.5):
        self.folder = Path(folder).expanduser() if folder else None
        self.threshold = float(threshold)
        self.scale = float(scale)
        self.templates: dict[str, list[Any]] = {}
        self.load()

    def __bool__(self) -> bool:
        return any(self.templates.values())

    def load(self) -> None:
        import cv2

        self.templates = {}
        if not self.folder or not self.folder.is_dir():
            return
        for p in sorted(self.folder.glob("*.png")):
            region = p.stem.rsplit("-", 1)[0]
            img = cv2.imread(str(p), cv2.IMREAD_GRAYSCALE)
            if img is not None:
                self.templates.setdefault(region, []).append(img)
        if self:
            log.info("Loaded badge pictures for: %s", ", ".join(sorted(self.templates)))

    def score(self, frame, regions: list[dict[str, Any]]) -> tuple[float, str | None]:
        """Best match score (0..1) of any remembered pill inside its box."""
        import cv2
        import numpy as np

        best, where = 0.0, None
        for region in regions:
            temps = self.templates.get(region["name"])
            if not temps:
                continue
            patch = _gray(crop(frame, region["box"]))
            for t in temps:
                th, tw = t.shape
                if patch.shape[0] < th or patch.shape[1] < tw:
                    continue
                res = cv2.matchTemplate(patch, t, cv2.TM_CCOEFF_NORMED)
                res = np.nan_to_num(res, nan=0.0, posinf=0.0, neginf=0.0)
                _, s, _, (x, y) = cv2.minMaxLoc(res)
                # A flat (blank) spot can score high by accident: require the
                # matched spot to have real detail, like the pill does.
                spot = patch[y : y + th, x : x + tw]
                if s > best and spot.std() > 0.3 * max(t.std(), 1.0):
                    best, where = float(s), region["name"]
        return best, where

    def matches(self, frame, regions: list[dict[str, Any]], still_there: bool = False) -> bool:
        """still_there: the badge was just seen, so accept a weaker match."""
        return self.score(frame, regions)[0] >= self.threshold * (0.8 if still_there else 1.0)

    def learn(self, frame, region: dict[str, Any], ocr: Callable[..., str]) -> bool:
        """Look for an "Ad" pill in this box and remember it. True if a new one was saved."""
        import cv2

        if not self.folder:
            return False
        name = region["name"]
        if len(self.templates.get(name, [])) >= MAX_PER_REGION:
            return False
        if self.score(frame, [region])[0] >= 0.9:
            return False  # already know this one
        patch = crop(frame, region["box"])
        binary, _ = preprocess_unflipped(patch, self.scale)
        gray = _gray(patch)
        for x, y, w, h in pill_boxes(binary):
            # Back to unscaled pixels. OCR the pill with a little margin, but
            # save only its inside: the background around a pill changes with
            # the video (a dark margin scored 0.4 over a pink scene).
            x0, y0 = int(x / self.scale), int(y / self.scale)
            x1, y1 = int((x + w) / self.scale), int((y + h) / self.scale)
            outer = gray[max(0, y0 - 2) : y1 + 2, max(0, x0 - 2) : x1 + 2]
            pill = gray[y0 + 1 : y1 - 1, x0 + 1 : x1 - 1]
            if pill.shape[0] < 8 or pill.shape[1] < 8:
                continue
            text, _ = preprocess(outer, self.scale * 2)
            if not AD_WORD.search(ocr(text, psm=8) or ""):  # 8: a single word
                continue
            self.folder.mkdir(parents=True, exist_ok=True)
            n = len(self.templates.get(name, []))
            path = self.folder / f"{name}-{n + 1}.png"
            cv2.imwrite(str(path), pill)
            self.templates.setdefault(name, []).append(pill.copy())
            log.info("Learned the %r Ad badge picture (%dx%d) -> %s", name, pill.shape[1], pill.shape[0], path)
            return True
        return False


def preprocess_unflipped(img, scale: float):
    """Like detector.preprocess but leaves pills solid, so they can be found."""
    import cv2

    gray = _gray(img)
    if scale and scale != 1:
        gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    gray = cv2.GaussianBlur(gray, (3, 3), 0)
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    if binary.mean() < 127:
        binary = 255 - binary
    return binary, float(gray.std())
