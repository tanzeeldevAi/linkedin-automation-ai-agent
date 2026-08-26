"""The dashboard.

Read-mostly on purpose. It shows what the agent actually did -- including the
failures and the screenshots of them -- and offers exactly four controls: stop,
start, approve a profile change, reject a profile change.

It never drives the browser. The worker owns the Chrome session (see
browser.attach), so anything needing a page load is written to the database as an
intent and picked up on the next work block.
"""
from __future__ import annotations

import os
import secrets
import time
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from . import db, limits, session
from .config import SHOT_DIR, env

STATIC = Path(__file__).resolve().parent / "static"
# auto_error=False so local mode can skip the browser's password prompt entirely
# instead of returning a 403 with no credentials present.
security = HTTPBasic(auto_error=False)


def build(cfg) -> FastAPI:
    app = FastAPI(title=cfg.get_path("dashboard.title", "LinkedIn Agent"),
                  docs_url=None, redoc_url=None)
    password = env("DASHBOARD_PASSWORD") or ""
    # Local mode: the launcher binds to 127.0.0.1 only, so nothing outside this
    # machine can reach it and a password is pure friction. Set by START-AGENT.bat.
    # It is never set on the VPS, where entrypoint.sh refuses to boot without a
    # password at all.
    local_only = os.environ.get("LA_NO_AUTH") == "1"

    def auth(creds: HTTPBasicCredentials | None = Depends(security)) -> str:
        if local_only:
            return "local"
        if not password:
            # No password set: refuse rather than silently serve the account's
            # whole activity history to the internet.
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "DASHBOARD_PASSWORD is not set in .env, refusing to serve")
        if creds is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "password required",
                                headers={"WWW-Authenticate": "Basic"})
        if not secrets.compare_digest(creds.password, password):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "wrong password",
                                headers={"WWW-Authenticate": "Basic"})
        return creds.username

    # ---------------------------------------------------------------- pages

    @app.get("/", response_class=HTMLResponse)
    def index(_: str = Depends(auth)):
        page = STATIC / "dashboard.html"
        if not page.exists():
            return HTMLResponse("<h1>dashboard.html is missing</h1>", status_code=500)
        return HTMLResponse(page.read_text(encoding="utf-8"))

    @app.get("/healthz")
    def healthz():
        """Unauthenticated, for the container health check only. No data."""
        paused, _ = db.is_paused()
        return {"ok": True, "paused": paused}

    # ----------------------------------------------------------------- api

    @app.get("/api/summary")
    def summary(_: str = Depends(auth)):
        budgets = limits.budget(cfg)
        meta = budgets.pop("_meta")
        me = cfg.get("me") or {}
        return {
            "me": {
                "name": me.get("name", ""),
                "headline": me.get("headline", ""),
                "profile_url": me.get("profile_url", ""),
                "learned_at": me.get("learned_at", ""),
                "unreviewed": me.get("learn_source") == "auto",
            },
            "today": db.stats(limits.DAY),
            "week": db.stats(7 * limits.DAY),
            "month": db.stats(30 * limits.DAY),
            "budgets": budgets,
            "meta": meta,
            "health": session.summary(),
            "pipeline": db.pipeline_counts(),
            "worker_status": db.get_state("worker_status", "unknown"),
            "last_block": db.get_state("last_block_start", ""),
            "server_time": time.time(),
        }

    @app.get("/api/activity")
    def activity(limit: int = 60, _: str = Depends(auth)):
        rows = db.recent_actions(min(limit, 300))
        return [{
            "id": r["id"],
            "kind": r["kind"],
            "ok": bool(r["ok"]),
            "target_name": r["target_name"],
            "target_url": r["target_url"],
            "body": r["body"],
            "engine": r["engine"],
            "error": r["error"],
            "screenshot": r["screenshot"],
            "at": r["created_at"],
        } for r in rows]

    @app.get("/api/events")
    def events(limit: int = 50, _: str = Depends(auth)):
        return [{"level": r["level"], "kind": r["kind"], "message": r["message"],
                 "at": r["created_at"]} for r in db.recent_events(min(limit, 200))]

    @app.get("/api/leads")
    def leads(_: str = Depends(auth)):
        return [{"name": r["name"], "headline": r["headline"], "url": r["url"],
                 "signal": r["signal"], "detail": r["detail"], "at": r["created_at"]}
                for r in db.leads(80)]

    @app.get("/api/posts")
    def posts(_: str = Depends(auth)):
        return [{"body": r["body"], "pillar": r["pillar"], "at": r["published_at"],
                 "reactions": r["reactions"], "comments": r["comments"],
                 "impressions": r["impressions"], "url": r["url"]}
                for r in db.my_posts(30)]

    @app.get("/api/suggestions")
    def suggestions(_: str = Depends(auth)):
        out = []
        for st in ("proposed", "approved"):
            for r in db.suggestions(st):
                out.append({"id": r["id"], "field": r["field"], "current": r["current"],
                            "proposed": r["proposed"], "rationale": r["rationale"],
                            "status": r["status"], "at": r["created_at"]})
        return out

    @app.get("/api/queue")
    def queue(_: str = Depends(auth)):
        return {
            "posts": [{"author": r["author_name"], "headline": r["author_headline"],
                       "score": r["score"], "url": r["url"],
                       "body": (r["body"] or "")[:400], "age_hours": r["age_hours"]}
                      for r in db.feed_posts_by_status("queued", 15)],
            "people": [{"name": r["name"], "headline": r["headline"],
                        "location": r["location"], "score": r["score"], "url": r["url"]}
                       for r in db.prospects_by_status("queued", 15)],
        }

    @app.get("/shots/{name}")
    def shot(name: str, _: str = Depends(auth)):
        # Resolve inside SHOT_DIR only -- never serve an arbitrary path.
        target = (SHOT_DIR / name).resolve()
        if not str(target).startswith(str(SHOT_DIR.resolve())) or not target.exists():
            raise HTTPException(404, "no such screenshot")
        return FileResponse(target)

    # -------------------------------------------------------------- actions

    @app.post("/api/pause")
    def do_pause(_: str = Depends(auth)):
        db.pause("stopped from the dashboard")
        return {"paused": True}

    @app.post("/api/resume")
    def do_resume(_: str = Depends(auth)):
        db.resume()
        for kind in ("connect", "search", "comment", "post", "react", "follow"):
            session.clear_backoff(kind)
        return {"paused": False}

    @app.post("/api/suggestions/{sug_id}/{decision}")
    def decide(sug_id: int, decision: str, _: str = Depends(auth)):
        if decision not in ("approve", "reject"):
            raise HTTPException(400, "decision must be approve or reject")
        # 'approved' is picked up by profile.apply_approved() on the next block.
        db.set_suggestion_status(sug_id, "approved" if decision == "approve" else "rejected")
        return JSONResponse({"ok": True, "status": decision})

    return app
