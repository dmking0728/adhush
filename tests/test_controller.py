from adhush.controller import MuteController
from adhush.muter import DryRunMuter


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def make(**kw):
    kw.setdefault("unmute_after_seconds", 0)  # most tests count checks only
    muter, clock = DryRunMuter(), Clock()
    ctl = MuteController(muter, mute_after=2, unmute_after=3, max_mute_seconds=100, clock=clock, **kw)
    return ctl, muter, clock


def feed(ctl, pattern):
    return [ctl.update(c == "A") for c in pattern]


def test_mutes_after_streak_and_unmutes_when_clear():
    ctl, muter, _ = make()
    events = feed(ctl, "AA")
    assert events == [None, "muted"] and muter.muted
    events = feed(ctl, "..")
    assert muter.muted  # not yet
    assert feed(ctl, ".") == ["unmuted"] and not muter.muted


def test_single_false_positive_is_ignored():
    ctl, muter, _ = make()
    feed(ctl, "A.A.A.")
    assert not muter.muted


def test_brief_dropout_during_ad_keeps_mute():
    ctl, muter, _ = make()
    feed(ctl, "AA..A..A")
    assert muter.muted


def test_leaves_user_mute_alone():
    ctl, muter, _ = make()
    muter.muted = True  # user muted before the ad
    assert feed(ctl, "AAAA") == [None, "already_muted", None, None]
    feed(ctl, "...")
    assert muter.muted  # still muted: we didn't do it, we don't undo it


def test_safety_timeout_unmutes_and_stands_down():
    ctl, muter, clock = make()
    feed(ctl, "AA")
    clock.t = 101
    assert feed(ctl, "A") == ["unmuted"]
    assert ctl.suppressed and not muter.muted
    feed(ctl, "AAAA")
    assert not muter.muted  # still standing down
    feed(ctl, "...AA")
    assert muter.muted  # clear screen reset it; next ad mutes again


def test_pause_unmutes_and_blocks():
    ctl, muter, _ = make()
    feed(ctl, "AA")
    assert ctl.set_enabled(False) == "unmuted"
    feed(ctl, "AAA")
    assert not muter.muted


def test_retries_after_mute_error():
    class Flaky(DryRunMuter):
        fails = 1

        def set_mute(self, muted):
            if self.fails:
                self.fails -= 1
                raise OSError("tv asleep")
            super().set_mute(muted)

    muter = Flaky()
    ctl = MuteController(muter, mute_after=2, unmute_after=3)
    assert [ctl.update(True) for _ in range(3)] == [None, "error", "muted"]


def test_misreads_during_ad_do_not_unmute_before_quiet_time():
    """Real Max ad: OCR missed the badge 3 checks in a row (~5 s) mid-ad."""
    ctl, muter, clock = make(unmute_after_seconds=8)

    def check(seen):
        clock.t += 1.7  # Pi Zero check time
        return ctl.update(seen)

    assert [check(True), check(True)] == [None, "muted"]
    assert [check(False) for _ in range(3)] == [None, None, None]  # 5.1 s of misses
    assert muter.muted
    check(True)  # badge read again
    events = [check(False) for _ in range(5)]  # ad really over
    assert events == [None, None, None, None, "unmuted"] and not muter.muted


def test_full_badge_mutes_on_first_check():
    ctl, muter, _ = make()
    assert ctl.update(True, strong=True, position=(1, 3)) == "muted" and muter.muted


def test_bare_ad_still_needs_two_checks():
    ctl, muter, _ = make()
    assert ctl.update(True) is None and not muter.muted


def test_real_max_break_from_log():
    """Replays the 13:37 Max ad break: times and what each check matched.

    It should mute on the first full badge, ride out the gaps between ads and
    the misreads, and unmute ~3 s after the last ad's badge goes away.
    """
    ctl, muter, clock = make(unmute_after_seconds=3, unmute_between_ads_seconds=12)
    A = lambda pos, rem=None: (True, True, pos, rem)  # noqa: E731
    miss = (False, False, None, None)
    timeline = [
        (15.6, A((1, 3), 24)),
        (16.9, A((1, 3), 22)),
        (19.3, A((1, 3), 20)),
        (20.7, A((1, 3), 18)),
        (24.4, A((1, 3), 15)),
        (27.0, miss),  # no badge for a moment
        (29.6, A((1, 3), 10)),
        (34.7, miss),
        (37.0, A((1, 3), 2)),
        (38.2, A((1, 3), 1)),
        # gap between ad 1 and ad 2: no badge for several seconds
        (40.0, miss), (42.0, miss), (44.0, miss), (46.0, miss),
        (49.5, A((2, 3), 8)),
        (52.6, A((2, 3), 1)),
        (53.0, miss), (54.7, miss), (55.6, miss),
        (58.6, A((3, 3), 25)),
        (60.8, miss),
        (67.0, miss), (67.2, miss), (68.2, miss), (70.3, miss), (71.3, miss),
        (74.9, A((3, 3), 9)),
        (78.9, A((3, 3), 5)),
    ]
    events = []
    for t, (is_ad, strong, pos, rem) in timeline:
        clock.t = t
        e = ctl.update(is_ad, strong, pos, rem)
        if e:
            events.append((t, e))
    assert events == [(15.6, "muted")]  # stayed muted the whole break
    # Ad ends at ~83.9 (78.9 + 5 s countdown). Checks keep coming every ~1 s.
    t = 79.9
    while not events[-1][1] == "unmuted":
        clock.t = t
        e = ctl.update(False)
        if e:
            events.append((t, e))
        t += 1.0
        assert t < 100
    assert 83.9 <= events[-1][0] <= 85.0, events
