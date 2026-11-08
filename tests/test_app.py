import shutil
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
cv2 = pytest.importorskip("cv2")

pytestmark = pytest.mark.skipif(not shutil.which("tesseract"), reason="tesseract not installed")

MAX_BOX = [0.04, 0.035, 0.16, 0.08]


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def show_frame(seed):
    rng = np.random.default_rng(seed)
    small = rng.integers(60, 180, (9, 16, 3), dtype=np.uint8)  # smooth, like video
    return cv2.resize(small, (1280, 720), interpolation=cv2.INTER_CUBIC)


def ad_frame(seed):
    """The real Max badge pasted over a busy picture."""
    badge = cv2.imread(str(Path(__file__).with_name("max_ad_frame.jpg")))
    frame = show_frame(seed)
    src, dst = badge[40:70, 90:180], frame[40:70, 90:180]
    mask = src.max(axis=2) > 60  # the pill and text, not the black around them
    dst[mask] = src[mask]
    # "3 of 3" sits on a dark pill in the real app
    frame[45:64, 128:172] = np.where(src[5:24, 38:82] > 60, src[5:24, 38:82], 35)
    return frame


def make_app(tmp_path):
    from adhush.app import App
    from adhush.config import DEFAULTS
    import copy

    cfg = copy.deepcopy(DEFAULTS)
    cfg["camera"]["source"] = str(Path(__file__).with_name("max_ad_frame.jpg"))
    cfg["detector"]["regions"] = [{"name": "max", "box": MAX_BOX}]
    cfg["_config_path"] = str(tmp_path / "config.yaml")
    app = App(cfg)
    clock = Clock()
    app.controller.clock = clock
    return app, clock


def run(app, clock, frames, ocr_every=4):
    """Feed frames ~4 per second; OCR keeps up with every 4th, like on a Pi Zero."""
    events = []
    for i, frame in enumerate(frames):
        clock.t += 0.25
        app.camera.read = lambda f=frame: f
        if i % ocr_every == 0:
            app.camera.read()  # make it the latest frame for OCR
            with app.lock:
                app.frame, app.frame_time = frame, clock.t
            app._frame_ready.set()
            app.ocr_step()
        before = len(app.events)
        app.step()
        if len(app.events) > before:
            events.append((i, app.events[0]["event"]))
    return events


def test_learns_badge_then_mutes_and_unmutes_fast(tmp_path):
    app, clock = make_app(tmp_path)
    # First ad ever: OCR reads the full badge, mutes, and learns the pill.
    frames = [show_frame(i) for i in range(8)] + [ad_frame(i) for i in range(12)] + [
        show_frame(100 + i) for i in range(12)
    ]
    events = run(app, clock, frames)
    assert app.templates, "should have learned the Ad pill"
    assert list((tmp_path / "badges").glob("max-*.png"))
    assert events[0][1] == "muted" and 8 <= events[0][0] <= 19  # OCR speed: within the ad
    assert events[-1][1] == "unmuted"

    # Next ad: the remembered picture catches it within a frame or two of the
    # badge appearing, and unmutes ~1 s after it goes away.
    frames = [show_frame(200 + i) for i in range(6)] + [ad_frame(300 + i) for i in range(20)] + [
        show_frame(400 + i) for i in range(12)
    ]
    events = run(app, clock, frames, ocr_every=1000)  # no OCR help at all
    assert events[0] == (7, "muted"), events  # badge appears at frame 6
    unmute = [i for i, e in events if e == "unmuted"]
    assert unmute and 26 + 3 <= unmute[0] <= 26 + 6, events  # badge gone at frame 26
    assert [e for _, e in events] == ["muted", "unmuted"]


def test_no_false_mute_on_normal_picture(tmp_path):
    app, clock = make_app(tmp_path)
    run(app, clock, [ad_frame(1)] * 4 + [show_frame(9)] * 20)  # learn the pill first
    app.controller.set_enabled(True)
    events = run(app, clock, [show_frame(500 + i) for i in range(40)])
    assert events == []
