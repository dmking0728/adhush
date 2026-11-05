"""Command line: `adhush run`, `adhush test-tv`, `adhush detect`, `adhush snapshot`."""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import time

from adhush import __version__
from adhush.config import load_config


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, str(level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def cmd_run(cfg) -> int:
    from adhush.app import App
    from adhush.web import start_web

    app = App(cfg)
    if cfg["web"]["enabled"]:
        start_web(app, cfg["web"]["host"], int(cfg["web"]["port"]))
    signal.signal(signal.SIGTERM, lambda *_: app.stop.set())
    try:
        app.run()
    except KeyboardInterrupt:
        app.stop.set()
    return 0


def cmd_test_tv(cfg) -> int:
    from adhush.muter import build_muter

    muter = build_muter(cfg["tv"])
    print(f"Backend: {muter.name}. If the TV asks to allow 'AdHush', choose Allow.")
    try:
        print("Current mute state:", muter.get_mute())
    except Exception as e:
        print("Could not read mute state:", e)
    print("Muting for 3 seconds...")
    muter.set_mute(True)
    time.sleep(3)
    muter.set_mute(False)
    print("Unmuted. If the TV went quiet and came back, you're set.")
    return 0


def cmd_detect(cfg, image: str) -> int:
    import cv2

    from adhush.detector import AdDetector

    frame = cv2.imread(image)
    if frame is None:
        print(f"Could not read {image}", file=sys.stderr)
        return 2
    det = AdDetector(cfg["detector"]).detect(frame)
    for r in det.regions:
        state = "blank" if r.skipped else ("AD" if r.matched else "-")
        print(f"{r.name:>14}  [{state:5}]  {r.text!r}")
    print(f"\nAd on screen: {det.is_ad}  ({det.seconds:.2f}s)")
    return 0 if det.is_ad else 1


def cmd_snapshot(cfg, out: str) -> int:
    import cv2

    from adhush.camera import Camera

    cam = Camera(cfg["camera"]["source"], cfg["camera"]["width"], cfg["camera"]["height"])
    frame = None
    for _ in range(10):  # let auto-exposure settle
        frame = cam.read()
    cam.close()
    if frame is None:
        print("No frame from camera", file=sys.stderr)
        return 2
    cv2.imwrite(out, frame)
    print(f"Saved {out} ({frame.shape[1]}x{frame.shape[0]})")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="adhush", description="Mute TV ads using a webcam.")
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("-c", "--config", help="config file (default: $ADHUSH_CONFIG, /etc/adhush/config.yaml, ./config.yaml)")
    p.add_argument("--dry-run", action="store_true", help="never touch the TV, just log")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("run", help="watch the TV and mute ads (default)")
    sub.add_parser("test-tv", help="mute the TV for 3 seconds to check the connection")
    d = sub.add_parser("detect", help="run detection on one saved image")
    d.add_argument("image")
    s = sub.add_parser("snapshot", help="save one camera frame to a file")
    s.add_argument("out", nargs="?", default="snapshot.jpg")
    args = p.parse_args(argv)

    cfg = load_config(args.config)
    if args.dry_run:
        cfg["tv"]["backend"] = "dry_run"
    _setup_logging("DEBUG" if args.verbose else cfg["log_level"])

    if args.cmd == "test-tv":
        return cmd_test_tv(cfg)
    if args.cmd == "detect":
        return cmd_detect(cfg, args.image)
    if args.cmd == "snapshot":
        return cmd_snapshot(cfg, args.out)
    return cmd_run(cfg)
