"""Decides when to mute and unmute.

Rules, in plain words:
* Mute after the ad badge shows up `mute_after` checks in a row, or on the
  first check when the full badge was read ("Ad 1 of 3", "1 of 3 :20").
* Unmute after it is gone `unmute_after` checks in a row *and* for at least
  `unmute_after_seconds`. OCR misreads the badge now and then, and on a slow
  Pi three misses in a row can take only a few seconds; the time rule keeps
  one blurry stretch from unmuting in the middle of an ad. When the badge
  said more ads are coming ("1 of 3"), wait `unmute_between_ads_seconds`
  instead, since apps show a few seconds with no badge between ads. When it
  showed a countdown (":07"), never unmute before that runs out.
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
        unmute_after_seconds: float = 3,
        unmute_between_ads_seconds: float = 12,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.muter = muter
        self.mute_after = max(1, int(mute_after))
        self.unmute_after = max(1, int(unmute_after))
        self.max_mute_seconds = float(max_mute_seconds)
        self.unmute_after_seconds = float(unmute_after_seconds)
        self.unmute_between_ads_seconds = float(unmute_between_ads_seconds)
        self.clock = clock

        self.enabled = True
        self.muted_by_us = False
        self.muted_at = 0.0
        self.ad_streak = 0
        self.clear_streak = 0
        self.last_ad_at = float("-inf")
        self.more_ads_coming = False  # last badge read "1 of 3" or "2 of 3"
        self.countdown_ends = float("-inf")  # when the last read countdown hits 0
        self.ad_handled = False  # this ad break was already dealt with
        self.suppressed = False  # safety timeout tripped; wait for a clear screen

    def update(
        self,
        is_ad: bool,
        strong: bool = False,
        position: tuple[int, int] | None = None,
        remaining: int | None = None,
    ) -> str | None:
        """Feed one detection result. Returns an event name when something happened.

        strong: the full badge was read, so one check is enough to mute.
        position: (ad number, ads in this break), e.g. (1, 3).
        remaining: countdown seconds left in this ad.
        """
        now = self.clock()
        if is_ad:
            self.ad_streak += 1
            self.clear_streak = 0
            self.last_ad_at = now
            if position:
                self.more_ads_coming = position[0] < position[1]
            if remaining is not None:
                self.countdown_ends = now + remaining
        else:
            self.clear_streak += 1
            self.ad_streak = 0

        quiet = self.unmute_between_ads_seconds if self.more_ads_coming else self.unmute_after_seconds
        clear = (
            self.clear_streak >= self.unmute_after
            and now - self.last_ad_at >= quiet
            and now >= self.countdown_ends
        )
        if clear:
            self.ad_handled = False
            self.suppressed = False
            self.more_ads_coming = False
            self.countdown_ends = float("-inf")

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
            and (self.ad_streak >= self.mute_after or (strong and self.ad_streak >= 1))
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
