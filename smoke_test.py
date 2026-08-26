#!/usr/bin/env python
"""Offline end-to-end test. Touches no browser, no network, no live LinkedIn.

Runs the whole pipeline against fake data with a stubbed LLM:

    fake posts + people  ->  score  ->  write (guarded)  ->  limits gate
                                                          ->  circuit breaker

Safe to run any time, including on the VPS. Exits non-zero on any failure.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time

# MUST happen before importing la.* -- config resolves paths at import time.
_TMP = tempfile.mkdtemp(prefix="la-smoke-")
os.environ["LA_DATA"] = _TMP
os.environ.setdefault("DASHBOARD_PASSWORD", "test")

from la import config, db, discover, limits, llm, niche, score, session, write  # noqa: E402

PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = ""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'ok  ' if cond else 'FAIL'}  {name}" + (f"  -- {detail}" if detail and not cond else ""))


# --------------------------------------------------------------------------
# stub the LLM so nothing leaves the machine
# --------------------------------------------------------------------------

FAKE_POST = """Most local businesses lose map pack visibility for a boring reason: the primary
category is wrong.

Open your Google Business Profile, go to Edit profile, then Business category. The primary
category carries far more weight than the additional ones. A roofer listed primarily as
Contractor will lose to a roofer listed primarily as Roofing contractor every time.

Change it, then leave it alone for 60 days before judging the result.

#localseo #googlebusinessprofile #homeservices"""

FAKE_COMMENT = ("The category field is the one most people get wrong here. Primary category "
                "outweighs the secondary ones by a long way, and switching it usually takes "
                "about six weeks to settle.")

FAKE_NOTE = ("You run a roofing company in Leeds, so you will have seen the map pack change "
             "this year. I work on local search for trades businesses. Happy to be connected.")

BAD_POST = ("In today's digital landscape, local SEO is a game changer that will "
            "supercharge your growth by 340% in 30 days. Thoughts?")


def install_stub(responses):
    """Replace llm.complete with a canned sequence, so no CLI is spawned."""
    seq = list(responses)

    def fake(cfg, system, user, model=None, max_tokens=900, temperature=0.8):
        return seq.pop(0) if seq else FAKE_COMMENT
    llm.complete = fake


# --------------------------------------------------------------------------

def main() -> int:
    cfg = config.load()
    cfg["me"] = {"name": "Test User", "first_name": "Test",
                 "headline": "Local SEO for service businesses", "location": "Dubai",
                 "services": ["local seo", "google business profile"]}

    print("== database ==")
    db.init()
    check("init creates the schema", config.DB_PATH.exists())
    check("warmup_start is stamped on first init", bool(db.get_state("warmup_start")))

    # ---------------------------------------------------------------- parsing
    print("\n== parsing helpers ==")
    check("age '3h' -> 3.0", discover.parse_age_hours("3h •") == 3.0)
    check("age '45 minutes ago' -> 0.75", abs(discover.parse_age_hours("45 minutes ago") - 0.75) < 0.01)
    check("age '2d' -> 48", discover.parse_age_hours("2d") == 48.0)
    check("unparseable age is treated as ancient", discover.parse_age_hours("just now!!") == 999.0)
    check("count '1,234 reactions' -> 1234", discover.parse_count("1,234 reactions") == 1234)
    check("count '2K' -> 2000", discover.parse_count("2K reactions") == 2000)
    check("public id from url",
          discover.public_id_from_url("https://www.linkedin.com/in/jane-doe-123/?x=1") == "jane-doe-123")

    # ---------------------------------------------------------------- targeting
    print("\n== targeting ==")
    good, gdetail = niche.score_prospect(
        cfg, headline="Owner at Precision Roofing", location="Leeds, United Kingdom",
        company="Precision Roofing")
    check("real owner in a served trade scores well", good >= 45, f"got {good} {gdetail}")

    recruiter, rdetail = niche.score_prospect(
        cfg, headline="Technical Recruiter | Hiring Engineers", location="Austin, Texas")
    check("recruiter is excluded", recruiter == 0 and "excluded" in rdetail)

    agency, adetail = niche.score_prospect(
        cfg, headline="SEO Consultant helping brands rank", location="London")
    check("competing SEO consultant is excluded", agency == 0 and "excluded" in adetail)

    far, fdetail = niche.score_prospect(
        cfg, headline="Owner, Sakura Dental Clinic", location="Osaka, Japan",
        company="Sakura Dental")
    check("owner outside served regions scores zero",
          far == 0 and "region" in str(fdetail.get("excluded", "")), f"got {far} {fdetail}")

    check("Dubai maps to the uae bucket", niche.region_of(cfg, "Dubai, United Arab Emirates") == "uae")
    check("Toronto maps to us_ca", niche.region_of(cfg, "Toronto, Ontario, Canada") == "us_ca")
    check("Manchester maps to uk_eu", niche.region_of(cfg, "Manchester, England") == "uk_eu")

    hot, _ = niche.score_post(
        cfg, body="Our plumbing company stopped showing up on Google Maps last week. "
                  "No idea why, phone has gone quiet. Anyone seen this?",
        author_headline="Owner at Vance Plumbing", age_hours=3, reactions=6, comments=4)
    cold, _ = niche.score_post(
        cfg, body="Delighted to share that I have completed a course on leadership synergy.",
        author_headline="Aspiring Leader", age_hours=3, reactions=6, comments=4)
    check("a real problem post outscores filler", hot > cold + 20, f"{hot} vs {cold}")

    stale, _ = niche.score_post(
        cfg, body="Our plumbing company stopped showing up on Google Maps last week.",
        author_headline="Owner at Vance Plumbing", age_hours=96, reactions=6, comments=4)
    check("recency damps an old post", stale < hot, f"{stale} vs {hot}")

    # ---------------------------------------------------------------- scoring pass
    print("\n== scoring pass ==")
    db.upsert_feed_post({
        "id": "urn:li:activity:1", "url": "https://x/1",
        "author_name": "Dave Vance", "author_public_id": "dave-vance",
        "author_headline": "Owner at Vance Plumbing",
        "body": "We stopped showing up on Google Maps last week and the phone has gone "
                "quiet. Our google business profile looks fine to me. Any ideas?",
        "reactions": 8, "comment_count": 3, "age_hours": 4, "source": "feed"})
    db.upsert_feed_post({
        "id": "urn:li:activity:2", "url": "https://x/2",
        "author_name": "Nobody", "author_public_id": "nobody",
        "author_headline": "Aspiring Leader | Open to work",
        "body": "Grateful to announce I finished another leadership course this week, "
                "so much to learn from the journey and the people around me.",
        "reactions": 2, "comment_count": 0, "age_hours": 5, "source": "feed"})
    db.upsert_prospect({
        "public_id": "dave-vance", "url": "https://x/in/dave-vance", "name": "Dave Vance",
        "headline": "Owner at Vance Plumbing", "location": "Dallas, Texas",
        "company": "Vance Plumbing", "degree": "2nd", "source": "search:test"})
    db.upsert_prospect({
        "public_id": "rec-ruiter", "url": "https://x/in/rec-ruiter", "name": "R Ruiter",
        "headline": "Technical Recruiter | Hiring", "location": "Austin, Texas",
        "degree": "2nd", "source": "search:test"})

    out = score.run(cfg)
    check("the good post queued", out["posts"]["queued"] == 1, str(out["posts"]))
    check("the filler post was dropped", out["posts"]["skipped"] == 1, str(out["posts"]))
    check("the owner queued", out["prospects"]["queued"] == 1, str(out["prospects"]))
    check("the recruiter was dropped", out["prospects"]["skipped"] == 1, str(out["prospects"]))

    # ---------------------------------------------------------------- guards
    print("\n== writing guards ==")
    check("banned phrases are detected", len(write.banned_hits(cfg, BAD_POST)) >= 3)
    check("a fabricated percentage is caught",
          "340%" in " ".join(write.unverified_numbers(cfg, BAD_POST)))
    # config.yaml ships with placeholder proof lines that carry no real digits,
    # so this check supplies its own verified figure rather than depending on
    # whatever numbers the user happens to have put in their proof block.
    cfg_with_proof = dict(cfg)
    cfg_with_proof["proof"] = ["Example Client: 4,930 clicks over 6 months."]
    check("a verified figure passes",
          write.unverified_numbers(cfg_with_proof, "We saw 4,930 clicks over six months.") == [])
    check("small prose numbers are allowed",
          write.unverified_numbers(cfg, "Three things to check in the next 10 minutes.") == [])
    check("em-dashes are stripped", "—" not in write.clean("a — b"))
    check("bold markdown is stripped", write.clean("**hi** there") == "hi there")
    check("wrapping quotes are stripped", write.clean('"hello"') == "hello")

    install_stub([FAKE_POST])
    body, engine, pillar, problems = write.post(cfg)
    check("a clean post passes the guards", problems == [], str(problems))
    check("hashtags survive onto the post", "#localseo" in body)
    check("a pillar was chosen", bool(pillar.get("name")))

    # A post that keeps failing must still come back flagged, never silently sent.
    install_stub([BAD_POST, BAD_POST, BAD_POST])
    _, _, _, problems = write.post(cfg)
    check("a post that never cleans up is reported as failing", problems != [])

    install_stub([FAKE_COMMENT])
    row = db.feed_posts_by_status("queued", 1)[0]
    ctext, _, cproblems = write.comment(cfg, row)
    check("comment generation is clean", cproblems == [] and len(ctext) > 30)

    install_stub([FAKE_NOTE])
    prow = dict(db.prospects_by_status("queued", 1)[0])
    ntext, _, nproblems = write.note(cfg, prow)
    check("note is within LinkedIn's character ceiling", len(ntext) <= 300, f"{len(ntext)} chars")
    check("note generation is clean", nproblems == [])

    install_stub(["x" * 900])
    long_note, _, _ = write.note(cfg, prow)
    check("an over-long note is trimmed, not sent",
          len(long_note) <= int(cfg.get_path("content.note_max_chars", 280)))

    # ---------------------------------------------------------------- rails
    print("\n== rails ==")
    cfg["limits"]["active_hours"] = [0, 24]      # take the clock out of the test
    cfg["limits"]["warmup_days"] = 0             # full caps
    cfg["limits"]["min_minutes_between"]["comment"] = 0
    cfg["limits"]["min_minutes_between_any"] = 0

    ok, why = limits.check(cfg, "comment")
    check("commenting is allowed on a clean slate", ok, why)

    cap = limits.cap_for(cfg, "comment")
    for i in range(cap):
        db.record_action("comment", ok=True, target_id=f"t{i}")
    ok, why = limits.check(cfg, "comment")
    check("the daily comment cap blocks further comments", not ok and "daily cap" in why, why)

    cfg["limits"]["active_hours"] = [0, 24]
    cfg["limits"]["connects_per_week"] = 2
    for i in range(2):
        db.record_action("connect", ok=True, target_id=f"c{i}")
    ok, why = limits.check(cfg, "connect")
    check("the weekly invite budget blocks invites",
          not ok and ("weekly" in why or "daily cap" in why), why)

    cfg["limits"]["active_hours"] = [23, 24] if time.localtime().tm_hour < 23 else [0, 1]
    ok, why = limits.check(cfg, "post")
    check("quiet hours block everything", not ok and "active hours" in why, why)
    cfg["limits"]["active_hours"] = [0, 24]

    # spacing
    cfg["limits"]["min_minutes_between"]["post"] = 600
    db.record_action("post", ok=True)
    ok, why = limits.check(cfg, "post")
    check("spacing blocks a second post too soon", not ok, why)

    # consecutive failures
    for i in range(3):
        db.record_action("react", ok=False, error="selector missing")
    check("consecutive failures are counted", limits.consecutive_failures() >= 3)
    ok, why = limits.check(cfg, "react")
    check("repeated failures stop the agent clicking",
          not ok and "consecutive failures" in why, why)
    db.record_action("react", ok=True)
    check("one success resets the failure counter", limits.consecutive_failures() == 0)

    # ---------------------------------------------------------------- breaker
    print("\n== circuit breaker ==")
    db.pause("challenge: test")
    ok, why = limits.check(cfg, "comment")
    check("a pause blocks every action", not ok and "paused" in why, why)
    db.resume()
    paused, _ = db.is_paused()
    check("resume clears the pause", not paused)

    session.back_off("connect", 5.0, "weekly invite limit")
    blocked, why = session.blocked_until("connect")
    check("a back-off registers for that kind", blocked, why)
    ok, why2 = limits.check(cfg, "connect")
    check("a back-off blocks that kind only", not ok, why2)
    session.clear_backoff("connect")
    check("clearing the back-off works", not session.blocked_until("connect")[0])

    # Page-state classification, the thing that decides whether to pause at all.
    class FakeSess:
        def __init__(self, url, text):
            self._u, self._t = url, text
            self.page = self
        @property
        def url(self): return self._u
        def inner_text(self, _sel): return self._t
        def shot(self, _n): return ""

    check("a normal feed reads as healthy",
          session.probe(FakeSess("https://www.linkedin.com/feed/", "Start a post")).state == "ok")
    check("a checkpoint URL reads as a challenge",
          session.probe(FakeSess("https://www.linkedin.com/checkpoint/challenge/x", "")).state
          == "challenge")
    check("a restriction notice reads as restricted",
          session.probe(FakeSess("https://www.linkedin.com/feed/",
                                 "Your account has been restricted")).state == "restricted")
    check("a login wall reads as logged out",
          session.probe(FakeSess("https://www.linkedin.com/feed/",
                                 "Sign in to LinkedIn")).state == "logged_out")
    check("an invite ceiling reads as rate limited",
          session.probe(FakeSess("https://www.linkedin.com/feed/",
                                 "You've reached the weekly invitation limit")).state
          == "rate_limited")

    # ---------------------------------------------------------------- stats
    print("\n== stats ==")
    s = db.stats(limits.DAY)
    check("stats count today's comments", s["comments"] >= cap)
    check("stats count failures separately", s["failures"] >= 3)
    check("pipeline counts render", "prospects" in db.pipeline_counts())

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("failed:")
        for f in FAIL:
            print("  -", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    try:
        code = main()
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)
    sys.exit(code)
