"""Finding things worth acting on.

Three sources, in cost order:

  feed      -- free, unmetered, and already personalised to the niche. Always on.
  hashtags  -- free, and the fastest way to reach people outside the network.
  search    -- metered. LinkedIn applies a monthly "commercial use limit" to
               search, and burning it in week one blinds the agent for the rest
               of the month. So: ONE query per cycle, rotating through the list.

Discovery only reads and records. It never scores and never acts -- scoring is
score.py, acting is act.py. Keeping them separate means a bad scoring change can
be re-run over already-discovered rows without touching LinkedIn again.
"""
from __future__ import annotations

import re
import time

from . import browser, db, selectors as S, session

# "2h", "3 days ago", "1w", "45m" -> hours
_AGE = re.compile(r"(\d+)\s*(minute|min|m|hour|hr|h|day|d|week|w|month|mo)", re.I)


def parse_age_hours(text: str) -> float:
    """LinkedIn shows relative ages only. Anything unparseable is treated as old
    so it fails the freshness filter rather than sneaking through."""
    if not text:
        return 999.0
    m = _AGE.search(text)
    if not m:
        return 999.0
    n, unit = int(m.group(1)), m.group(2).lower()
    if unit in ("minute", "min", "m"):
        return n / 60.0
    if unit in ("hour", "hr", "h"):
        return float(n)
    if unit in ("day", "d"):
        return n * 24.0
    if unit in ("week", "w"):
        return n * 24.0 * 7
    return n * 24.0 * 30


def parse_count(text: str) -> int:
    """'1,234 reactions' -> 1234. '2K' -> 2000."""
    if not text:
        return 0
    t = text.replace(",", "").lower()
    m = re.search(r"(\d+\.?\d*)\s*([km])?", t)
    if not m:
        return 0
    val = float(m.group(1))
    if m.group(2) == "k":
        val *= 1_000
    elif m.group(2) == "m":
        val *= 1_000_000
    return int(val)


def public_id_from_url(url: str) -> str:
    """https://www.linkedin.com/in/some-slug/?foo -> some-slug"""
    m = re.search(r"/in/([^/?#]+)", url or "")
    return m.group(1) if m else ""


def _text_of(locator) -> str:
    try:
        if locator.count():
            return (locator.first.inner_text() or "").strip()
    except Exception:
        pass
    return ""


def _attr(locator, name: str) -> str:
    try:
        if locator.count():
            return locator.first.get_attribute(name) or ""
    except Exception:
        pass
    return ""


# --------------------------------------------------------------------------
# posts
# --------------------------------------------------------------------------

def _harvest_cards(cfg, sess, source: str, limit: int) -> int:
    """Read every rendered post card on the current page into feed_posts."""
    known = db.known_feed_ids()
    added = 0
    max_age = float(cfg.get_path("discovery.max_post_age_hours", 20) or 20)

    cards = S.feed_post_cards(sess.page)
    try:
        total = cards.count()
    except Exception:
        return 0

    for i in range(min(total, limit * 3)):   # over-scan: many cards get filtered
        if added >= limit:
            break
        card = cards.nth(i)
        try:
            urn = _attr(card, "data-urn") or _attr(card, "data-id")
            if not urn or not urn.startswith("urn:li:"):
                continue
            if urn in known:
                continue

            author_url = _attr(S.card_author_link(card), "href")
            author_pid = public_id_from_url(author_url)
            # Skip our own posts, and company-page posts (no /in/ author).
            if not author_pid:
                continue

            age_text = _text_of(S.card_age(card))
            age_h = parse_age_hours(age_text)
            if age_h > max_age:
                continue

            # Expand truncated text so scoring sees the whole post.
            try:
                more = S.card_see_more(card)
                if more.count() and more.first.is_visible():
                    more.first.click()
                    browser.human_pause(0.3, 0.9)
            except Exception:
                pass

            body = _text_of(S.card_body(card))
            if len(body) < 40:          # image-only or one-liner: nothing to add to
                continue

            row = {
                "id": urn,
                "url": f"https://www.linkedin.com/feed/update/{urn}/",
                "author_name": _text_of(S.card_author_name(card)),
                "author_public_id": author_pid,
                "author_headline": _text_of(S.card_author_headline(card)),
                "body": body[:4000],
                "hashtags": re.findall(r"#(\w+)", body)[:10],
                "reactions": parse_count(_text_of(S.card_reaction_count(card))),
                "comment_count": parse_count(_text_of(S.card_comment_count(card))),
                "age_hours": round(age_h, 2),
                "source": source,
            }
            if db.upsert_feed_post(row):
                added += 1
                known.add(urn)
        except Exception as exc:
            db.log("warn", "discover", f"card {i} on {source} failed: {type(exc).__name__}")
            continue
    return added


def from_feed(cfg, sess) -> int:
    if not cfg.get_path("discovery.read_own_feed", True):
        return 0
    if not sess.goto(S.FEED_URL, settle=4.0):
        return 0
    if not session.guard(sess, "read feed"):
        return 0
    sess.scroll_like_reading(int(cfg.get_path("discovery.feed_scroll_steps", 6) or 6))
    n = _harvest_cards(cfg, sess, "feed",
                       int(cfg.get_path("discovery.max_posts_per_source", 15) or 15))
    db.log("info", "discover", f"feed: {n} new posts")
    return n


def from_hashtag(cfg, sess) -> int:
    """One hashtag per cycle, rotating, so the agent's footprint stays even."""
    tags = cfg.get_path("discovery.hashtags", []) or []
    if not tags:
        return 0
    idx = int(db.get_state("hashtag_cursor", "0") or 0) % len(tags)
    tag = tags[idx]
    db.set_state("hashtag_cursor", str(idx + 1))

    if not sess.goto(S.hashtag_url(tag), settle=4.0):
        return 0
    if not session.guard(sess, f"hashtag {tag}"):
        return 0
    sess.scroll_like_reading(4)
    n = _harvest_cards(cfg, sess, f"hashtag:{tag}",
                       int(cfg.get_path("discovery.max_posts_per_source", 15) or 15))
    db.log("info", "discover", f"#{tag}: {n} new posts")
    return n


# --------------------------------------------------------------------------
# people
# --------------------------------------------------------------------------

def people_from_search(cfg, sess) -> int:
    """ONE search query per cycle. See the module docstring on why."""
    blocked, why = session.blocked_until("search")
    if blocked:
        db.log("info", "discover", f"skipping people search: {why}")
        return 0

    queries = cfg.get_path("discovery.people_queries", []) or []
    if not queries:
        return 0
    idx = int(db.get_state("people_cursor", "0") or 0) % len(queries)
    query = queries[idx]
    db.set_state("people_cursor", str(idx + 1))

    if not sess.goto(S.people_search_url(query), settle=4.5):
        return 0
    h = session.probe(sess)
    if h.state == "rate_limited":
        session.back_off("search", 26.0, h.detail)
        return 0
    if not h.ok:
        session.guard(sess, f"people search {query}")
        return 0

    sess.scroll_like_reading(3)
    known = db.known_prospect_ids()
    limit = int(cfg.get_path("discovery.max_people_per_query", 10) or 10)
    added = 0

    cards = S.people_result_cards(sess.page)
    try:
        total = cards.count()
    except Exception:
        return 0

    for i in range(min(total, limit * 3)):
        if added >= limit:
            break
        card = cards.nth(i)
        try:
            href = _attr(S.result_profile_link(card), "href")
            pid = public_id_from_url(href)
            if not pid or pid in known or pid == "me":
                continue

            name = _text_of(S.result_name(card))
            headline = _text_of(S.result_headline(card))
            location = _text_of(S.result_location(card))
            if not headline:
                continue

            # The degree badge is in the card text: "· 2nd", "· 3rd".
            card_text = ""
            try:
                card_text = (card.inner_text() or "")
            except Exception:
                pass
            degree = ""
            for d in ("1st", "2nd", "3rd"):
                if f"· {d}" in card_text or f"• {d}" in card_text:
                    degree = d
                    break
            if degree == "1st":       # already connected, nothing to send
                continue

            row = {
                "public_id": pid,
                "url": S.profile_url(pid),
                "name": name,
                "headline": headline,
                "location": location,
                "degree": degree,
                "source": f"search:{query}",
            }
            if db.upsert_prospect(row):
                added += 1
                known.add(pid)
        except Exception as exc:
            db.log("warn", "discover", f"result {i} failed: {type(exc).__name__}")
            continue

    db.log("info", "discover", f"search {query!r}: {added} new people")
    return added


def people_from_engagers(cfg, sess, max_people: int = 8) -> int:
    """People who commented on posts we already scored highly.

    Cheaper than search and far better qualified: they were talking about the
    problem in public. Uses only already-stored posts, so no extra page loads
    beyond the post itself.
    """
    rows = db.feed_posts_by_status("queued", limit=3)
    if not rows:
        return 0

    known = db.known_prospect_ids()
    added = 0
    for row in rows:
        if added >= max_people:
            break
        if not sess.goto(row["url"], settle=3.5):
            continue
        if not session.guard(sess, "read engagers"):
            return added
        sess.scroll_like_reading(2)

        links = sess.page.locator("article a[href*='/in/'], "
                                  "div.comments-comment-item a[href*='/in/']")
        try:
            n = links.count()
        except Exception:
            continue
        for i in range(min(n, 25)):
            if added >= max_people:
                break
            try:
                pid = public_id_from_url(links.nth(i).get_attribute("href") or "")
                if not pid or pid in known or pid == "me":
                    continue
                name = (links.nth(i).inner_text() or "").strip().split("\n")[0]
                if not name:
                    continue
                if db.upsert_prospect({
                    "public_id": pid,
                    "url": S.profile_url(pid),
                    "name": name,
                    "headline": "",         # filled in when we visit to connect
                    "source": f"engager:{row['id']}",
                }):
                    added += 1
                    known.add(pid)
            except Exception:
                continue
        browser.human_pause(2.0, 5.0)

    if added:
        db.log("info", "discover", f"engagers: {added} new people")
    return added


def run(cfg, sess) -> dict:
    """One full discovery pass. Order matters: free sources first, so if the
    session trips a guard we have still gathered something."""
    out = {"feed": 0, "hashtag": 0, "search": 0, "engagers": 0}
    out["feed"] = from_feed(cfg, sess)
    browser.human_pause(3, 7)

    paused, _ = db.is_paused()
    if not paused:
        out["hashtag"] = from_hashtag(cfg, sess)
        browser.human_pause(3, 7)

    paused, _ = db.is_paused()
    if not paused:
        out["search"] = people_from_search(cfg, sess)

    db.set_state("last_discovery", str(time.time()))
    return out
