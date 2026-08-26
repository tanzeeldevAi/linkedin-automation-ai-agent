"""Playwright wrapper: one long-lived, persistent Chrome profile.

Design notes worth knowing before you change anything here:

* ONE persistent profile, reused forever. you log in once by hand through
  the noVNC view on the dashboard; the cookies live in the mounted volume and
  survive restarts and redeploys. The agent never handles his password.

* The browser is NOT relaunched per action. Launching Chrome, hitting
  linkedin.com cold and logging in again on every comment is both slow and the
  single most suspicious pattern available. The worker opens one session, does
  its cycle at human pace, and closes it.

* Headful on a virtual display (Xvfb), never headless. Headless Chrome is
  trivially detectable and LinkedIn does detect it.

* One concession to being automated at all: we drop the
  `AutomationControlled` blink feature, which is what sets
  `navigator.webdriver = true`. Without it the session is flagged on the first
  page load and nothing else in this project matters. That is the extent of it
  -- no proxy rotation, no canvas/font spoofing, no UA randomisation, no
  throwaway profiles. The account is a real account behaving like a slow human.
"""
from __future__ import annotations

import os
import random
import time
from contextlib import contextmanager
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

from .config import PROFILE_DIR, SHOT_DIR, ensure_dirs

# Match a real desktop Chrome on Windows. Kept stable on purpose: a user agent
# that changes between sessions is worse than one that is slightly stale.
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

LAUNCH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--no-sandbox",                    # required in the container
    "--disable-dev-shm-usage",         # /dev/shm is small in Docker; avoids crashes
    "--disable-gpu",
    "--window-size=1440,900",
    "--start-maximized",
]


def human_pause(lo: float = 1.2, hi: float = 3.4) -> None:
    """Real reading time. Not an evasion trick -- the agent genuinely should not
    fire requests faster than a person could read the page."""
    time.sleep(random.uniform(lo, hi))


def long_pause(lo: float = 20.0, hi: float = 90.0) -> None:
    """Between two real actions. Humans get distracted."""
    time.sleep(random.uniform(lo, hi))


class Session:
    """A live browser tab plus the small set of behaviours we need from it."""

    def __init__(self, ctx, page: Page):
        self.ctx = ctx
        self.page = page

    # -- navigation -------------------------------------------------------
    def goto(self, url: str, settle: float = 3.0, timeout: int = 60000) -> bool:
        """Navigate and let client-side rendering settle. Returns False rather
        than raising, so one dead URL cannot kill a whole cycle."""
        try:
            self.page.goto(url, wait_until="domcontentloaded", timeout=timeout)
            self.page.wait_for_timeout(int(settle * 1000))
            return True
        except Exception:
            return False

    def scroll_like_reading(self, steps: int = 4) -> None:
        """Scroll down in uneven increments with pauses, the way someone
        skimming a feed does. Also what makes lazy-loaded posts appear."""
        for _ in range(steps):
            self.page.mouse.wheel(0, random.randint(320, 900))
            time.sleep(random.uniform(0.7, 2.3))

    def dwell(self, seconds: float | None = None) -> None:
        """Sit on a post long enough to have plausibly read it before replying."""
        time.sleep(seconds if seconds is not None else random.uniform(6.0, 18.0))

    # -- input ------------------------------------------------------------
    def type_human(self, locator, text: str, click_first: bool = True) -> None:
        """Type at a human cadence, with slight hesitation at sentence ends.

        LinkedIn's editors are contenteditable and reject `fill()`; they need
        real key events anyway for the Post button to enable.
        """
        if click_first:
            locator.click()
            human_pause(0.4, 1.1)
        for chunk in text.split("\n"):
            for ch in chunk:
                locator.type(ch, delay=random.uniform(18, 85))
                if ch in ".!?" and random.random() < 0.5:
                    time.sleep(random.uniform(0.2, 0.7))
            # Shift+Enter keeps a soft line break instead of submitting.
            locator.press("Shift+Enter")
            time.sleep(random.uniform(0.15, 0.5))

    # -- diagnostics ------------------------------------------------------
    def shot(self, name: str) -> str:
        """Screenshot for the dashboard. Called on every failure so a broken
        selector is visible instead of just being a row with ok=0."""
        ensure_dirs()
        safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in name)[:60]
        path = SHOT_DIR / f"{int(time.time())}-{safe}.png"
        try:
            self.page.screenshot(path=str(path), full_page=False)
            return path.name
        except Exception:
            return ""

    def html(self) -> str:
        try:
            return self.page.content()
        except Exception:
            return ""

    @property
    def url(self) -> str:
        try:
            return self.page.url
        except Exception:
            return ""


@contextmanager
def launch(headless: bool = False, profile_dir: Path | None = None):
    """Launch our own Chrome against the persistent profile.

    Used for local runs on your machine. NOT used in the container -- see
    `attach` and the note in `session`.
    """
    path = Path(profile_dir or PROFILE_DIR)
    path.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            str(path),
            headless=headless,
            args=LAUNCH_ARGS,
            user_agent=UA,
            viewport={"width": 1440, "height": 900},
            locale="en-US",
            timezone_id="Asia/Dubai",
            ignore_default_args=["--enable-automation"],
        )
        ctx.set_default_timeout(30000)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            yield Session(ctx, page)
        finally:
            try:
                ctx.close()
            except Exception:
                pass


def serve_forever(profile_dir: Path | None = None, cdp_port: int = 9222,
                  headless: bool = False) -> None:
    """Hold one Chrome open for the life of the container and never return.

    This is the process the worker attaches to and the process you see
    through noVNC. It retries the launch because supervisor may start it before
    Xvfb has finished coming up.
    """
    path = Path(profile_dir or PROFILE_DIR)
    path.mkdir(parents=True, exist_ok=True)
    args = [*LAUNCH_ARGS, f"--remote-debugging-port={cdp_port}",
            "--remote-allow-origins=*"]

    while True:
        try:
            with sync_playwright() as p:
                ctx = p.chromium.launch_persistent_context(
                    str(path),
                    headless=headless,
                    args=args,
                    user_agent=UA,
                    viewport={"width": 1440, "height": 900},
                    locale="en-US",
                    timezone_id="Asia/Dubai",
                    ignore_default_args=["--enable-automation"],
                )
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                try:
                    page.goto("https://www.linkedin.com/feed/",
                              wait_until="domcontentloaded", timeout=60000)
                except Exception:
                    pass
                print(f"chrome up, CDP on {cdp_port}, profile {path}", flush=True)
                # Block until the browser dies (crash, OOM, manual quit).
                while True:
                    try:
                        if not ctx.pages:
                            ctx.new_page()
                        time.sleep(20)
                    except Exception:
                        break
        except Exception as exc:
            print(f"chrome launch failed ({type(exc).__name__}: {exc}), retrying in 10s",
                  flush=True)
        time.sleep(10)


@contextmanager
def attach(cdp_url: str, wait_seconds: float = 90.0):
    """Attach to a Chrome that is already running, over CDP.

    This is how the container works, and it matters for two reasons:

      * A persistent profile directory can only be held by ONE Chrome process.
        If the worker launched its own, the dashboard could never open one, and
        two workers would corrupt the profile.

      * you log in through the noVNC view. That needs a Chrome window that
        exists between work blocks, not one that appears for 40 minutes an hour.

    So supervisor keeps one Chrome alive for the life of the container and every
    consumer attaches to it. Closing here detaches and deliberately leaves that
    Chrome running.
    """
    with sync_playwright() as p:
        # The managed Chrome may still be starting; wait rather than crash the
        # whole work block on a cold container.
        deadline = time.time() + wait_seconds
        b = None
        last = None
        while True:
            try:
                b = p.chromium.connect_over_cdp(cdp_url)
                break
            except Exception as exc:
                last = exc
                if time.time() >= deadline:
                    raise RuntimeError(
                        f"no Chrome on {cdp_url} after {wait_seconds:.0f}s: {last}") from last
                time.sleep(3)

        ctx = b.contexts[0] if b.contexts else b.new_context()
        ctx.set_default_timeout(30000)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            yield Session(ctx, page)
        finally:
            try:
                b.close()      # detaches only; does not kill the Chrome process
            except Exception:
                pass


@contextmanager
def session(headless: bool = False, profile_dir: Path | None = None,
            cdp: str | None = None):
    """Get a Session, attaching if a Chrome is being managed for us.

    Set LA_CDP (the container does) to attach to the long-lived Chrome.
    Leave it unset to launch a private one, which is what you want locally.
    """
    cdp = cdp or os.environ.get("LA_CDP") or ""
    if cdp:
        with attach(cdp) as sess:
            yield sess
    else:
        with launch(headless=headless, profile_dir=profile_dir) as sess:
            yield sess
