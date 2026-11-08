"""Decides when to mute and unmute.

Rules, in plain words:
* Mute after the ad badge shows up `mute_after` checks in a row.
* Unmute after it is gone `unmute_after` checks in a row *and* for at least
  `unmute_after_seconds`. OCR misreads the badge now and then, and on a slow
  Pi three misses in a row can take only a few seconds; the time rule keeps
  one blurry stretch from unmuting in the middle of an ad.
* Only unmute if AdHush did the muting. If you muted the TV yourself before
  the ad, AdHush leaves it muted.
* Never stay muted longer than `max_mute_seconds`. If that trips, it is
  probably a false match (say, a paused screen), so stand down until the
  badge goes away.
"""

from __future__ import annotations

import logging
import time
from typing import Callable

from adhush.muter import Muter

log = logging.getLogger(__name__)


class MuteController:
    def __init__(
        self,
        muter: Muter,
        mute_after: int = 2,
        unmute_after: int = 3,
        max_mute_seconds: float = 240,
        unmute_after_seconds: float = 8,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.muter = muter
        self.mute_after = max(1, int(mute_after))
        self.unmute_after = max(1, int(unmute_after))
        self.max_mute_seconds = float(max_mute_seconds)
        self.unmute_after_seconds = float(unmute_after_seconds)
        self.clock = clock

        self.enabled = True
        self.muted_by_us = False
        self.muted_at = 0.0
        self.ad_streak = 0
        self.clear_streak = 0
        self.last_ad_at = float("-inf")
        self.ad_handled = False  # this ad break was already dealt with
        self.suppressed = False  # safety timeout tripped; wait for a clear screen

    def update(self, is_ad: bool) -> str | None:
        """Feed one detection result. Returns an event name when something happened."""
        if is_ad:
            self.ad_streak += 1
            self.clear_streak = 0
            self.last_ad_at = self.clock()
        else:
            self.clear_streak += 1
            self.ad_streak = 0

        clear = (
            self.clear_streak >= self.unmute_after
            and self.clock() - self.last_ad_at >= self.unmute_after_seconds
        )
        if clear:
            self.ad_handled = False
            self.suppressed = False

        if self.muted_by_us:
            if clear:
                return self._unmute("ad over")
            if self.clock() - self.muted_at > self.max_mute_seconds:
                event = self._unmute("safety timeout")
                if event == "unmuted":
                    self.suppressed = True
                    self.ad_handled = True
                return event
            return None

        if (
            self.enabled
            and not self.suppressed
            and not self.ad_handled
            and self.ad_streak >= self.mute_after
        ):
            return self._mute()
        return None

    def set_enabled(self, enabled: bool) -> str | None:
        self.enabled = enabled
        if not enabled and self.muted_by_us:
            return self._unmute("paused by user")
        return None

    def _mute(self) -> str:
        try:
            already = self.muter.get_mute()
        except Exception:
            already = None
        if already is True:
            log.info("Ad detected, but the TV is already muted; leaving it alone")
            self.ad_handled = True
            return "already_muted"
        try:
            self.muter.set_mute(True)
        except Exception as e:
            log.error("Could not mute the TV: %s", e)
            return "error"
        log.info("Ad detected -> muted")
        self.muted_by_us = True
        self.muted_at = self.clock()
        self.ad_handled = True
        return "muted"

    def _unmute(self, reason: str) -> str:
        try:
            self.muter.set_mute(False)
        except Exception as e:
            log.error("Could not unmute the TV: %s", e)
            return "error"
        log.info("Unmuted (%s)", reason)
        self.muted_by_us = False
        return "unmuted"
