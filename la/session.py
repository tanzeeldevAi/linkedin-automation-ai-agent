"""Session health and the circuit breaker.

The single most dangerous failure mode for an unattended LinkedIn agent is not
"it stopped working" -- it is "LinkedIn showed a security challenge and the
agent kept clicking anyway". That converts a recoverable checkpoint into a
restricted account.

So: before and after every action, `guard()` looks at what is actually on
screen. Anything other than a normal logged-in page stops all activity and
raises an alert. Nothing here ever attempts to answer a challenge, solve a
CAPTCHA, or re-enter credentials -- those need you, through the noVNC view
on the dashboard.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from . import db, selectors as S

# States that stop everything until a human intervenes.
HARD_STATES = {"restricted", "challenge", "logged_out"}

# Per-kind cool-off applied when LinkedIn says "you've hit a limit" rather than
# "you're suspicious". Not a pause: the rest of the agent keeps working.
RATE_LIMIT_BACKOFF_HOURS = {
    "connect": 26.0,   # weekly invite ceiling -- wait a day, then retry slowly
    "search": 26.0,    # commercial-use limit resets monthly, but back off daily
    "default": 6.0,
}


@dataclass
class Health:
    state: str          # ok | logged_out | challenge | restricted | rate_limited | unknown
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.state == "ok"

    @property
    def hard(self) -> bool:
        return self.state in HARD_STATES


def _text(sess) -> str:
    try:
        return (sess.page.inner_text("body") or "").lower()
    except Exception:
        return ""


def probe(sess) -> Health:
    """Classify whatever is currently on screen. Never navigates."""
    url = (sess.url or "").lower()
    body = _text(sess)

    # URL checks first: they are unambiguous and cheap.
    if any(frag in url for frag in S.RESTRICTED_URLS):
        return Health("restricted", f"restricted checkpoint URL: {url}")
    if any(frag in url for frag in S.CHALLENGE_URLS):
        return Health("challenge", f"challenge URL: {url}")
    if any(frag in url for frag in S.LOGGED_OUT_URLS):
        return Health("logged_out", f"login URL: {url}")

    if not body:
        return Health("unknown", "empty page body")

    # Then page text, most severe first.
    for frag in S.RESTRICTED_TEXT:
        if frag in body:
            return Health("restricted", f"page says: {frag!r}")
    for frag in S.CHALLENGE_TEXT:
        if frag in body:
            return Health("challenge", f"page says: {frag!r}")
    for frag in S.LOGGED_OUT_TEXT:
        if frag in body:
            return Health("logged_out", f"page says: {frag!r}")
    for frag in S.RATE_LIMIT_TEXT:
        if frag in body:
            return Health("rate_limited", f"page says: {frag!r}")

    return Health("ok")


def check(sess) -> Health:
    """Navigate to the feed and classify. Use at the start of a cycle."""
    if not sess.goto(S.FEED_URL, settle=4.0):
        return Health("unknown", "could not load the feed at all")
    h = probe(sess)
    db.set_state("session_state", h.state)
    db.set_state("session_checked_at", str(time.time()))
    if h.state == "ok":
        db.set_state("session_detail", "")
    else:
        db.set_state("session_detail", h.detail)
    return h


def guard(sess, where: str = "") -> bool:
    """Probe the current page and trip the breaker if anything is wrong.

    Returns True only when it is safe to carry on. Call this immediately before
    committing an action and immediately after.
    """
    h = probe(sess)
    if h.ok:
        return True

    shot = sess.shot(f"health-{h.state}")
    detail = f"{h.detail}{f' (during {where})' if where else ''}"

    if h.hard:
        db.pause(f"{h.state}: {detail}")
        db.record_action("health", ok=False, error=f"{h.state}: {detail}", screenshot=shot)
        return False

    if h.state == "rate_limited":
        kind = "connect" if "invitation" in h.detail else "default"
        back_off(kind, RATE_LIMIT_BACKOFF_HOURS.get(kind, 6.0), detail)
        db.record_action("health", ok=False, error=f"rate_limited: {detail}", screenshot=shot)
        return False

    # 'unknown' is not proof of trouble -- a slow render looks identical. Log it
    # and let the caller's own selector waits decide.
    db.log("warn", "health", f"unknown page state during {where or 'cycle'}: {detail}")
    return False


# --------------------------------------------------------------------------
# per-kind back-off, honoured by limits.check()
# --------------------------------------------------------------------------

def back_off(kind: str, hours: float, reason: str = "") -> None:
    until = time.time() + hours * 3600
    db.set_state(f"blocked_until:{kind}", str(until))
    db.set_state(f"blocked_reason:{kind}", reason)
    db.log("warn", "backoff", f"{kind} paused for {hours:.0f}h: {reason}")


def blocked_until(kind: str) -> tuple[bool, str]:
    """Is this action kind in a cool-off period?"""
    raw = db.get_state(f"blocked_until:{kind}")
    if not raw:
        return False, ""
    try:
        until = float(raw)
    except ValueError:
        return False, ""
    if time.time() >= until:
        return False, ""
    mins = (until - time.time()) / 60.0
    reason = db.get_state(f"blocked_reason:{kind}", "") or "rate limited"
    if mins > 90:
        return True, f"{kind} in cool-off for another {mins / 60:.1f}h ({reason})"
    return True, f"{kind} in cool-off for another {mins:.0f} min ({reason})"


def clear_backoff(kind: str) -> None:
    db.set_state(f"blocked_until:{kind}", "")
    db.set_state(f"blocked_reason:{kind}", "")


def summary() -> dict:
    """For the dashboard's health panel."""
    paused, reason = db.is_paused()
    checked = db.get_state("session_checked_at")
    blocks = {}
    for kind in ("post", "comment", "connect", "react", "follow", "search"):
        is_blocked, why = blocked_until(kind)
        if is_blocked:
            blocks[kind] = why
    return {
        "paused": paused,
        "pause_reason": reason,
        "session_state": db.get_state("session_state", "unknown"),
        "session_detail": db.get_state("session_detail", ""),
        "checked_ago_minutes": (
            round((time.time() - float(checked)) / 60.0, 1) if checked else None
        ),
        "backoffs": blocks,
    }
