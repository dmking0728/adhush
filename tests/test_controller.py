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
