"""The main loop: camera -> detector -> controller, plus state for the web UI."""

from __future__ import annotations

import logging
import re
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

from adhush.camera import Camera
from adhush.config import save_regions, validate_regions
from adhush.controller import MuteController
from adhush.detector import AdDetector, Detection
from adhush.muter import build_muter

log = logging.getLogger(__name__)

# Text that hints at an ad badge even when the "Ad" word itself wasn't read:
# "3 of 3", "30f3", "0:30". Only used to decide which frames to save.
AD_LIKE = re.compile(r"\d\s*[o0]f\s*\d|\d:\d\d", re.IGNORECASE)


class App:
    def __init__(self, cfg: dict[str, Any]):
        self.cfg = cfg
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.camera = Camera(cfg["camera"]["source"], cfg["camera"]["width"], cfg["camera"]["height"])
        self.detector = AdDetector(cfg["detector"])
        self.muter = build_muter(cfg["tv"])
        c = cfg["controller"]
        self.controller = MuteController(
            self.muter,
            c["mute_after"],
            c["unmute_after"],
            c["max_mute_seconds"],
            c.get("unmute_after_seconds", 3),
            c.get("unmute_between_ads_seconds", 12),
        )
        self.frame = None
        self.detection: Detection | None = None
        self._last_debug_save = 0.0
        self.events: deque[dict[str, Any]] = deque(maxlen=50)
        self.started = time.time()

    def _event(self, name: str) -> None:
        self.events.appendleft({"t": time.time(), "event": name})

    def step(self) -> None:
        frame = self.camera.read()
        if frame is None:
            return
        detection = self.detector.detect(frame)
        with self.lock:
            self.frame = frame
            self.detection = detection
            if self.controller.muted_by_us and not detection.is_ad:
                # Misses during an ad show what OCR read, to tune patterns.
                texts = {r.name: r.text for r in detection.regions if r.text}
                log.info("No badge this check while muted: %s", texts)
            event = self.controller.update(
                detection.is_ad, detection.strong, detection.position, detection.remaining
            )
            if event:
                self._event(event)
        self._maybe_save_debug_frame(frame, detection)
        log.debug(
            "ad=%s %.2fs %s",
            detection.is_ad,
            detection.seconds,
            {r.name: r.text for r in detection.regions if r.text},
        )

    def _maybe_save_debug_frame(self, frame, detection: Detection) -> None:
        opts = self.cfg.get("debug_frames") or {}
        folder = opts.get("dir")
        if not folder:
            return
        texts = " ".join(r.text for r in detection.regions if r.text)
        if not (detection.is_ad or AD_LIKE.search(texts)):
            return
        now = time.time()
        if now - self._last_debug_save < float(opts.get("min_seconds_apart", 20)):
            return
        self._last_debug_save = now
        try:
            import cv2

            d = Path(folder).expanduser()
            d.mkdir(parents=True, exist_ok=True)
            tag = "ad" if detection.is_ad else "maybe"
            name = time.strftime("%Y%m%d-%H%M%S") + f"-{tag}.jpg"
            cv2.imwrite(str(d / name), frame)
            log.info("Saved %s frame %s (text: %r)", tag, name, texts[:60])
            old = sorted(d.glob("*.jpg"))[: -int(opts.get("keep", 60))]
            for p in old:
                p.unlink()
        except Exception:
            log.exception("Could not save debug frame")

    def run(self) -> None:
        interval = float(self.cfg["detector"]["check_interval"])
        log.info("AdHush running: TV backend=%s, %d regions", self.muter.name, len(self.detector.regions))
        while not self.stop.is_set():
            start = time.monotonic()
            try:
                self.step()
            except Exception:
                log.exception("Check failed")
                self.stop.wait(2)
            self.stop.wait(max(0.0, interval - (time.monotonic() - start)))
        with self.lock:
            if self.controller.muted_by_us:
                self.controller.set_enabled(False)  # don't leave the TV muted on exit
        self.camera.close()

    # --- used by the web UI -------------------------------------------------

    def status(self) -> dict[str, Any]:
        with self.lock:
            d = self.detection
            ctl = self.controller
            return {
                "enabled": ctl.enabled,
                "muted_by_adhush": ctl.muted_by_us,
                "suppressed": ctl.suppressed,
                "is_ad": bool(d and d.is_ad),
                "check_seconds": round(d.seconds, 2) if d else None,
                "backend": self.muter.name,
                "regions": self.detector.regions,
                "region_results": [
                    {"name": r.name, "text": r.text, "matched": r.matched, "skipped": r.skipped}
                    for r in (d.regions if d else [])
                ],
                "events": list(self.events),
                "uptime": round(time.time() - self.started),
            }

    def set_enabled(self, enabled: bool) -> None:
        with self.lock:
            event = self.controller.set_enabled(enabled)
            self._event("resumed" if enabled else "paused")
            if event:
                self._event(event)

    def set_regions(self, regions: Any) -> None:
        clean = validate_regions(regions)
        path = save_regions(self.cfg["_config_path"], clean)
        with self.lock:
            self.detector.regions = clean
        log.info("Saved %d regions to %s", len(clean), path)

    def test_mute(self, muted: bool) -> None:
        self.muter.set_mute(muted)

    def snapshot_jpeg(self) -> bytes | None:
        import cv2

        with self.lock:
            if self.frame is None:
                return None
            img = self.frame.copy()
            regions = list(self.detector.regions)
            matched = {r.name for r in (self.detection.regions if self.detection else []) if r.matched}
        h, w = img.shape[:2]
        for r in regions:
            x, y, bw, bh = r["box"]
            color = (0, 0, 255) if r["name"] in matched else (0, 220, 0)
            p0, p1 = (int(x * w), int(y * h)), (int((x + bw) * w), int((y + bh) * h))
            cv2.rectangle(img, p0, p1, color, 2)
            cv2.putText(img, r["name"], (p0[0] + 4, p0[1] + 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 75])
        return buf.tobytes() if ok else None

    def raw_jpeg(self) -> bytes | None:
        import cv2

        with self.lock:
            if self.frame is None:
                return None
            ok, buf = cv2.imencode(".jpg", self.frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
        return buf.tobytes() if ok else None
