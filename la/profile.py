"""Reading your own profile, and keeping it sharp.

Two jobs:

  learn()   -- read the live profile and derive who we are, what we sell, and
               where. This is what makes the agent understand the niche from the
               account rather than from a hand-written brief. It writes the
               `me:` block back into config.yaml.

  review()  -- every `profile.review_every_days`, propose a better headline and
               About section. On autopilot these apply themselves; the previous
               text is always stored in profile_suggestions first, so any change
               can be read back and reverted by hand.

Extraction strategy: scrape the page text, then have the model turn it into
JSON. Field-by-field CSS selectors on a LinkedIn profile break constantly; the
visible text does not. The DOM selectors below are used only as a cross-check.
"""
from __future__ import annotations

import json
import re
import time

from . import browser, config, db, llm, selectors as S, session, write

FIELDS = ("name", "first_name", "headline", "location", "about", "services")


# --------------------------------------------------------------------------
# reading
# --------------------------------------------------------------------------

def read_own(sess) -> dict:
    """Load our own profile and return raw text plus best-effort DOM fields."""
    if not sess.goto(S.ME_URL, settle=4.0):
        return {"error": "could not load own profile"}
    if not session.guard(sess, "read own profile"):
        return {"error": "session not healthy"}

    # The About section lazy-renders; scroll it into view and expand it.
    sess.scroll_like_reading(steps=3)
    try:
        more = S.about_more_button(sess.page)
        if more.count() and more.is_visible():
            more.click()
            browser.human_pause(0.6, 1.4)
    except Exception:
        pass

    raw = ""
    try:
        raw = sess.page.inner_text("main") or ""
    except Exception:
        raw = sess.page.inner_text("body") or ""

    dom = {}
    for key, fn in (("name", S.profile_name), ("headline", S.profile_headline)):
        try:
            loc = fn(sess.page)
            if loc.count():
                dom[key] = (loc.inner_text() or "").strip()
        except Exception:
            pass

    return {
        "url": sess.url,
        "raw_text": raw[:12000],
        "dom": dom,
    }


def _extract(cfg, raw_text: str, dom: dict) -> dict:
    """Turn profile page text into structured fields."""
    system = ("You extract structured data from the text of a LinkedIn profile page. "
              "You output nothing but a single JSON object. You never guess: if a field "
              "is not clearly present in the text, use an empty string or an empty list.")
    user = (
        "Here is the visible text of a LinkedIn profile page.\n\n"
        f"\"\"\"\n{raw_text}\n\"\"\"\n\n"
        "Return a JSON object with exactly these keys:\n"
        '  "name": full name as displayed\n'
        '  "first_name": first name only\n'
        '  "headline": the headline line directly under the name\n'
        '  "location": the location line\n'
        '  "about": the full text of the About section, verbatim, or "" if absent\n'
        '  "services": a list of the specific services this person sells, in their own '
        'words where possible, lowercase, 3 to 10 items\n'
        '  "niche": one short phrase naming the kind of client they serve\n'
        "Output only the JSON object."
    )
    txt = llm.complete(cfg, system, user,
                       model=cfg.get_path("writing.extract_model", "sonnet"),
                       max_tokens=1600, temperature=0.1)
    # Models occasionally wrap JSON in a fence despite instructions.
    m = re.search(r"\{.*\}", txt, re.S)
    if not m:
        raise ValueError(f"could not find JSON in extraction response: {txt[:200]}")
    data = json.loads(m.group(0))

    # DOM cross-check: trust the DOM for the two fields it reads reliably.
    for key in ("name", "headline"):
        if dom.get(key) and not data.get(key):
            data[key] = dom[key]
    return data


def learn(cfg, sess, save: bool = True) -> dict:
    """Read the profile, fill in config's `me:` block, report what changed."""
    got = read_own(sess)
    if got.get("error"):
        db.log("error", "learn", got["error"])
        return got

    data = _extract(cfg, got["raw_text"], got.get("dom", {}))

    me = dict(cfg.get("me") or {})
    before = dict(me)
    for key in FIELDS:
        val = data.get(key)
        if val:
            me[key] = val
    me["profile_url"] = me.get("profile_url") or got.get("url", "")
    me["learned_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    me["learn_source"] = "auto"
    cfg["me"] = me

    if data.get("niche"):
        cfg["detected_niche"] = data["niche"]

    if save:
        config.save_me(me, data.get("niche", ""))

    changed = {k: (before.get(k), me.get(k)) for k in FIELDS
               if (before.get(k) or "") != (me.get(k) or "")}
    db.log("info", "learn",
           f"profile learned: {me.get('name')} / {me.get('headline')[:60] if me.get('headline') else ''}")
    db.set_state("profile_learned_at", str(time.time()))
    return {"me": me, "changed": changed, "niche": data.get("niche", "")}


# --------------------------------------------------------------------------
# reviewing
# --------------------------------------------------------------------------

def due_for_review(cfg) -> bool:
    every = float(cfg.get_path("profile.review_every_days", 14) or 14)
    last = db.get_state("profile_reviewed_at")
    if not last:
        return True
    return (time.time() - float(last)) > every * 86400


def review(cfg, sess, apply: bool | None = None) -> list[dict]:
    """Propose (and optionally apply) better profile copy.

    Returns the list of suggestions created.
    """
    if apply is None:
        apply = bool(cfg.get_path("profile.auto_apply", False))

    got = read_own(sess)
    if got.get("error"):
        return []

    current = _extract(cfg, got["raw_text"], got.get("dom", {}))
    made: list[dict] = []

    audience = ("Owners of local service businesses in the US, Canada, UK and the Gulf: "
                "home services, clinics, law firms, salons, restaurants, auto, transport.")

    for field in ("headline", "about"):
        cur = current.get(field, "") or ""
        proposed, engine, problems = write.profile_field(
            cfg, field, cur,
            extra=f"The audience reading this profile: {audience}")

        if problems:
            db.log("warn", "profile", f"{field} proposal rejected by guards: {problems}")
            continue
        if _too_similar(cur, proposed):
            db.log("info", "profile", f"{field} proposal was not a real change, skipped")
            continue

        rationale = (f"Rewritten for {audience.split(':')[0].lower()}, "
                     f"generated by {engine}.")
        sug_id = db.add_suggestion(field, cur, proposed, rationale)
        made.append({"id": sug_id, "field": field, "current": cur, "proposed": proposed})

        if apply:
            ok, err = apply_field(sess, field, proposed)
            db.set_suggestion_status(sug_id, "applied" if ok else "proposed")
            db.record_action("profile_edit", ok=ok, target_id=field,
                             body=proposed, engine=engine, error=err)
            if not ok:
                db.log("error", "profile", f"could not apply {field}: {err}")

    db.set_state("profile_reviewed_at", str(time.time()))
    return made


def apply_approved(cfg, sess) -> int:
    """Apply any suggestion you approved on the dashboard.

    The dashboard cannot drive the browser itself -- the worker owns the Chrome
    session -- so approving marks the row 'approved' and this runs on the next
    work block.
    """
    rows = db.suggestions("approved")
    applied = 0
    for r in rows:
        ok, err = apply_field(sess, r["field"], r["proposed"])
        db.set_suggestion_status(r["id"], "applied" if ok else "proposed")
        db.record_action("profile_edit", ok=ok, target_id=r["field"],
                         body=r["proposed"], error=err)
        if ok:
            applied += 1
            db.log("info", "profile", f"applied approved {r['field']} change")
        else:
            db.log("error", "profile", f"could not apply {r['field']}: {err}")
    return applied


def _too_similar(a: str, b: str) -> bool:
    """Cheap guard against burning an edit on a cosmetic reword."""
    na = re.sub(r"\W+", " ", (a or "").lower()).strip()
    nb = re.sub(r"\W+", " ", (b or "").lower()).strip()
    if not nb:
        return True
    if na == nb:
        return True
    wa, wb = set(na.split()), set(nb.split())
    if not wa or not wb:
        return False
    overlap = len(wa & wb) / max(len(wa), len(wb))
    return overlap > 0.93


# --------------------------------------------------------------------------
# applying
# --------------------------------------------------------------------------

def apply_field(sess, field: str, text: str) -> tuple[bool, str]:
    """Edit one profile field through the UI. Returns (ok, error)."""
    if field == "headline":
        return _apply_headline(sess, text)
    if field == "about":
        return _apply_about(sess, text)
    return False, f"no handler for field {field!r}"


def _apply_headline(sess, text: str) -> tuple[bool, str]:
    if not sess.goto(S.ME_URL, settle=3.5):
        return False, "could not load profile"
    if not session.guard(sess, "edit headline"):
        return False, "session not healthy"
    try:
        btn = S.edit_intro_button(sess.page)
        if not btn.count():
            return False, "no 'Edit intro' button found"
        btn.click()
        browser.human_pause(1.2, 2.4)

        field = S.headline_input(sess.page)
        if not field.count():
            return False, "no headline input in the intro dialog"
        field.click()
        field.press("Control+a")
        field.press("Delete")
        browser.human_pause(0.3, 0.8)
        field.type(text, delay=28)
        browser.human_pause(0.6, 1.5)

        save = S.save_button(sess.page)
        if not save.count():
            return False, "no Save button in the intro dialog"
        save.click()
        browser.human_pause(2.0, 3.5)
        return True, ""
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def _apply_about(sess, text: str) -> tuple[bool, str]:
    if not sess.goto(S.ME_URL, settle=3.5):
        return False, "could not load profile"
    if not session.guard(sess, "edit about"):
        return False, "session not healthy"
    try:
        btn = S.edit_about_button(sess.page)
        if not btn.count():
            return False, "no 'Edit about' button found"
        btn.click()
        browser.human_pause(1.2, 2.4)

        box = S.about_textarea(sess.page)
        if not box.count():
            return False, "no About editor found"
        box.click()
        box.press("Control+a")
        box.press("Delete")
        browser.human_pause(0.3, 0.8)
        # About is long; type in line chunks rather than char-by-char.
        for i, line in enumerate(text.split("\n")):
            box.type(line, delay=8)
            if i < len(text.split("\n")) - 1:
                box.press("Enter")
        browser.human_pause(0.8, 1.6)

        save = S.save_button(sess.page)
        if not save.count():
            return False, "no Save button in the About dialog"
        save.click()
        browser.human_pause(2.0, 3.5)
        return True, ""
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"
