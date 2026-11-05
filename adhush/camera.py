"""Grabs the newest frame from a webcam, or from an image/video file for testing."""

from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

log = logging.getLogger(__name__)

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


class Camera:
    def __init__(self, source, width: int = 1280, height: int = 720):
        import cv2

        self.cv2 = cv2
        self.width, self.height = width, height
        self.still = None
        self.cap = None
        if isinstance(source, str) and source.isdigit():
            source = int(source)
        self.source = source
        if isinstance(source, str) and Path(source).suffix.lower() in IMAGE_EXTS:
            self.still = cv2.imread(source)
            if self.still is None:
                raise RuntimeError(f"could not read image {source}")
        else:
            self._open()

    def _open(self) -> None:
        cv2 = self.cv2
        if isinstance(self.source, int) and sys.platform.startswith("linux"):
            self.cap = cv2.VideoCapture(self.source, cv2.CAP_V4L2)
        else:
            self.cap = cv2.VideoCapture(self.source)
        if isinstance(self.source, int):
            # MJPEG lets cheap USB webcams deliver 720p over USB 2.0.
            self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
            self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        if not self.cap.isOpened():
            log.warning("Camera %r did not open", self.source)

    def read(self):
        """Return the latest frame (BGR numpy array), or None if unavailable."""
        if self.still is not None:
            return self.still.copy()
        if self.cap is None or not self.cap.isOpened():
            time.sleep(2)
            self._open()
            return None
        if isinstance(self.source, int):
            # Drop the buffered frame so we look at "now", not a second ago.
            self.cap.grab()
        ok, frame = self.cap.read()
        if not ok:
            if not isinstance(self.source, int):  # video file ended: loop it
                self.cap.set(self.cv2.CAP_PROP_POS_FRAMES, 0)
                ok, frame = self.cap.read()
            if not ok:
                log.warning("Camera read failed; reopening")
                self.cap.release()
                self.cap = None
                return None
        return frame

    def close(self) -> None:
        if self.cap is not None:
            self.cap.release()
