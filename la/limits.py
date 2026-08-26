"""Safety rails. These are the difference between an aged account and a banned one.

Every rule here is about staying inside normal human behaviour, not about hiding
from detection. If a rule blocks an action the answer is to wait, not to raise
the cap. Raising the cap is how this stops working.

LinkedIn's own published ceiling that actually matters is the weekly invitation
limit (roughly 100 for a free account, enforced hard). Everything else is
undocumented and enforced by behavioural scoring, which is why the defaults here
are far below what "works" in a scraper forum post.

Three rails no amount of config can switch off:
  * the pause flag  -- set by the circuit breaker, checked before every action
  * the warm-up ramp -- an account that goes from 0 to 12 invites a day is a flag
  * consecutive failures -- if the UI stopped matching, stop clicking
"""
from __future__ import annotations

import time
from datetime import datetime
from zoneinfo import ZoneInfo

from . import db

DAY = 86400.0
WEEK = 7 * DAY

# Cap key per action kind, and the sane default if config omits it.
_CAPS = {
    "post": ("posts_per_day", 1),
    "comment": ("comments_per_day", 12),
    "connect": ("connects_per_day", 12),
    "react": ("reacts_per_day", 25),
    "follow": ("follows_per_day", 5),
}

# Minimum minutes between two actions of the same kind.
_GAPS = {
    "post": 240.0,
    "comment": 7.0,
    "connect": 5.0,
    "react": 3.0,
    "follow": 10.0,
}


def _tz(cfg) -> ZoneInfo:
    name = cfg.get_path("limits.timezone", "Asia/Dubai")
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo("UTC")


def now_local(cfg) -> datetime:
    return datetime.now(_tz(cfg))


def warmup_factor(cfg) -> float:
    """Ramp from 25% to 100% of every cap over `warmup_days`.

    The account itself is old and real, but *this pattern of activity* is new,
    and a step change in behaviour is exactly what behavioural scoring looks
    for. Ramp in.
    """
    days_total = float(cfg.get_path("limits.warmup_days", 14) or 0)
    if days_total <= 0:
        return 1.0
    started = float(db.get_state("warmup_start") or time.time())
    elapsed_days = max(0.0, (time.time() - started) / DAY)
    return min(1.0, 0.25 + 0.75 * (elapsed_days / days_total))


def cap_for(cfg, kind: str) -> int:
    """Effective daily cap after warm-up and weekend damping."""
    key, default = _CAPS.get(kind, ("comments_per_day", 4))
    raw = float(cfg.get_path(f"limits.{key}", default) or 0)
    factor = warmup_factor(cfg)

    if now_local(cfg).weekday() >= 5:  # Sat/Sun: business owners aren't reading
        factor *= float(cfg.get_path("limits.weekend_factor", 0.4) or 1.0)

    scaled = raw * factor
    # Never round a non-zero allowance down to zero, or warm-up week 1 does nothing.
    return max(1, int(scaled)) if raw >= 1 else 0


def gap_minutes(cfg, kind: str) -> float:
    """Configured minimum minutes between two actions of this kind."""
    return float(cfg.get_path(f"limits.min_minutes_between.{kind}", _GAPS.get(kind, 7.0)))


def in_active_hours(cfg) -> tuple[bool, str]:
    lo, hi = (cfg.get_path("limits.active_hours", [8, 20]) or [8, 20])[:2]
    hour = now_local(cfg).hour
    if lo <= hour < hi:
        return True, ""
    return False, f"outside active hours ({lo:02d}:00-{hi:02d}:00 {cfg.get_path('limits.timezone', 'Asia/Dubai')}, now {hour:02d}:00)"


def check(cfg, kind: str) -> tuple[bool, str]:
    """The single gate. Every action in act.py calls this first.

    Returns (allowed, reason_if_blocked).
    """
    # --- circuit breaker --------------------------------------------------
    paused, reason = db.is_paused()
    if paused:
        return False, f"paused: {reason}"

    # --- the UI stopped agreeing with us ---------------------------------
    max_fail = int(cfg.get_path("limits.max_consecutive_failures", 3) or 3)
    if consecutive_failures() >= max_fail:
        return False, f"{max_fail} consecutive failures, refusing to keep clicking"

    # --- LinkedIn told us to slow down on this specific thing -------------
    from . import session  # local import: session imports nothing from limits
    blocked, why = session.blocked_until(kind)
    if blocked:
        return False, why

    # --- quiet hours ------------------------------------------------------
    ok, why = in_active_hours(cfg)
    if not ok:
        return False, why

    # --- daily cap --------------------------------------------------------
    cap = cap_for(cfg, kind)
    done = db.count_since(DAY, kind)
    if done >= cap:
        return False, f"daily cap reached for {kind} ({done}/{cap})"

    # --- weekly invitation budget (the one LinkedIn enforces itself) ------
    if kind == "connect":
        wk_cap = int(cfg.get_path("limits.connects_per_week", 80) or 80)
        wk_done = db.count_since(WEEK, "connect")
        if wk_done >= wk_cap:
            return False, f"weekly invite budget spent ({wk_done}/{wk_cap})"

    # --- spacing between actions of this kind ----------------------------
    gap_min = gap_minutes(cfg, kind)
    last = db.last_action_time(kind)
    if last:
        waited = (time.time() - last) / 60.0
        if waited < gap_min:
            return False, (f"too soon for another {kind}, "
                           f"{gap_min - waited:.0f} min left of the {gap_min:.0f} min gap")

    # --- global spacing, so kinds don't interleave into a burst ----------
    any_gap = float(cfg.get_path("limits.min_minutes_between_any", 2) or 0)
    last_any = db.last_action_time()
    if any_gap and last_any:
        waited = (time.time() - last_any) / 60.0
        if waited < any_gap:
            return False, f"{any_gap - waited:.1f} min left of the global {any_gap:.0f} min gap"

    return True, ""


def consecutive_failures() -> int:
    """How many actions in a row failed. Reset by the next success."""
    n = 0
    for row in db.recent_actions(20):
        if row["ok"]:
            break
        n += 1
    return n


def budget(cfg) -> dict[str, dict]:
    """Remaining allowance per kind. Drives the dashboard, and `run.py status`."""
    out = {}
    for kind in _CAPS:
        cap = cap_for(cfg, kind)
        done = db.count_since(DAY, kind)
        allowed, reason = check(cfg, kind)
        out[kind] = {
            "done": done,
            "cap": cap,
            "left": max(0, cap - done),
            "allowed_now": allowed,
            "blocked_by": reason,
        }
    out["_meta"] = {
        "warmup_factor": round(warmup_factor(cfg), 2),
        "connects_this_week": db.count_since(WEEK, "connect"),
        "connects_per_week": int(cfg.get_path("limits.connects_per_week", 80) or 80),
        "local_time": now_local(cfg).strftime("%Y-%m-%d %H:%M %Z"),
        "in_active_hours": in_active_hours(cfg)[0],
        "consecutive_failures": consecutive_failures(),
    }
    return out
