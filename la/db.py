"""SQLite store.

Everything is here: who we found, what we wrote, what we actually did, and what
came back. The `actions` table is deliberately append-only and records failures
as well as successes -- a dashboard that only shows wins is how you find out in
month three that the agent has been silently broken since week one.
"""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from typing import Any

from .config import DB_PATH, ensure_dirs

SCHEMA = """
-- Singleton key/value for agent state: paused flag, warmup start, session health.
CREATE TABLE IF NOT EXISTS state (
    key        TEXT PRIMARY KEY,
    value      TEXT,
    updated_at REAL NOT NULL
);

-- People who match the ICP and are worth connecting with.
CREATE TABLE IF NOT EXISTS prospects (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    public_id     TEXT NOT NULL UNIQUE,   -- the /in/<public_id> slug, our stable key
    url           TEXT DEFAULT '',
    name          TEXT DEFAULT '',
    headline      TEXT DEFAULT '',
    company       TEXT DEFAULT '',
    location      TEXT DEFAULT '',
    region        TEXT DEFAULT '',        -- us_ca | uk_eu | uae | other
    industry      TEXT DEFAULT '',
    degree        TEXT DEFAULT '',        -- 2nd | 3rd  (1st means already connected)
    score         REAL DEFAULT 0,
    score_detail  TEXT DEFAULT '{}',
    source        TEXT DEFAULT '',        -- search | engager | hashtag | suggested
    status        TEXT DEFAULT 'new',     -- new|queued|invited|accepted|withdrawn|excluded|skipped
    exclude_reason TEXT DEFAULT '',
    note_sent     TEXT DEFAULT '',
    invited_at    REAL DEFAULT 0,
    accepted_at   REAL DEFAULT 0,
    discovered_at REAL NOT NULL
);

-- Other people's posts we might comment on.
CREATE TABLE IF NOT EXISTS feed_posts (
    id             TEXT PRIMARY KEY,      -- LinkedIn activity/share urn
    url            TEXT DEFAULT '',
    author_name    TEXT DEFAULT '',
    author_public_id TEXT DEFAULT '',
    author_headline TEXT DEFAULT '',
    body           TEXT DEFAULT '',
    hashtags       TEXT DEFAULT '[]',
    reactions      INTEGER DEFAULT 0,
    comment_count  INTEGER DEFAULT 0,
    age_hours      REAL DEFAULT 0,
    score          REAL DEFAULT 0,
    score_detail   TEXT DEFAULT '{}',
    source         TEXT DEFAULT '',       -- feed | hashtag:<tag> | search:<q>
    status         TEXT DEFAULT 'new',    -- new | queued | commented | skipped
    skip_reason    TEXT DEFAULT '',
    discovered_at  REAL NOT NULL
);

-- Every action the agent took, successful or not. Append-only.
CREATE TABLE IF NOT EXISTS actions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT NOT NULL,            -- post|comment|connect|react|follow|profile_edit
    target_id   TEXT DEFAULT '',          -- activity urn or prospect public_id
    target_url  TEXT DEFAULT '',
    target_name TEXT DEFAULT '',
    body        TEXT DEFAULT '',          -- exactly what was written
    engine      TEXT DEFAULT '',          -- e.g. claude:opus, claude:sonnet
    permalink   TEXT DEFAULT '',
    ok          INTEGER DEFAULT 0,
    error       TEXT DEFAULT '',
    screenshot  TEXT DEFAULT '',
    created_at  REAL NOT NULL
);

-- Our own published posts, plus how they performed.
CREATE TABLE IF NOT EXISTS my_posts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    action_id    INTEGER,
    urn          TEXT DEFAULT '',
    url          TEXT DEFAULT '',
    body         TEXT DEFAULT '',
    pillar       TEXT DEFAULT '',
    published_at REAL NOT NULL,
    impressions  INTEGER DEFAULT 0,
    reactions    INTEGER DEFAULT 0,
    comments     INTEGER DEFAULT 0,
    reposts      INTEGER DEFAULT 0,
    last_checked REAL DEFAULT 0
);

-- Profile changes the agent thinks are worth making.
CREATE TABLE IF NOT EXISTS profile_suggestions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    field      TEXT NOT NULL,             -- headline | about | featured | services
    current    TEXT DEFAULT '',
    proposed   TEXT DEFAULT '',
    rationale  TEXT DEFAULT '',
    status     TEXT DEFAULT 'proposed',   -- proposed | applied | rejected
    created_at REAL NOT NULL,
    applied_at REAL DEFAULT 0
);

-- Health / audit log surfaced on the dashboard.
CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    level      TEXT DEFAULT 'info',       -- info | warn | error
    kind       TEXT DEFAULT '',
    message    TEXT DEFAULT '',
    created_at REAL NOT NULL
);

-- Warm inbound signals: someone accepted and then engaged, or replied to us.
CREATE TABLE IF NOT EXISTS leads (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    public_id    TEXT DEFAULT '',
    name         TEXT DEFAULT '',
    headline     TEXT DEFAULT '',
    url          TEXT DEFAULT '',
    signal       TEXT DEFAULT '',         -- accepted | commented_on_my_post | replied | viewed_profile
    detail       TEXT DEFAULT '',
    created_at   REAL NOT NULL,
    handled      INTEGER DEFAULT 0
);
"""

INDEXES = """
CREATE INDEX IF NOT EXISTS idx_prospects_status ON prospects(status);
CREATE INDEX IF NOT EXISTS idx_prospects_score  ON prospects(score DESC);
CREATE INDEX IF NOT EXISTS idx_feed_status      ON feed_posts(status);
CREATE INDEX IF NOT EXISTS idx_feed_score       ON feed_posts(score DESC);
CREATE INDEX IF NOT EXISTS idx_actions_time     ON actions(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_actions_kind     ON actions(kind, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_events_time      ON events(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_leads_time       ON leads(created_at DESC);
"""

MIGRATIONS: list[tuple[str, str, str]] = [
    # (table, column, declaration) -- applied on every init(), safe to re-run.
]


def _migrate(cx) -> list[str]:
    applied = []
    for table, col, decl in MIGRATIONS:
        have = {r["name"] for r in cx.execute(f"PRAGMA table_info({table})")}
        if not have:
            continue
        if col not in have:
            cx.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
            applied.append(f"{table}.{col}")
    return applied


@contextmanager
def conn():
    ensure_dirs()
    cx = sqlite3.connect(DB_PATH, timeout=30)
    cx.row_factory = sqlite3.Row
    try:
        cx.execute("PRAGMA journal_mode=WAL")
        yield cx
        cx.commit()
    finally:
        cx.close()


def init() -> list[str]:
    with conn() as cx:
        cx.executescript(SCHEMA)
        applied = _migrate(cx)
        cx.executescript(INDEXES)
    if not get_state("warmup_start"):
        set_state("warmup_start", str(time.time()))
    return applied


# --------------------------------------------------------------------------
# state
# --------------------------------------------------------------------------

def set_state(key: str, value: str) -> None:
    with conn() as cx:
        cx.execute(
            "INSERT INTO state (key, value, updated_at) VALUES (?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (key, str(value), time.time()),
        )


def get_state(key: str, default: str | None = None) -> str | None:
    with conn() as cx:
        row = cx.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def all_state() -> dict[str, str]:
    with conn() as cx:
        return {r["key"]: r["value"] for r in cx.execute("SELECT key, value FROM state")}


def is_paused() -> tuple[bool, str]:
    """The single gate every action checks. Returns (paused, reason)."""
    return get_state("paused", "0") == "1", get_state("pause_reason", "") or ""


def pause(reason: str) -> None:
    set_state("paused", "1")
    set_state("pause_reason", reason)
    log("error", "pause", reason)


def resume() -> None:
    set_state("paused", "0")
    set_state("pause_reason", "")
    log("info", "resume", "activity resumed")


# --------------------------------------------------------------------------
# events
# --------------------------------------------------------------------------

def log(level: str, kind: str, message: str) -> None:
    with conn() as cx:
        cx.execute(
            "INSERT INTO events (level, kind, message, created_at) VALUES (?,?,?,?)",
            (level, kind, message[:2000], time.time()),
        )


def recent_events(limit: int = 50) -> list[sqlite3.Row]:
    with conn() as cx:
        return cx.execute(
            "SELECT * FROM events ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()


# --------------------------------------------------------------------------
# prospects
# --------------------------------------------------------------------------

def upsert_prospect(row: dict[str, Any]) -> bool:
    """Insert a discovered person. Returns True if new. Never overwrites status."""
    row = dict(row)
    row.setdefault("discovered_at", time.time())
    for k in ("score_detail",):
        if isinstance(row.get(k), (dict, list)):
            row[k] = json.dumps(row[k])
    cols = ", ".join(row.keys())
    marks = ", ".join("?" for _ in row)
    with conn() as cx:
        cur = cx.execute(
            f"INSERT OR IGNORE INTO prospects ({cols}) VALUES ({marks})", list(row.values())
        )
        return cur.rowcount > 0


def update_prospect(public_id: str, **fields) -> None:
    if not fields:
        return
    sets, vals = [], []
    for k, v in fields.items():
        sets.append(f"{k}=?")
        vals.append(json.dumps(v) if isinstance(v, (dict, list)) else v)
    vals.append(public_id)
    with conn() as cx:
        cx.execute(f"UPDATE prospects SET {', '.join(sets)} WHERE public_id=?", vals)


def prospects_by_status(status: str, limit: int = 100) -> list[sqlite3.Row]:
    with conn() as cx:
        return cx.execute(
            "SELECT * FROM prospects WHERE status=? ORDER BY score DESC, discovered_at ASC LIMIT ?",
            (status, limit),
        ).fetchall()


def known_prospect_ids() -> set[str]:
    with conn() as cx:
        return {r["public_id"] for r in cx.execute("SELECT public_id FROM prospects")}


# --------------------------------------------------------------------------
# feed posts
# --------------------------------------------------------------------------

def upsert_feed_post(row: dict[str, Any]) -> bool:
    row = dict(row)
    row.setdefault("discovered_at", time.time())
    for k in ("hashtags", "score_detail"):
        if isinstance(row.get(k), (dict, list)):
            row[k] = json.dumps(row[k])
    cols = ", ".join(row.keys())
    marks = ", ".join("?" for _ in row)
    with conn() as cx:
        cur = cx.execute(
            f"INSERT OR IGNORE INTO feed_posts ({cols}) VALUES ({marks})", list(row.values())
        )
        return cur.rowcount > 0


def update_feed_post(post_id: str, **fields) -> None:
    if not fields:
        return
    sets, vals = [], []
    for k, v in fields.items():
        sets.append(f"{k}=?")
        vals.append(json.dumps(v) if isinstance(v, (dict, list)) else v)
    vals.append(post_id)
    with conn() as cx:
        cx.execute(f"UPDATE feed_posts SET {', '.join(sets)} WHERE id=?", vals)


def feed_posts_by_status(status: str, limit: int = 100) -> list[sqlite3.Row]:
    with conn() as cx:
        return cx.execute(
            "SELECT * FROM feed_posts WHERE status=? ORDER BY score DESC LIMIT ?",
            (status, limit),
        ).fetchall()


def known_feed_ids() -> set[str]:
    with conn() as cx:
        return {r["id"] for r in cx.execute("SELECT id FROM feed_posts")}


def authors_commented_since(seconds_ago: float) -> set[str]:
    """Authors we already commented on recently -- never comment on the same
    person twice in a row, that is what makes automation obvious."""
    cutoff = time.time() - seconds_ago
    with conn() as cx:
        rows = cx.execute(
            "SELECT f.author_public_id AS a FROM actions x "
            "JOIN feed_posts f ON f.id = x.target_id "
            "WHERE x.kind='comment' AND x.ok=1 AND x.created_at > ?",
            (cutoff,),
        ).fetchall()
    return {r["a"] for r in rows if r["a"]}


# --------------------------------------------------------------------------
# actions
# --------------------------------------------------------------------------

def record_action(kind: str, ok: bool, target_id: str = "", target_url: str = "",
                  target_name: str = "", body: str = "", engine: str = "",
                  permalink: str = "", error: str = "", screenshot: str = "") -> int:
    with conn() as cx:
        cur = cx.execute(
            "INSERT INTO actions (kind, target_id, target_url, target_name, body, engine, "
            "permalink, ok, error, screenshot, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (kind, target_id, target_url, target_name, body, engine, permalink,
             1 if ok else 0, error[:1000], screenshot, time.time()),
        )
        return cur.lastrowid


def actions_since(seconds_ago: float, kind: str | None = None,
                  only_ok: bool = True) -> list[sqlite3.Row]:
    cutoff = time.time() - seconds_ago
    sql = "SELECT * FROM actions WHERE created_at > ?"
    args: list[Any] = [cutoff]
    if kind:
        sql += " AND kind = ?"
        args.append(kind)
    if only_ok:
        sql += " AND ok = 1"
    sql += " ORDER BY created_at DESC"
    with conn() as cx:
        return cx.execute(sql, args).fetchall()


def count_since(seconds_ago: float, kind: str) -> int:
    return len(actions_since(seconds_ago, kind))


def last_action_time(kind: str | None = None) -> float:
    sql = "SELECT MAX(created_at) AS t FROM actions WHERE ok=1"
    args: list[Any] = []
    if kind:
        sql += " AND kind=?"
        args.append(kind)
    with conn() as cx:
        row = cx.execute(sql, args).fetchone()
    return row["t"] or 0.0


def recent_actions(limit: int = 100) -> list[sqlite3.Row]:
    with conn() as cx:
        return cx.execute(
            "SELECT * FROM actions ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()


# --------------------------------------------------------------------------
# my posts
# --------------------------------------------------------------------------

def record_my_post(action_id: int, urn: str, url: str, body: str, pillar: str) -> int:
    with conn() as cx:
        cur = cx.execute(
            "INSERT INTO my_posts (action_id, urn, url, body, pillar, published_at) "
            "VALUES (?,?,?,?,?,?)",
            (action_id, urn, url, body, pillar, time.time()),
        )
        return cur.lastrowid


def my_posts(limit: int = 50) -> list[sqlite3.Row]:
    with conn() as cx:
        return cx.execute(
            "SELECT * FROM my_posts ORDER BY published_at DESC LIMIT ?", (limit,)
        ).fetchall()


def update_my_post_stats(post_id: int, **fields) -> None:
    fields["last_checked"] = time.time()
    sets = ", ".join(f"{k}=?" for k in fields)
    with conn() as cx:
        cx.execute(f"UPDATE my_posts SET {sets} WHERE id=?", [*fields.values(), post_id])


def recent_post_bodies(limit: int = 25) -> list[str]:
    """Fed back into the writer so it stops repeating itself."""
    return [r["body"] for r in my_posts(limit)]


def recent_pillars(limit: int = 10) -> list[str]:
    return [r["pillar"] for r in my_posts(limit) if r["pillar"]]


# --------------------------------------------------------------------------
# profile suggestions
# --------------------------------------------------------------------------

def add_suggestion(field: str, current: str, proposed: str, rationale: str) -> int:
    with conn() as cx:
        cur = cx.execute(
            "INSERT INTO profile_suggestions (field, current, proposed, rationale, created_at) "
            "VALUES (?,?,?,?,?)",
            (field, current, proposed, rationale, time.time()),
        )
        return cur.lastrowid


def suggestions(status: str = "proposed") -> list[sqlite3.Row]:
    with conn() as cx:
        return cx.execute(
            "SELECT * FROM profile_suggestions WHERE status=? ORDER BY created_at DESC",
            (status,),
        ).fetchall()


def set_suggestion_status(sug_id: int, status: str) -> None:
    with conn() as cx:
        cx.execute(
            "UPDATE profile_suggestions SET status=?, applied_at=? WHERE id=?",
            (status, time.time() if status == "applied" else 0, sug_id),
        )


# --------------------------------------------------------------------------
# leads
# --------------------------------------------------------------------------

def add_lead(public_id: str, name: str, headline: str, url: str,
             signal: str, detail: str = "") -> None:
    with conn() as cx:
        cx.execute(
            "INSERT INTO leads (public_id, name, headline, url, signal, detail, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (public_id, name, headline, url, signal, detail, time.time()),
        )


def leads(limit: int = 100, unhandled_only: bool = False) -> list[sqlite3.Row]:
    sql = "SELECT * FROM leads"
    if unhandled_only:
        sql += " WHERE handled=0"
    sql += " ORDER BY created_at DESC LIMIT ?"
    with conn() as cx:
        return cx.execute(sql, (limit,)).fetchall()


# --------------------------------------------------------------------------
# stats for the dashboard
# --------------------------------------------------------------------------

def stats(window_seconds: float) -> dict[str, Any]:
    cutoff = time.time() - window_seconds
    with conn() as cx:
        by_kind = {
            r["kind"]: r["n"] for r in cx.execute(
                "SELECT kind, COUNT(*) AS n FROM actions "
                "WHERE ok=1 AND created_at > ? GROUP BY kind", (cutoff,)
            )
        }
        fails = cx.execute(
            "SELECT COUNT(*) AS n FROM actions WHERE ok=0 AND created_at > ?", (cutoff,)
        ).fetchone()["n"]
        invited = cx.execute(
            "SELECT COUNT(*) AS n FROM prospects WHERE invited_at > ?", (cutoff,)
        ).fetchone()["n"]
        accepted = cx.execute(
            "SELECT COUNT(*) AS n FROM prospects WHERE accepted_at > ?", (cutoff,)
        ).fetchone()["n"]
        engagement = cx.execute(
            "SELECT COALESCE(SUM(reactions),0) AS r, COALESCE(SUM(comments),0) AS c, "
            "COALESCE(SUM(impressions),0) AS i FROM my_posts WHERE published_at > ?", (cutoff,)
        ).fetchone()
        lead_count = cx.execute(
            "SELECT COUNT(*) AS n FROM leads WHERE created_at > ?", (cutoff,)
        ).fetchone()["n"]

    # Accept rate is measured against invites sent in the window. It lags by
    # design -- an invite sent yesterday may be accepted next week.
    return {
        "posts": by_kind.get("post", 0),
        "comments": by_kind.get("comment", 0),
        "connects": by_kind.get("connect", 0),
        "reactions_given": by_kind.get("react", 0),
        "failures": fails,
        "invited": invited,
        "accepted": accepted,
        "accept_rate": round(accepted / invited, 3) if invited else None,
        "impressions": engagement["i"],
        "post_reactions": engagement["r"],
        "post_comments": engagement["c"],
        "leads": lead_count,
    }


def pipeline_counts() -> dict[str, dict[str, int]]:
    with conn() as cx:
        p = {r["status"]: r["n"] for r in cx.execute(
            "SELECT status, COUNT(*) AS n FROM prospects GROUP BY status")}
        f = {r["status"]: r["n"] for r in cx.execute(
            "SELECT status, COUNT(*) AS n FROM feed_posts GROUP BY status")}
    return {"prospects": p, "feed_posts": f}
