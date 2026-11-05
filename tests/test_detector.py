import shutil

import pytest

from adhush.config import DEFAULTS
from adhush.detector import AdDetector, TextMatcher

matcher = TextMatcher(DEFAULTS["detector"]["patterns"])


@pytest.mark.parametrize(
    "text",
    [
        "Ad", "AD 0:30", "Ad 1 of 3 · 0:45", "Ads 2/4", "Ad • 1:15", "Advertisement", "ad|0:12",
        # real OCR misreads: lost spaces, 1 read as i or l
        "Adiof3 0:45", "Adlof2", "Ad0:30", "Ad 2of4",
    ],
)
def test_ad_badges_match(text):
    assert matcher.match(text)


@pytest.mark.parametrize(
    "text",
    [
        "", "Bad Day", "Ready", "Adam", "Episode 4", "Skip Intro", "HEADLINE", "Up Next 0:15",
        "adios", "Adam 1:30", "Season 1 of 3", "load 1 of 3",
    ],
)
def test_normal_text_does_not_match(text):
    assert matcher.match(text) is None


np = pytest.importorskip("numpy")
cv2 = pytest.importorskip("cv2")


def fake_tv_frame(badge: str | None):
    frame = np.full((720, 1280, 3), 40, np.uint8)
    cv2.circle(frame, (640, 360), 200, (90, 140, 200), -1)  # "show content"
    if badge:
        cv2.rectangle(frame, (1000, 30), (1260, 90), (20, 20, 20), -1)
        cv2.putText(frame, badge, (1015, 72), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (240, 240, 240), 2)
    return frame


def test_detector_with_fake_ocr_reads_only_regions():
    seen = []

    def fake_ocr(img):
        seen.append(img.shape)
        return "Ad 1 of 2"

    cfg = dict(DEFAULTS["detector"], regions=[{"name": "tr", "box": [0.75, 0.0, 0.25, 0.15]}])
    det = AdDetector(cfg, ocr=fake_ocr).detect(fake_tv_frame("Ad 1 of 2"))
    assert det.is_ad and len(seen) == 1


def test_blank_region_skips_ocr():
    def boom(img):
        raise AssertionError("OCR should not run on a blank crop")

    cfg = dict(DEFAULTS["detector"], regions=[{"name": "tl", "box": [0.0, 0.0, 0.2, 0.1]}])
    det = AdDetector(cfg, ocr=boom).detect(fake_tv_frame(None))
    assert not det.is_ad and det.regions[0].skipped


@pytest.mark.skipif(not shutil.which("tesseract"), reason="tesseract not installed")
def test_real_ocr_end_to_end():
    cfg = dict(DEFAULTS["detector"], regions=[{"name": "tr", "box": [0.75, 0.0, 0.25, 0.15]}])
    detector = AdDetector(cfg)
    assert detector.detect(fake_tv_frame("Ad 1 of 3  0:45")).is_ad
    assert not detector.detect(fake_tv_frame("Up Next")).is_ad
