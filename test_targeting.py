#!/usr/bin/env python
"""Does the ICP actually recognise real prospects, and reject real noise?

This is the test to re-run every time `icp:` or `scoring:` in config.yaml is
edited. Everything here is a realistic LinkedIn headline/location pair of the
kind the agent will genuinely meet, including the awkward ones.

A missed prospect is invisible in production -- the agent simply never invites
them and nothing looks wrong. That is why this file exists.

    python test_targeting.py
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile

_TMP = tempfile.mkdtemp(prefix="la-target-")
os.environ["LA_DATA"] = _TMP

from la import config, niche  # noqa: E402

# (headline, location, company, should_queue, note)
PEOPLE = [
    # --- must be found -----------------------------------------------------
    ("Owner at Vance Plumbing", "Dallas, Texas", "Vance Plumbing", True,
     "textbook prospect, US state spelled out"),
    ("Owner", "Dallas, TX", "Vance Plumbing & Heating", True,
     "bare title, two-letter state code, trade only in the company name"),
    ("Founder & Managing Director, Precision Roofing Ltd", "Leeds, United Kingdom",
     "Precision Roofing Ltd", True, "UK owner, full country"),
    ("Practice Owner | Bright Smile Dental", "Greater London Area", "Bright Smile Dental",
     True, "metro area with no country, dental"),
    ("Owner, Elite Auto Repair", "Sharjah, United Arab Emirates", "Elite Auto Repair",
     True, "Gulf owner"),
    ("Managing Director at Gulf Chauffeur Services", "Dubai", "Gulf Chauffeur Services",
     True, "city only, transport vertical"),
    ("Co-Founder at Northside Med Spa", "Toronto, Ontario", "Northside Med Spa", True,
     "Canadian province, med spa"),
    ("Owner/Operator - Summit HVAC", "Phoenix, Arizona", "Summit HVAC", True,
     "slash title, HVAC"),
    ("General Manager at Riverside Family Law", "Manchester, England",
     "Riverside Family Law", True, "GM at a law firm"),
    ("Restaurant Owner", "Miami, Florida", "", True,
     "no company listed, trade is in the title"),

    # --- must be rejected --------------------------------------------------
    ("Technical Recruiter | We're hiring!", "Austin, Texas", "TalentCo", False,
     "recruiter"),
    ("SEO Consultant | I help brands rank #1", "London", "Self-employed", False,
     "direct competitor"),
    ("Digital Marketing Agency Owner", "New York, NY", "GrowthLab", False,
     "agency owner: an owner, but a competitor, not a client"),
    ("Owner at Sakura Dental Clinic", "Osaka, Japan", "Sakura Dental", False,
     "perfect fit in a region we do not serve"),
    ("Student at University of Texas", "Austin, Texas", "", False, "student"),
    ("Open to work | Aspiring Marketing Professional", "Chicago, Illinois", "", False,
     "job seeker"),
    ("Business Coach helping founders scale to 7 figures", "Dubai", "", False, "coach"),
    ("Crypto investor | Web3 builder", "Miami, Florida", "", False, "crypto"),
    ("Senior Software Engineer at Google", "Seattle, Washington", "Google", False,
     "wrong industry entirely, no owner title"),
    ("Virtual Assistant | Lead Generation Expert", "Manila, Philippines", "", False,
     "excluded role and unserved region"),
]

# (body, author_headline, age_hours, reactions, comments, should_queue, note)
POSTS = [
    ("We stopped showing up on Google Maps last week and the phone has gone quiet. "
     "Our Google Business Profile looks fine to me. Has anyone had this happen?",
     "Owner at Vance Plumbing", 4, 8, 3, True, "buying signal from a real owner"),
    ("Spent £4k on Google Ads last quarter and got two enquiries. Wondering whether "
     "local SEO would have been the better spend for a roofing company.",
     "Director at Precision Roofing", 6, 12, 7, True, "ads waste, real owner"),
    ("Just opened our second location in Scottsdale. Now trying to work out how to get "
     "the new address showing up properly on Google.",
     "Owner, Summit HVAC", 9, 5, 2, True, "expansion plus a GBP problem"),
    ("Delighted to share that I have completed a certificate in leadership synergy. "
     "So grateful for the journey and everyone who supported me along the way.",
     "Aspiring Leader | Open to work", 3, 40, 12, False, "pure filler"),
    ("We stopped showing up on Google Maps last week and the phone has gone quiet.",
     "Owner at Vance Plumbing", 96, 8, 3, False, "right post, four days stale"),
    ("Here are 10 AI tools that will change your life in 2026. Save this post.",
     "Content Creator | AI Enthusiast", 2, 300, 90, False, "engagement bait, pile-on"),
]


def main() -> int:
    cfg = config.load()
    p_floor = float(cfg.get_path("scoring.min_prospect_score", 45))
    f_floor = float(cfg.get_path("scoring.min_post_score", 40))
    fails = []

    print(f"== people (queue at >= {p_floor:.0f}) ==")
    for headline, loc, company, want, note in PEOPLE:
        score, detail = niche.score_prospect(cfg, headline=headline, location=loc,
                                             company=company)
        got = score >= p_floor and not detail.get("excluded")
        ok = got == want
        if not ok:
            fails.append(f"person {headline!r} @ {loc!r}: wanted "
                         f"{'QUEUE' if want else 'DROP'}, got {score:.0f} {detail}")
        why = detail.get("excluded", "") or f"region={detail.get('region', '?')}"
        print(f"  {'ok  ' if ok else 'FAIL'}  {score:5.1f}  "
              f"{'queue' if got else 'drop ':5}  {headline[:44]:<44}  {note}")
        if not ok:
            print(f"          -> {why}")

    print(f"\n== posts (queue at >= {f_floor:.0f}) ==")
    for body, headline, age, reacts, comments, want, note in POSTS:
        score, detail = niche.score_post(cfg, body=body, author_headline=headline,
                                         age_hours=age, reactions=reacts, comments=comments)
        got = score >= f_floor
        ok = got == want
        if not ok:
            fails.append(f"post by {headline!r}: wanted {'QUEUE' if want else 'DROP'}, "
                         f"got {score:.0f} {detail}")
        print(f"  {'ok  ' if ok else 'FAIL'}  {score:5.1f}  "
              f"{'queue' if got else 'drop ':5}  {body[:44]:<44}  {note}")

    total = len(PEOPLE) + len(POSTS)
    print(f"\n{total - len(fails)}/{total} correct")
    if fails:
        print("\nwrong calls:")
        for f in fails:
            print("  -", f)
        print("\nTune icp: / scoring: in config.yaml, then re-run. "
              "After changing them on a live install, run: python run.py rescore")
    return 1 if fails else 0


if __name__ == "__main__":
    try:
        code = main()
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)
    sys.exit(code)
