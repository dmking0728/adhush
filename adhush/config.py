"""Config loading. Everything has a default so a near-empty config works."""

from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

import yaml

# Regions are boxes on the camera image, as fractions of width/height:
# [x, y, w, h]. The defaults cover the four corners of the picture, which is
# where the streaming apps put their "Ad 1 of 3 · 0:30" badge. They are a
# starting point: use the web UI to draw tight boxes around the real badge.
DEFAULT_REGIONS = [
    {"name": "top-left", "box": [0.00, 0.00, 0.35, 0.18]},
    {"name": "top-right", "box": [0.65, 0.00, 0.35, 0.18]},
    {"name": "bottom-left", "box": [0.00, 0.80, 0.35, 0.20]},
    {"name": "bottom-right", "box": [0.65, 0.80, 0.35, 0.20]},
]

DEFAULTS: dict[str, Any] = {
    "camera": {
        # A device index (0 = /dev/video0), or a path to an image/video file
        # for testing on a laptop.
        "source": 0,
        "width": 1280,
        "height": 720,
    },
    "detector": {
        "check_interval": 1.0,  # seconds between text-reading checks (Pi Zero W is slow)
        # Once an app's "Ad" pill has been seen, AdHush remembers its picture
        # (in a "badges" folder next to the config) and looks for it this often.
        "picture_check_interval": 0.25,
        "picture_match_threshold": 0.7,
        "ocr_scale": 2.5,  # upscale crops before OCR; small text reads better
        "min_contrast": 12.0,  # skip OCR on near-blank crops (saves CPU)
        "tesseract_psm": 11,  # sparse text: a badge in an otherwise empty box
        # OCR drops spaces and mixes up 1/i/l, so "Ad 1 of 3" may come back as
        # "Adiof3". These patterns allow for that.
        "patterns": [
            r"\bad\b",  # "Ad", "AD"
            r"\bads?\s*[0-9il|]{1,2}\s*(of|/)\s*[0-9il|]{1,2}",  # "Ad 1 of 3", "Adiof3", "Ads 2/4"
            r"\bads?\s*[:\-]?\s*\d{1,2}:\d{2}",  # "Ad 0:30", "Ad0:30"
            # A box that *starts* with "3 of 3" (Max's badge, when OCR misses
            # the "Ad" pill). Anchored so "Season 1 of 3" doesn't count.
            r"^\W*[0-9il]{1,2}\s*[o0]f\s*[0-9il]{1,2}\b",
            # "3 of 3 :07" - ad position plus countdown, wherever it is. OCR
            # often loses the colon ("1of3 .20", "20f3 08") or the first digit
            # ("of3 :10").
            r"[0-9il]{1,2}\s*[o0]f\s*[0-9il]{1,2}\W{0,4}[:.]?\s*\d{2,3}\b",
            r"\b[o0]f\s*[0-9il]\W{0,4}[:.]\s*\d{2}\b",
            r"\badvertisement\b",
            r"\bcommercial\b",
        ],
        "regions": DEFAULT_REGIONS,
    },
    "controller": {
        # A bare "ad" must be seen this many checks in a row. A full badge
        # ("Ad 1 of 3", "1 of 3 :20") mutes on the first check.
        "mute_after": 2,
        "unmute_after": 2,  # no ad this many checks in a row -> unmute
        "unmute_after_seconds": 3,  # ...and for at least this long
        # If the last badge said more ads are coming ("1 of 3"), wait this long
        # instead: apps show a few seconds without a badge between ads.
        "unmute_between_ads_seconds": 12,
        # Same two waits once AdHush has learned what the badge looks like and
        # checks for it several times a second: it no longer misreads, so it
        # can unmute almost as soon as the badge goes away.
        "unmute_after_seconds_picture": 1.0,
        "unmute_between_ads_seconds_picture": 4,
        "max_mute_seconds": 240,  # safety net: never stay muted longer
    },
    "tv": {
        # samsung     - try UPnP (exact mute on/off), fall back to the remote API
        # samsung_upnp, samsung_ws - force one method
        # dry_run     - just log, never touch the TV
        "backend": "dry_run",
        "host": "",
        "token_file": "~/.config/adhush/tv-token.txt",
    },
    "web": {"enabled": True, "host": "0.0.0.0", "port": 8080},
    # Save camera frames that look ad-related (a match, or text like "3 of 3"
    # or "0:30") so you can see what the badge looks like and tune boxes.
    "debug_frames": {"dir": "", "min_seconds_apart": 20, "keep": 60},
    "log_level": "INFO",
}


def _merge(base: dict, over: dict) -> dict:
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge(base[key], value)
        else:
            base[key] = value
    return base


def default_config_path() -> str:
    env = os.environ.get("ADHUSH_CONFIG")
    if env:
        return env
    for candidate in ("/etc/adhush/config.yaml", "config.yaml"):
        if Path(candidate).exists():
            return candidate
    return "config.yaml"


def regions_path(config_path: str) -> Path:
    """Regions drawn in the web UI live next to the config, in regions.yaml."""
    return Path(config_path).expanduser().with_name("regions.yaml")


def load_config(path: str | None = None) -> dict[str, Any]:
    path = path or default_config_path()
    cfg = copy.deepcopy(DEFAULTS)
    p = Path(path).expanduser()
    if p.exists():
        with p.open() as f:
            _merge(cfg, yaml.safe_load(f) or {})
    rp = regions_path(path)
    if rp.exists():
        with rp.open() as f:
            saved = yaml.safe_load(f) or []
        if saved:
            cfg["detector"]["regions"] = saved
    cfg["_config_path"] = str(p)
    return cfg


def validate_regions(regions: Any) -> list[dict[str, Any]]:
    if not isinstance(regions, list):
        raise ValueError("regions must be a list")
    clean = []
    for i, r in enumerate(regions):
        box = [float(v) for v in r["box"]]
        if len(box) != 4:
            raise ValueError(f"region {i}: box needs 4 numbers")
        x, y, w, h = box
        if not (0 <= x < 1 and 0 <= y < 1 and 0 < w <= 1 and 0 < h <= 1):
            raise ValueError(f"region {i}: box values must be fractions 0..1")
        name = str(r.get("name") or f"region-{i + 1}")[:40]
        clean.append({"name": name, "box": [round(v, 4) for v in box]})
    return clean


def save_regions(config_path: str, regions: list[dict[str, Any]]) -> Path:
    rp = regions_path(config_path)
    rp.parent.mkdir(parents=True, exist_ok=True)
    tmp = rp.with_suffix(".tmp")
    with tmp.open("w") as f:
        yaml.safe_dump(regions, f, sort_keys=False)
    tmp.replace(rp)
    return rp
