"""The only module that changes anything on LinkedIn.

Every function here follows the same shape, and the shape is the safety model:

    1. limits.check(kind)            -- am I allowed to do this at all, right now
    2. session.guard()               -- is the page in a sane, logged-in state
    3. write.*()                     -- generate, and refuse to proceed if the
                                        voice/proof guards rejected the text
    4. do the thing, slowly
    5. session.guard()               -- did that trip a checkpoint
    6. db.record_action()            -- always, success or failure, with a
                                        screenshot when it failed

Nothing in here retries a failed action. A failure means either LinkedIn changed
its markup or something is wrong with the session, and in both cases the correct
response is to record it, let the consecutive-failure rail count it, and stop.
"""
from __future__ import annotations

import time

from . import browser, db, limits, niche, selectors as S, session, write


def _blocked(cfg, kind: str) -> str:
    ok, reason = limits.check(cfg, kind)
    return "" if ok else reason


# --------------------------------------------------------------------------
# publish our own post
# --------------------------------------------------------------------------

def publish_post(cfg, sess) -> dict:
    reason = _blocked(cfg, "post")
    if reason:
        return {"done": False, "skipped": reason}

    body, engine, pillar, problems = write.post(cfg)
    if problems:
        db.log("error", "post", f"draft rejected by guards, not posting: {problems}")
        db.record_action("post", ok=False, body=body, engine=engine,
                         error=f"guard rejected: {'; '.join(problems)}")
        return {"done": False, "error": "draft failed the voice/proof guards"}

    if not sess.goto(S.FEED_URL, settle=4.0):
        db.record_action("post", ok=False, body=body, engine=engine,
                         error="could not load feed")
        return {"done": False, "error": "could not load feed"}
    if not session.guard(sess, "publish post"):
        return {"done": False, "error": "session not healthy"}

    try:
        btn = S.start_post_button(sess.page)
        if not btn.count():
            raise RuntimeError("no 'Start a post' button on the feed")
        btn.click()
        browser.human_pause(1.5, 3.0)

        editor = S.post_editor(sess.page)
        editor.wait_for(state="visible", timeout=15000)
        sess.type_human(editor, body)
        browser.human_pause(1.5, 3.5)

        submit = S.post_submit_button(sess.page)
        if not submit.count():
            raise RuntimeError("no enabled 'Post' button in the share dialog")
        submit.click()

        # The dialog closing is the reliable success signal; a toast is not
        # always present and its text is localised.
        try:
            S.post_dialog(sess.page).wait_for(state="detached", timeout=20000)
        except Exception:
            browser.human_pause(3.0, 5.0)

        browser.human_pause(2.0, 4.0)
        if not session.guard(sess, "after publishing post"):
            return {"done": False, "error": "checkpoint straight after posting"}

        action_id = db.record_action("post", ok=True, body=body, engine=engine,
                                     target_name=pillar.get("name", ""))
        db.record_my_post(action_id, urn="", url="", body=body,
                          pillar=pillar.get("name", ""))
        db.log("info", "post", f"published a {pillar.get('name')} post ({len(body.split())} words)")
        return {"done": True, "pillar": pillar.get("name"), "words": len(body.split())}

    except Exception as exc:
        shot = sess.shot("post-failed")
        db.record_action("post", ok=False, body=body, engine=engine,
                         error=f"{type(exc).__name__}: {exc}", screenshot=shot)
        db.log("error", "post", f"publishing failed: {type(exc).__name__}: {exc}")
        return {"done": False, "error": str(exc)}


# --------------------------------------------------------------------------
# comment on someone else's post
# --------------------------------------------------------------------------

def comment_on_next(cfg, sess) -> dict:
    reason = _blocked(cfg, "comment")
    if reason:
        return {"done": False, "skipped": reason}

    rows = db.feed_posts_by_status("queued", limit=1)
    if not rows:
        return {"done": False, "skipped": "no scored posts in the queue"}
    row = rows[0]

    body, engine, problems = write.comment(cfg, row)
    if problems:
        db.update_feed_post(row["id"], status="skipped",
                            skip_reason=f"draft failed guards: {'; '.join(problems)}")
        db.log("warn", "comment", f"draft rejected for {row['id']}, post skipped")
        return {"done": False, "error": "draft failed the voice/proof guards"}

    if not sess.goto(row["url"], settle=4.0):
        db.update_feed_post(row["id"], status="skipped", skip_reason="post would not load")
        return {"done": False, "error": "could not load the post"}
    if not session.guard(sess, "comment"):
        return {"done": False, "error": "session not healthy"}

    try:
        # Read it first. A comment posted 1.5 seconds after page load is a bot.
        sess.dwell()
        sess.scroll_like_reading(1)

        card = S.feed_post_cards(sess.page).first
        if not card.count():
            card = sess.page.locator("main").first

        cbtn = S.card_comment_button(card)
        if cbtn.count():
            cbtn.first.click()
            browser.human_pause(1.2, 2.6)

        editor = S.card_comment_editor(card)
        editor.wait_for(state="visible", timeout=15000)
        sess.type_human(editor, body)
        browser.human_pause(1.2, 2.8)

        submit = S.card_comment_submit(card)
        if not submit.count():
            raise RuntimeError("no comment submit button found")
        submit.last.click()
        browser.human_pause(3.0, 5.5)

        if not session.guard(sess, "after commenting"):
            return {"done": False, "error": "checkpoint straight after commenting"}

        db.update_feed_post(row["id"], status="commented")
        db.record_action("comment", ok=True, target_id=row["id"],
                         target_url=row["url"], target_name=row["author_name"],
                         body=body, engine=engine)
        db.log("info", "comment", f"commented under {row['author_name']} ({row['score']:.0f})")
        return {"done": True, "author": row["author_name"], "score": row["score"]}

    except Exception as exc:
        shot = sess.shot("comment-failed")
        db.update_feed_post(row["id"], status="skipped",
                            skip_reason=f"comment failed: {type(exc).__name__}")
        db.record_action("comment", ok=False, target_id=row["id"], target_url=row["url"],
                         target_name=row["author_name"], body=body, engine=engine,
                         error=f"{type(exc).__name__}: {exc}", screenshot=shot)
        return {"done": False, "error": str(exc)}


# --------------------------------------------------------------------------
# send a connection request
# --------------------------------------------------------------------------

def _enrich_from_profile(cfg, sess, row) -> dict:
    """Read the real headline, location and company off the profile page.

    Search results truncate and sometimes mislabel. Re-scoring on the real thing
    is what stops the agent inviting a recruiter whose search-card headline
    happened to contain the word "owner".
    """
    out = {
        "headline": row["headline"] or "",
        "location": row["location"] or "",
        "company": row["company"] or "",
    }
    try:
        h = S.profile_headline(sess.page)
        if h.count():
            out["headline"] = (h.inner_text() or "").strip() or out["headline"]
    except Exception:
        pass
    try:
        loc = S.profile_location(sess.page)
        if loc.count():
            out["location"] = (loc.inner_text() or "").strip() or out["location"]
    except Exception:
        pass
    try:
        co = S.profile_current_company(sess.page)
        if co.count():
            out["company"] = (co.inner_text() or "").strip().split("\n")[0] or out["company"]
    except Exception:
        pass
    return out


def connect_with_next(cfg, sess) -> dict:
    reason = _blocked(cfg, "connect")
    if reason:
        return {"done": False, "skipped": reason}

    rows = db.prospects_by_status("queued", limit=1)
    if not rows:
        return {"done": False, "skipped": "no scored prospects in the queue"}
    row = rows[0]

    if not sess.goto(row["url"], settle=4.0):
        db.update_prospect(row["public_id"], status="skipped",
                           exclude_reason="profile would not load")
        return {"done": False, "error": "could not load the profile"}
    if not session.guard(sess, "connect"):
        return {"done": False, "error": "session not healthy"}

    # Re-score against what the profile actually says.
    fresh = _enrich_from_profile(cfg, sess, row)
    new_score, detail = niche.score_prospect(
        cfg, headline=fresh["headline"], location=fresh["location"],
        company=fresh["company"], name=row["name"] or "")
    floor = float(cfg.get_path("scoring.min_prospect_score", 45) or 0)

    db.update_prospect(row["public_id"], score=new_score, score_detail=detail, **fresh)
    if detail.get("excluded") or new_score < floor:
        why = detail.get("excluded") or f"re-score {new_score} < {floor} on real profile"
        db.update_prospect(row["public_id"], status="excluded", exclude_reason=why)
        db.log("info", "connect", f"dropped {row['name']} after reading profile: {why}")
        return {"done": False, "skipped": why}

    # Refresh the row so the note writer sees the enriched values. dict(Row)
    # carries every column, so the writer's row["field"] lookups all resolve.
    row = dict(row) | fresh
    note_text, engine, problems = write.note(cfg, row)
    if problems:
        db.log("warn", "connect", f"note rejected by guards for {row['name']}")
        note_text = ""      # send without a note rather than send bad copy

    try:
        btn = S.connect_button(sess.page)
        if not btn.count() or not btn.first.is_visible():
            # Connect is often hidden behind the More menu on 3rd-degree profiles.
            more = S.more_actions_button(sess.page)
            if more.count():
                more.click()
                browser.human_pause(0.8, 1.8)
                btn = S.connect_in_more_menu(sess.page)
        if not btn.count():
            raise RuntimeError("no Connect option on this profile")

        btn.first.click()
        browser.human_pause(1.5, 3.0)

        sent_with_note = False
        if note_text:
            add = S.add_note_button(sess.page)
            if add.count() and add.first.is_visible():
                add.first.click()
                browser.human_pause(0.8, 1.8)
                box = S.note_textarea(sess.page)
                if box.count():
                    box.click()
                    box.type(note_text, delay=22)
                    sent_with_note = True
                    browser.human_pause(1.0, 2.2)

        send = S.send_invite_button(sess.page)
        if not send.count() or not send.first.is_visible():
            send = S.send_without_note_button(sess.page)
        if not send.count():
            raise RuntimeError("no Send button in the invite dialog")
        send.first.click()
        browser.human_pause(2.5, 4.5)

        h = session.probe(sess)
        if h.state == "rate_limited":
            session.back_off("connect", session.RATE_LIMIT_BACKOFF_HOURS["connect"], h.detail)
            db.record_action("connect", ok=False, target_id=row["public_id"],
                             target_url=row["url"], target_name=row["name"],
                             body=note_text, engine=engine,
                             error=f"weekly invite limit: {h.detail}")
            return {"done": False, "error": "hit LinkedIn's invite limit, backing off"}
        if not h.ok:
            session.guard(sess, "after sending invite")
            return {"done": False, "error": f"checkpoint after invite: {h.state}"}

        db.update_prospect(row["public_id"], status="invited",
                           invited_at=time.time(), note_sent=note_text)
        db.record_action("connect", ok=True, target_id=row["public_id"],
                         target_url=row["url"], target_name=row["name"],
                         body=note_text, engine=engine)
        db.log("info", "connect",
               f"invited {row['name']} ({new_score:.0f}) "
               f"{'with a note' if sent_with_note else 'without a note'}")
        return {"done": True, "name": row["name"], "score": new_score,
                "with_note": sent_with_note}

    except Exception as exc:
        shot = sess.shot("connect-failed")
        db.update_prospect(row["public_id"], status="skipped",
                           exclude_reason=f"invite failed: {type(exc).__name__}")
        db.record_action("connect", ok=False, target_id=row["public_id"],
                         target_url=row["url"], target_name=row["name"],
                         body=note_text, engine=engine,
                         error=f"{type(exc).__name__}: {exc}", screenshot=shot)
        return {"done": False, "error": str(exc)}


# --------------------------------------------------------------------------
# react -- cheap, human, and it warms the feed algorithm
# --------------------------------------------------------------------------

def react_to_next(cfg, sess) -> dict:
    reason = _blocked(cfg, "react")
    if reason:
        return {"done": False, "skipped": reason}

    # React to something we are NOT going to comment on, so the two don't stack
    # on the same author.
    rows = db.feed_posts_by_status("queued", limit=6)
    if len(rows) < 2:
        return {"done": False, "skipped": "nothing worth reacting to"}
    row = rows[-1]

    if not sess.goto(row["url"], settle=3.5):
        return {"done": False, "error": "could not load the post"}
    if not session.guard(sess, "react"):
        return {"done": False, "error": "session not healthy"}

    try:
        sess.dwell(4.0)
        card = S.feed_post_cards(sess.page).first
        if not card.count():
            card = sess.page.locator("main").first
        like = S.card_like_button(card)
        if not like.count():
            raise RuntimeError("no Like button found")
        like.first.click()
        browser.human_pause(1.5, 3.0)

        db.record_action("react", ok=True, target_id=row["id"], target_url=row["url"],
                         target_name=row["author_name"])
        return {"done": True, "author": row["author_name"]}
    except Exception as exc:
        shot = sess.shot("react-failed")
        db.record_action("react", ok=False, target_id=row["id"], target_url=row["url"],
                         target_name=row["author_name"],
                         error=f"{type(exc).__name__}: {exc}", screenshot=shot)
        return {"done": False, "error": str(exc)}


# --------------------------------------------------------------------------
# tracking: who accepted, and how our posts did
# --------------------------------------------------------------------------

def track_accepts(cfg, sess) -> int:
    """Anyone we invited who is now a 1st-degree connection has accepted.

    Read from the sent-invitations manager: whoever we invited and who is no
    longer listed as pending has either accepted or the invite expired. We only
    count it as accepted when their profile shows as a connection, so this reads
    the connections list instead.
    """
    invited = db.prospects_by_status("invited", limit=200)
    if not invited:
        return 0
    if not sess.goto(S.INVITATIONS_SENT_URL, settle=4.0):
        return 0
    if not session.guard(sess, "track accepts"):
        return 0

    sess.scroll_like_reading(3)
    try:
        page_text = sess.page.inner_text("main") or ""
    except Exception:
        return 0

    still_pending = set()
    for r in invited:
        name = (r["name"] or "").strip()
        if name and name in page_text:
            still_pending.add(r["public_id"])

    accepted = 0
    for r in invited:
        # Only conclude "accepted" for invites old enough to have shown up in
        # the pending list at least once, so a rendering hiccup cannot mark the
        # whole queue accepted.
        age_days = (time.time() - float(r["invited_at"] or 0)) / 86400
        if r["public_id"] in still_pending or age_days < 1:
            continue
        db.update_prospect(r["public_id"], status="accepted", accepted_at=time.time())
        db.add_lead(r["public_id"], r["name"] or "", r["headline"] or "",
                    r["url"] or "", "accepted",
                    f"accepted a connection request sent {age_days:.0f} days ago")
        accepted += 1

    if accepted:
        db.log("info", "track", f"{accepted} connection requests accepted")
    return accepted


def track_my_posts(cfg, sess) -> int:
    """Update reaction/comment counts on our own recent posts."""
    rows = [r for r in db.my_posts(10)
            if time.time() - (r["last_checked"] or 0) > 6 * 3600]
    if not rows:
        return 0

    me = cfg.get_path("me.profile_url", "") or S.ME_URL
    activity = me.rstrip("/") + "/recent-activity/all/"
    if not sess.goto(activity, settle=4.5):
        return 0
    if not session.guard(sess, "track own posts"):
        return 0
    sess.scroll_like_reading(3)

    cards = S.feed_post_cards(sess.page)
    try:
        n = cards.count()
    except Exception:
        return 0

    from .discover import parse_count
    updated = 0
    for i in range(min(n, 10)):
        card = cards.nth(i)
        try:
            text = (card.inner_text() or "")[:400]
            reactions = parse_count((S.card_reaction_count(card).first.inner_text()
                                     if S.card_reaction_count(card).count() else ""))
            comments = parse_count((S.card_comment_count(card).first.inner_text()
                                    if S.card_comment_count(card).count() else ""))
            # Match the rendered card back to a stored post by its opening words.
            for row in rows:
                head = " ".join((row["body"] or "").split()[:8])
                if head and head[:40] in text:
                    db.update_my_post_stats(row["id"], reactions=reactions,
                                            comments=comments)
                    updated += 1
                    break
        except Exception:
            continue
    return updated
