"""The autopilot loop.

Pacing model: budget burn, not a timetable.

A fixed schedule ("comment at 09:15, 10:30, 11:45") is both fragile and
obviously mechanical -- it produces identical timing every day and it collapses
the moment a pause or a failure shifts everything. Instead, each tick asks:

    for each action kind, how much of today's allowance is left, and how much of
    today's active window is left?

and picks probabilistically, weighted by that pressure. The result spreads work
across the day on its own, recovers automatically after a pause, never bursts to
catch up, and looks different every day without any randomness theatre.

Work happens in blocks: open one browser, do a handful of things over ~40
minutes at human pace, close it, go quiet for a while. That shape -- bursts of
attention separated by gaps -- is what a person's activity actually looks like.
"""
from __future__ import annotations

import datetime
import os
import random
import time

from . import act, browser, db, discover, limits, profile, score, session

KINDS = ("post", "comment", "connect", "react")


# --------------------------------------------------------------------------
# timing
# --------------------------------------------------------------------------

def seconds_until_active(cfg) -> float:
    """0 if we are inside the active window, else seconds until it opens.

    The random minute offset means the agent does not start at exactly 08:00
    every single morning.
    """
    ok, _ = limits.in_active_hours(cfg)
    if ok:
        return 0.0
    lo = int((cfg.get_path("limits.active_hours", [8, 21]) or [8, 21])[0])
    now = limits.now_local(cfg)
    target = now.replace(hour=lo, minute=random.randint(0, 25), second=0, microsecond=0)
    if target <= now:
        target += datetime.timedelta(days=1)
    return max(60.0, (target - now).total_seconds())


def active_minutes_left(cfg) -> float:
    lo, hi = (cfg.get_path("limits.active_hours", [8, 21]) or [8, 21])[:2]
    now = limits.now_local(cfg)
    if now.hour >= int(hi):
        return 0.0
    end = now.replace(hour=int(hi), minute=0, second=0, microsecond=0)
    return max(0.0, (end - now).total_seconds() / 60.0)


def post_is_due(cfg) -> bool:
    """Keep the daily post inside its preferred morning window, with a
    late-afternoon catch-up so a quiet morning does not skip the day."""
    if db.count_since(limits.DAY, "post") >= limits.cap_for(cfg, "post"):
        return False
    lo, hi = (cfg.get_path("content.post_window", [9, 12]) or [9, 12])[:2]
    catch = int(cfg.get_path("content.post_catchup_after", 15) or 15)
    hour = limits.now_local(cfg).hour
    return (int(lo) <= hour < int(hi)) or hour >= catch


# --------------------------------------------------------------------------
# what to do next
# --------------------------------------------------------------------------

def pressure(cfg) -> dict[str, float]:
    """How badly each kind needs to fire right now.

    remaining allowance / remaining opportunities. A kind with 8 comments left
    and 40 minutes of window left is under far more pressure than one with 2
    left and 6 hours to go.
    """
    mins_left = max(5.0, active_minutes_left(cfg))
    out = {}
    for kind in KINDS:
        allowed, _ = limits.check(cfg, kind)
        if not allowed:
            out[kind] = 0.0
            continue
        if kind == "post" and not post_is_due(cfg):
            out[kind] = 0.0
            continue
        left = max(0, limits.cap_for(cfg, kind) - db.count_since(limits.DAY, kind))
        if left <= 0:
            out[kind] = 0.0
            continue
        slots = max(1.0, mins_left / max(1.0, limits.gap_minutes(cfg, kind)))
        out[kind] = left / slots
    return out


def choose(cfg) -> str | None:
    """Pick the next action kind, weighted by pressure. The daily post always
    wins when it is due -- it is the only action with real reach."""
    p = pressure(cfg)
    if p.get("post", 0) > 0:
        return "post"
    live = {k: v for k, v in p.items() if v > 0}
    if not live:
        return None
    kinds = list(live)
    return random.choices(kinds, weights=[live[k] for k in kinds], k=1)[0]


def queue_is_thin(cfg) -> bool:
    posts = len(db.feed_posts_by_status("queued", limit=6))
    people = len(db.prospects_by_status("queued", limit=6))
    stale = time.time() - float(db.get_state("last_discovery", "0") or 0) > 3 * 3600
    return posts < 3 or people < 3 or stale


# --------------------------------------------------------------------------
# one block of work
# --------------------------------------------------------------------------

def run_block(cfg, sess, block_minutes: float = 40.0) -> dict:
    """Discover if needed, then act at human pace until the block is spent."""
    summary: dict[str, int] = {"post": 0, "comment": 0, "connect": 0, "react": 0,
                               "skipped": 0, "failed": 0}

    h = session.check(sess)
    if not h.ok:
        session.guard(sess, "block start")
        return {**summary, "aborted": h.state}

    if queue_is_thin(cfg):
        discover.run(cfg, sess)
        score.run(cfg)
        browser.human_pause(4, 10)

    # Housekeeping, cheap and only occasionally.
    if random.random() < 0.25:
        act.track_accepts(cfg, sess)
        browser.human_pause(3, 8)
    if random.random() < 0.2:
        act.track_my_posts(cfg, sess)
        browser.human_pause(3, 8)
    # Anything approved on the dashboard since the last block.
    try:
        profile.apply_approved(cfg, sess)
    except Exception as exc:
        db.log("error", "profile", f"applying approvals failed: {type(exc).__name__}: {exc}")

    if profile.due_for_review(cfg):
        try:
            made = profile.review(cfg, sess)
            if made:
                db.log("info", "profile", f"{len(made)} profile suggestion(s) generated")
        except Exception as exc:
            db.log("error", "profile", f"review failed: {type(exc).__name__}: {exc}")

    deadline = time.time() + block_minutes * 60
    while time.time() < deadline:
        paused, reason = db.is_paused()
        if paused:
            return {**summary, "aborted": f"paused: {reason}"}

        kind = choose(cfg)
        if kind is None:
            break

        fn = {"post": act.publish_post, "comment": act.comment_on_next,
              "connect": act.connect_with_next, "react": act.react_to_next}[kind]
        result = fn(cfg, sess)

        if result.get("done"):
            summary[kind] += 1
        elif result.get("error"):
            summary["failed"] += 1
        else:
            summary["skipped"] += 1
            # Nothing in the queue for this kind: try to refill once, then move on.
            if "queue" in (result.get("skipped") or ""):
                discover.run(cfg, sess)
                score.run(cfg)

        # Between two real actions, go away for a while.
        browser.long_pause(45, 210)

    return summary


def run_forever(cfg, block_minutes: float = 40.0) -> None:
    """The worker entrypoint. Never returns.

    LA_KEEP_BROWSER=1 holds one browser open across blocks instead of opening a
    fresh one each time. That is the right default when running on your own
    desktop -- otherwise a Chrome window appears, clicks around and vanishes
    every half hour while he is trying to work. In the container the browser is
    managed separately anyway, so the flag is irrelevant there.
    """
    keep = os.environ.get("LA_KEEP_BROWSER") == "1"
    ctx = None      # the live contextmanager, when we are keeping one
    sess = None
    db.log("info", "worker", f"worker started (keep_browser={keep})")

    def drop_browser():
        nonlocal ctx, sess
        if ctx is not None:
            try:
                ctx.__exit__(None, None, None)
            except Exception:
                pass
        ctx, sess = None, None

    while True:
        try:
            wait = seconds_until_active(cfg)
            if wait > 0:
                db.set_state("worker_status", f"sleeping until active hours ({wait / 60:.0f} min)")
                # Nothing will happen for hours; let the browser go so it is not
                # sitting on an idle LinkedIn tab all night.
                drop_browser()
                time.sleep(min(wait, 1800))
                continue

            paused, reason = db.is_paused()
            if paused:
                db.set_state("worker_status", f"paused: {reason}")
                time.sleep(600)
                continue

            db.set_state("worker_status", "working")
            db.set_state("last_block_start", str(time.time()))

            if keep:
                if sess is None:
                    # Entered by hand rather than with `with`, because this one
                    # has to outlive the loop iteration.
                    ctx = browser.session()
                    sess = ctx.__enter__()
                out = run_block(cfg, sess, block_minutes)
            else:
                with browser.session() as s:
                    out = run_block(cfg, s, block_minutes)

            db.log("info", "worker", f"block done: {out}")

            gap = random.uniform(12, 35) * 60
            db.set_state("worker_status", f"idle for {gap / 60:.0f} min")
            time.sleep(gap)

        except KeyboardInterrupt:
            db.log("info", "worker", "worker stopped by hand")
            drop_browser()
            return
        except Exception as exc:
            db.log("error", "worker", f"block crashed: {type(exc).__name__}: {exc}")
            db.set_state("worker_status", f"recovering from {type(exc).__name__}")
            # A crashed block may have left the browser in a bad state.
            drop_browser()
            time.sleep(300)
