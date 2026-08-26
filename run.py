#!/usr/bin/env python
"""linkedin-agent CLI.

  python run.py init            create the database
  python run.py doctor          check config, keys, session, caps -- touches nothing
  python run.py login           open the browser so YOU can log in by hand
  python run.py learn           read your profile, fill in config's me: block
  python run.py draft           write a post and print it. Publishes NOTHING.
  python run.py discover        one discovery pass
  python run.py score           score what has been discovered
  python run.py rescore         re-score everything after tuning the ICP
  python run.py block           one work block (this DOES act on LinkedIn)
  python run.py worker          the autopilot loop, forever
  python run.py dashboard       serve the dashboard
  python run.py status          pipeline, budgets, health
  python run.py pause "reason"  stop all activity
  python run.py resume         clear the pause
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from la import browser, config, db, discover, limits, llm, profile, score, session, write


def cmd_init(args, cfg):
    applied = db.init()
    print(f"database ready at {config.DB_PATH}")
    if applied:
        print("migrations applied:", ", ".join(applied))


def cmd_doctor(args, cfg):
    db.init()
    ok = True

    print("== config ==")
    me = cfg.get("me") or {}
    if me.get("profile_url"):
        print(f"  ok    profile: {me['profile_url']}")
    else:
        # Not fatal: `learn` reads /in/me/ and fills this in itself.
        print("  warn  me.profile_url is empty -- `learn` will discover it")
    if not me.get("name"):
        print("  warn  me.name is empty -- run `python run.py learn` after logging in")
    print(f"  ok    proof entries: {len(cfg.get('proof') or [])}")
    print(f"  ok    banned phrases: {len(cfg.get('banned_phrases') or [])}")

    print("== writing ==")
    cli_ok, info = llm.check()
    print(f"  {'ok  ' if cli_ok else 'FAIL'}  claude CLI: {info}")
    if not cli_ok:
        ok = False
    else:
        print(f"  ok    posts on {cfg.get_path('writing.post_model', 'opus')}, "
              f"comments on {cfg.get_path('writing.comment_model', 'sonnet')} "
              f"(your Claude subscription, no API key)")

    print("== rails ==")
    b = limits.budget(cfg)
    meta = b.pop("_meta")
    print(f"  local time      {meta['local_time']}")
    print(f"  active hours    {'yes' if meta['in_active_hours'] else 'NO (agent is asleep)'}")
    print(f"  warm-up factor  {meta['warmup_factor']} of full caps")
    print(f"  invites/week    {meta['connects_this_week']}/{meta['connects_per_week']}")
    for kind, row in b.items():
        state = "allowed" if row["allowed_now"] else f"blocked: {row['blocked_by']}"
        print(f"  {kind:<8} {row['done']}/{row['cap']} today, {state}")

    print("== session ==")
    s = session.summary()
    print(f"  paused          {'YES -- ' + s['pause_reason'] if s['paused'] else 'no'}")
    detail = f" ({s['session_detail']})" if s["session_detail"] else ""
    print(f"  last known      {s['session_state']}{detail}")
    if s["backoffs"]:
        for k, v in s["backoffs"].items():
            print(f"  backoff         {k}: {v}")

    print("\n" + ("all good" if ok else "fix the FAIL lines above first"))
    return 0 if ok else 1


def cmd_login(args, cfg):
    """Sign in once. Does nothing if the saved session is still good.

    The agent never types the password -- you do, in the window this
    opens. That is deliberate and it is also what makes 2FA a non-issue: the
    session cookie is saved afterwards and reused forever.
    """
    db.init()
    with browser.session() as sess:
        h = session.check(sess)
        if h.ok:
            print("already signed in, nothing to do")
            db.resume()
            return 0

        print(f"not signed in ({h.state}). A Chrome window is opening.")
        print("Sign in to LinkedIn in that window, finish any code it asks for,")
        print("wait until your feed appears, then come back here and press Enter.")
        sess.goto("https://www.linkedin.com/login", settle=3.0)
        try:
            input()
        except EOFError:
            print("no console attached; leaving the window open for 5 minutes")
            import time
            time.sleep(300)

        h = session.check(sess)
        print(f"session state: {h.state} {h.detail}")
        if h.ok:
            db.resume()
            print("signed in. The session is saved and any pause has been cleared.")
            return 0
        print("still not signed in -- run this again when you have a moment")
        return 1


def cmd_ready(args, cfg):
    """One-word answer for the launcher: what still needs doing?"""
    db.init()
    if db.get_state("session_state") != "ok":
        print("NEED_LOGIN")
    elif not (cfg.get("me") or {}).get("name"):
        print("NEED_LEARN")
    else:
        print("READY")
    return 0


def cmd_learn(args, cfg):
    db.init()
    with browser.session() as sess:
        h = session.check(sess)
        if not h.ok:
            print(f"cannot read the profile: {h.state} -- {h.detail}")
            print("run `python run.py login` first")
            return 1
        out = profile.learn(cfg, sess)
    if out.get("error"):
        print("failed:", out["error"])
        return 1
    me = out["me"]
    print(f"name      {me.get('name')}")
    print(f"headline  {me.get('headline')}")
    print(f"location  {me.get('location')}")
    print(f"services  {', '.join(me.get('services') or []) or '(none found)'}")
    print(f"niche     {out.get('niche')}")
    print(f"\nwritten into {config.ROOT / 'config.yaml'}")
    return 0


def cmd_draft(args, cfg):
    """Write a post and print it. Deliberately publishes nothing."""
    db.init()
    body, engine, pillar, problems = write.post(cfg)
    print(f"--- pillar: {pillar.get('name')} · engine: {engine} · "
          f"{len(body.split())} words ---\n")
    print(body)
    print("\n--- guards ---")
    print("PASSED" if not problems else "FAILED: " + "; ".join(problems))
    return 0


def cmd_discover(args, cfg):
    db.init()
    with browser.session() as sess:
        out = discover.run(cfg, sess)
    print(json.dumps(out, indent=2))
    print(json.dumps(score.run(cfg), indent=2))
    return 0


def cmd_score(args, cfg):
    db.init()
    print(json.dumps(score.run(cfg), indent=2))
    return 0


def cmd_rescore(args, cfg):
    db.init()
    print(json.dumps(score.rescore(cfg), indent=2))
    return 0


def cmd_block(args, cfg):
    from la import schedule
    db.init()
    with browser.session() as sess:
        out = schedule.run_block(cfg, sess, block_minutes=args.minutes)
    print(json.dumps(out, indent=2))
    return 0


def cmd_worker(args, cfg):
    from la import schedule
    db.init()
    schedule.run_forever(cfg, block_minutes=args.minutes)
    return 0


def cmd_dashboard(args, cfg):
    import uvicorn
    from la import dashboard
    db.init()
    port = args.port or int(cfg.get_path("dashboard.port", 4300))
    # Local mode never binds beyond this machine; that is what makes skipping the
    # password safe. In the container we must listen on all interfaces so Traefik
    # can reach us, and there a password is mandatory.
    host = "127.0.0.1" if os.environ.get("LA_LOCAL") == "1" else "0.0.0.0"
    print(f"dashboard on http://{host}:{port}")
    uvicorn.run(dashboard.build(cfg), host=host, port=port, log_level="warning")
    return 0


def cmd_status(args, cfg):
    db.init()
    print("== pipeline ==")
    print(json.dumps(db.pipeline_counts(), indent=2))
    print("== today ==")
    print(json.dumps(db.stats(limits.DAY), indent=2))
    print("== last 7 days ==")
    print(json.dumps(db.stats(7 * limits.DAY), indent=2))
    print("== health ==")
    print(json.dumps(session.summary(), indent=2))
    print("== recent ==")
    for a in db.recent_actions(12):
        flag = "ok " if a["ok"] else "ERR"
        print(f"  {flag} {a['kind']:<8} {a['target_name'] or a['target_id'] or ''} "
              f"{(a['error'] or a['body'] or '')[:70]}")
    return 0


def cmd_pause(args, cfg):
    db.init()
    db.pause(args.reason or "paused by hand")
    print("paused:", args.reason or "paused by hand")
    return 0


def cmd_resume(args, cfg):
    db.init()
    db.resume()
    for kind in ("connect", "search", "comment", "post", "react", "follow"):
        session.clear_backoff(kind)
    print("resumed, back-offs cleared")
    return 0


def cmd_profile_review(args, cfg):
    db.init()
    with browser.session() as sess:
        made = profile.review(cfg, sess, apply=args.apply)
    if not made:
        print("no suggestions (either nothing worth changing, or the guards rejected them)")
        return 0
    for m in made:
        print(f"\n=== {m['field']} ===\nCURRENT:\n{m['current']}\n\nPROPOSED:\n{m['proposed']}")
    print(f"\n{'applied' if args.apply else 'queued on the dashboard for approval'}")
    return 0


COMMANDS = {
    "init": cmd_init,
    "doctor": cmd_doctor,
    "login": cmd_login,
    "ready": cmd_ready,
    "learn": cmd_learn,
    "draft": cmd_draft,
    "discover": cmd_discover,
    "score": cmd_score,
    "rescore": cmd_rescore,
    "block": cmd_block,
    "worker": cmd_worker,
    "dashboard": cmd_dashboard,
    "status": cmd_status,
    "pause": cmd_pause,
    "resume": cmd_resume,
    "profile-review": cmd_profile_review,
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=sorted(COMMANDS))
    ap.add_argument("reason", nargs="?", default="", help="pause reason")
    ap.add_argument("--minutes", type=float, default=40.0, help="block length")
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--apply", action="store_true",
                    help="profile-review: apply instead of queueing")
    ap.add_argument("--config", default=None)
    args = ap.parse_args()

    cfg = config.load(args.config)
    return COMMANDS[args.command](args, cfg) or 0


if __name__ == "__main__":
    sys.exit(main())
