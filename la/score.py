"""Turning discovered rows into a ranked queue.

Deliberately offline: no browser, no network. That means scoring can be re-run
over everything already discovered after a config change, which is how you tune
the ICP without spending a single LinkedIn page load. See `run.py rescore`.
"""
from __future__ import annotations

from . import db, niche


def score_prospects(cfg) -> dict:
    rows = db.prospects_by_status("new", limit=500)
    queued = skipped = 0
    floor = float(cfg.get_path("scoring.min_prospect_score", 45) or 0)

    for r in rows:
        score, detail = niche.score_prospect(
            cfg,
            headline=r["headline"] or "",
            location=r["location"] or "",
            company=r["company"] or "",
            name=r["name"] or "",
        )
        if detail.get("excluded"):
            db.update_prospect(r["public_id"], score=score, score_detail=detail,
                               status="excluded", exclude_reason=detail["excluded"])
            skipped += 1
            continue
        if score < floor:
            db.update_prospect(r["public_id"], score=score, score_detail=detail,
                               status="skipped",
                               exclude_reason=f"score {score} < {floor}")
            skipped += 1
            continue
        db.update_prospect(r["public_id"], score=score, score_detail=detail, status="queued")
        queued += 1

    return {"queued": queued, "skipped": skipped, "seen": len(rows)}


def score_posts(cfg) -> dict:
    rows = db.feed_posts_by_status("new", limit=500)
    floor = float(cfg.get_path("scoring.min_post_score", 40) or 0)
    cooldown_h = float(cfg.get_path("scoring.same_author_cooldown_hours", 72) or 0)
    recent_authors = db.authors_commented_since(cooldown_h * 3600) if cooldown_h else set()

    queued = skipped = 0
    for r in rows:
        # Never comment on the same person twice in a row. Nothing looks more
        # automated than following one account down its whole timeline.
        if r["author_public_id"] and r["author_public_id"] in recent_authors:
            db.update_feed_post(r["id"], status="skipped",
                                skip_reason=f"author commented on within {cooldown_h:.0f}h")
            skipped += 1
            continue

        score, detail = niche.score_post(
            cfg,
            body=r["body"] or "",
            author_headline=r["author_headline"] or "",
            age_hours=float(r["age_hours"] or 999),
            reactions=int(r["reactions"] or 0),
            comments=int(r["comment_count"] or 0),
        )
        if score < floor:
            db.update_feed_post(r["id"], score=score, score_detail=detail,
                                status="skipped", skip_reason=f"score {score} < {floor}")
            skipped += 1
            continue
        db.update_feed_post(r["id"], score=score, score_detail=detail, status="queued")
        queued += 1

    return {"queued": queued, "skipped": skipped, "seen": len(rows)}


def run(cfg) -> dict:
    p = score_prospects(cfg)
    f = score_posts(cfg)
    db.log("info", "score",
           f"prospects {p['queued']} queued / {p['skipped']} out, "
           f"posts {f['queued']} queued / {f['skipped']} out")
    return {"prospects": p, "posts": f}


def rescore(cfg) -> dict:
    """Send everything previously judged back through scoring after a tuning
    change. Never touches anything already acted on."""
    with db.conn() as cx:
        cx.execute("UPDATE prospects SET status='new', score=0, score_detail='{}', "
                   "exclude_reason='' WHERE status IN ('queued','skipped','excluded')")
        cx.execute("UPDATE feed_posts SET status='new', score=0, score_detail='{}', "
                   "skip_reason='' WHERE status IN ('queued','skipped')")
    return run(cfg)
