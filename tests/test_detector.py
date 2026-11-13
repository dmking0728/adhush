import shutil

import pytest

from adhush.config import DEFAULTS
from adhush.detector import AdDetector, TextMatcher

matcher = TextMatcher(DEFAULTS["detector"]["patterns"])


@pytest.mark.parametrize(
    "text",
    [
        "Ad", "AD 0:30", "1:04 [Ad", "[0:57 Ad", "Ad 1 of 3 · 0:45", "Ads 2/4", "Ad • 1:15", "Advertisement", "ad|0:12",
        # real OCR misreads: lost spaces, 1 read as i or l
        "Adiof3 0:45", "Adlof2", "Ad0:30", "Ad 2of4",
        # Max: yellow "Ad" pill + "3 of 3"
        "3 of 3", "30f3", "“Ad> 3of3", "‘ad? 30f3", "r,,%\nAd, 30f3",
        # real misses from a Max ad break
        "Al (30f3 :07|", "(30f3", "x 2 of 3 :17",
        "Ris 1of3 .20", "_, 10f3\n\n18", "ip 1of3 174", "em 20f3 08", "or |\n\nof3 .02 78",
    ],
)
def test_ad_badges_match(text):
    assert matcher.match(text)


@pytest.mark.parametrize(
    "text",
    [
        "", "Bad Day", "Ready", "Adam", "Episode 4", "Skip Intro", "HEADLINE", "Up Next 0:15",
        "adios", "Adam 1:30", "Season 1 of 3", "load 1 of 3", "Episode 3 of 8", "Chapter 2 of 5",
        "We've seen the United States and done it, so ready",
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


@pytest.mark.skipif(not shutil.which("tesseract"), reason="tesseract not installed")
def test_real_max_ad_frame():
    """A real capture of Max's badge: dark "Ad" on a yellow pill, then "3 of 3"."""
    from pathlib import Path

    frame = cv2.imread(str(Path(__file__).with_name("max_ad_frame.jpg")))
    cfg = dict(DEFAULTS["detector"], regions=[{"name": "max", "box": [0.04, 0.035, 0.16, 0.08]}])
    det = AdDetector(cfg).detect(frame)
    assert det.is_ad, det.regions[0].text
    # And the "Ad" pill itself is read, not just "3 of 3"
    assert TextMatcher([r"\bad\b"]).match(det.regions[0].text), det.regions[0].text


@pytest.mark.parametrize(
    "text,position,remaining",
    [
        ("Ad 1 of 3 · 0:45", (1, 3), 45),
        ("Ris 1of3 .20", (1, 3), 20),
        ("em 20f3 08", (2, 3), 8),
        ("Al (30f3 :07|", (3, 3), 7),
        ("“Ad 30f3", (3, 3), None),
        ("Ad", None, None),
    ],
)
def test_parse_badge(text, position, remaining):
    from adhush.detector import parse_badge

    assert parse_badge(text) == (position, remaining)


@pytest.mark.skipif(not shutil.which("tesseract"), reason="tesseract not installed")
def test_real_hulu_ad_frame():
    """Hulu: countdown pill "1:04" then a gold "Ad" pill, top-right."""
    from pathlib import Path

    frame = cv2.imread(str(Path(__file__).with_name("hulu_ad_frame.jpg")))
    cfg = dict(DEFAULTS["detector"], regions=[{"name": "hulu", "box": [0.86, 0.03, 0.115, 0.085]}])
    det = AdDetector(cfg).detect(frame)
    assert det.is_ad and det.strong, det.regions[0].text
