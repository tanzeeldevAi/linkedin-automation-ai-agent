"""Who counts as a prospect, and who does not.

Pure functions only -- no browser, no network, no database. That is deliberate:
targeting is the part most likely to be wrong, and this way the whole ICP can be
tested offline against realistic profiles before the agent ever sends an invite.
"""
from __future__ import annotations

import re


def _norm(s: str | None) -> str:
    return re.sub(r"\s+", " ", (s or "").lower()).strip()


def _has(text: str, needles: list[str]) -> list[str]:
    """Substring matches, but word-boundary aware for short needles so 'ceo'
    does not match 'ceos of tomorrow' badly or 'spa' match 'space'."""
    hits = []
    for n in needles:
        n = _norm(n)
        if not n:
            continue
        if len(n) <= 4 or " " not in n:
            if re.search(rf"\b{re.escape(n)}\b", text):
                hits.append(n)
        elif n in text:
            hits.append(n)
    return hits


# --------------------------------------------------------------------------
# region
# --------------------------------------------------------------------------

# LinkedIn almost never writes the country. Real values look like "Dallas,
# Texas", "Dallas, TX", "Greater London Area", "Dallas-Fort Worth Metroplex".
# Matching only on country names excludes nearly every genuine US prospect, so
# these tables carry states, provinces and the metros people actually type.
_US_STATES = [
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
    "connecticut", "delaware", "florida", "hawaii", "idaho", "illinois",
    "indiana", "iowa", "kansas", "kentucky", "louisiana", "maryland",
    "massachusetts", "michigan", "minnesota", "mississippi", "missouri",
    "montana", "nebraska", "nevada", "new hampshire", "new jersey", "new mexico",
    "new york", "north carolina", "north dakota", "ohio", "oklahoma", "oregon",
    "pennsylvania", "rhode island", "south carolina", "south dakota",
    "tennessee", "texas", "utah", "vermont", "virginia", "washington",
    "west virginia", "wisconsin", "wyoming", "district of columbia",
]
_CA_PROVINCES = [
    "ontario", "quebec", "british columbia", "alberta", "manitoba",
    "saskatchewan", "nova scotia", "new brunswick", "newfoundland",
    "prince edward island",
]
# Two-letter codes are only trusted after a comma ("Dallas, TX"), otherwise
# short words in prose produce false positives.
_US_CA_CODES = ("al|ak|az|ar|ca|co|ct|de|fl|ga|hi|id|il|in|ia|ks|ky|la|me|md|ma|mi|mn|"
                "ms|mo|mt|ne|nv|nh|nj|nm|ny|nc|nd|oh|ok|or|pa|ri|sc|sd|tn|tx|ut|vt|va|"
                "wa|wv|wi|wy|dc|on|qc|bc|ab|mb|sk|ns|nb|nl|pe")
_CODE_RE = re.compile(rf",\s*({_US_CA_CODES})\b", re.I)

_US_CA_CITIES = [
    "new york", "los angeles", "chicago", "houston", "phoenix", "philadelphia",
    "san antonio", "san diego", "dallas", "austin", "san jose", "jacksonville",
    "fort worth", "columbus", "charlotte", "indianapolis", "seattle", "denver",
    "boston", "nashville", "las vegas", "detroit", "portland", "miami", "atlanta",
    "tampa", "orlando", "minneapolis", "cleveland", "pittsburgh", "sacramento",
    "kansas city", "st louis", "cincinnati", "salt lake city", "san francisco",
    "toronto", "vancouver", "calgary", "montreal", "ottawa", "edmonton",
    "winnipeg", "hamilton", "mississauga", "brampton", "halifax",
]
_UK_EU_HINTS = [
    "united kingdom", "england", "scotland", "wales", "northern ireland",
    "ireland", "london", "manchester", "birmingham", "leeds", "glasgow",
    "bristol", "liverpool", "edinburgh", "sheffield", "nottingham", "leicester",
    "newcastle", "cardiff", "belfast", "brighton", "coventry", "dublin", "cork",
    "amsterdam", "rotterdam", "berlin", "munich", "hamburg", "frankfurt",
    "madrid", "barcelona", "valencia",
]
_GULF_HINTS = [
    "united arab emirates", "u.a.e", "uae", "dubai", "abu dhabi", "sharjah",
    "ajman", "ras al khaimah", "fujairah", "saudi arabia", "riyadh", "jeddah",
    "dammam", "mecca", "medina", "qatar", "doha", "kuwait", "bahrain", "manama",
    "oman", "muscat",
]


def region_of(cfg, location: str) -> str:
    """Map a free-text LinkedIn location onto one of the configured buckets.

    Configured country names win; the built-in tables above are the fallback for
    the city/state forms LinkedIn actually uses.
    """
    loc = _norm(location)
    if not loc:
        return "other"

    regions = cfg.get_path("icp.regions", {}) or {}
    for key, spec in regions.items():
        if not spec.get("enabled", True):
            continue
        for geo in spec.get("geo", []) or []:
            if _norm(geo) in loc:
                return key

    # Gulf first: "Sharjah, United Arab Emirates" must not be caught by anything
    # else, and these strings are unambiguous.
    if _has(loc, _GULF_HINTS):
        return "uae"
    if _has(loc, _UK_EU_HINTS):
        return "uk_eu"
    if _has(loc, _US_STATES) or _has(loc, _CA_PROVINCES) or _has(loc, _US_CA_CITIES):
        return "us_ca"
    if _CODE_RE.search(loc):
        return "us_ca"
    return "other"


def region_weight(cfg, region: str) -> float:
    """0.0 means "we do not serve here", which excludes the prospect entirely.

    Note the explicit membership test: an unrecognised location resolves to
    "other", which is deliberately NOT in the config, and must score zero rather
    than fall back to a default weight.
    """
    regions = cfg.get_path("icp.regions", {}) or {}
    if region not in regions:
        return 0.0
    spec = regions.get(region) or {}
    if not spec.get("enabled", True):
        return 0.0
    return float(spec.get("weight", 0.5))


# --------------------------------------------------------------------------
# exclusion -- runs before anything else, cheap and decisive
# --------------------------------------------------------------------------

def exclusion_reason(cfg, headline: str, name: str = "", company: str = "") -> str:
    blob = _norm(f"{headline} {company}")
    hits = _has(blob, cfg.get_path("icp.exclude_headline", []) or [])
    if hits:
        return f"excluded headline term: {hits[0]}"
    if not _norm(headline):
        return "no headline to judge"
    return ""


# --------------------------------------------------------------------------
# fit
# --------------------------------------------------------------------------

def title_hits(cfg, headline: str) -> list[str]:
    return _has(_norm(headline), cfg.get_path("icp.titles", []) or [])


def industry_hits(cfg, text: str) -> list[str]:
    return _has(_norm(text), cfg.get_path("icp.industries", []) or [])


def signal_hits(cfg, text: str) -> list[str]:
    return _has(_norm(text), cfg.get_path("icp.buying_signals", []) or [])


def score_prospect(cfg, headline: str, location: str, company: str = "",
                   name: str = "") -> tuple[float, dict]:
    """0-100 fit score plus a breakdown, so the dashboard can explain a decision.

    Shape of the scoring: being a decision maker in a local service trade is
    most of the score. Region is a multiplier rather than a bonus, because a
    perfect plumbing-company owner in a country we do not serve is worth zero.
    """
    detail: dict = {}
    reason = exclusion_reason(cfg, headline, name, company)
    if reason:
        return 0.0, {"excluded": reason}

    blob = f"{headline} {company}"
    titles = title_hits(cfg, headline)
    inds = industry_hits(cfg, blob)
    signals = signal_hits(cfg, blob)
    region = region_of(cfg, location)
    weight = region_weight(cfg, region)

    detail["titles"] = titles
    detail["industries"] = inds
    detail["signals"] = signals
    detail["region"] = region
    detail["region_weight"] = weight

    if weight == 0.0:
        return 0.0, {**detail, "excluded": f"region not served: {region}"}

    score = 0.0
    # Decision maker. Capped: three owner-ish words is not three times better.
    score += min(len(titles), 2) * 30
    # In a trade that lives on the map pack.
    score += min(len(inds), 2) * 25
    # Actively saying they have the problem.
    score += min(len(signals), 2) * 8

    # A named company is a real business rather than a personal brand page.
    if _norm(company):
        score += 7

    # Region is a mild preference between served markets, NOT a heavy penalty.
    # An unserved region already returned zero above; among the rest, a UK owner
    # is worth nearly as much as a US one and must not be scored out of the queue.
    score *= 0.85 + 0.15 * weight
    score = max(0.0, min(100.0, score))
    detail["raw"] = round(score, 1)
    return round(score, 1), detail


def score_post(cfg, body: str, author_headline: str, age_hours: float,
               reactions: int, comments: int) -> tuple[float, dict]:
    """Is this post worth commenting on?

    Priority is not "will this get me likes" but "is the person who wrote this a
    potential client, and can I say something genuinely useful underneath it".
    """
    detail: dict = {}
    blob = f"{body} {author_headline}"

    inds = industry_hits(cfg, blob)
    signals = signal_hits(cfg, body)
    titles = title_hits(cfg, author_headline)
    excluded = exclusion_reason(cfg, author_headline)

    detail.update({"industries": inds, "signals": signals, "titles": titles})
    if excluded:
        # We still allow commenting under an excluded author's post only if the
        # post itself is a strong buying signal -- but it scores much lower.
        detail["author_excluded"] = excluded
        if not signals:
            return 0.0, detail

    score = 0.0
    score += min(len(signals), 3) * 16      # someone describing the problem
    score += min(len(inds), 2) * 14         # in a trade we serve
    score += min(len(titles), 1) * 14       # written by a decision maker
    if detail.get("author_excluded"):
        score *= 0.45

    # Recency. A post nobody is reading any more cannot be commented into life.
    half = float(cfg.get_path("scoring.recency_half_life_hours", 12) or 12)
    recency = 0.5 ** (max(0.0, age_hours) / half) if half > 0 else 1.0
    detail["recency"] = round(recency, 3)
    score *= 0.45 + 0.55 * recency

    # Enough life to be seen, not so much that we are comment number sixty.
    min_r = int(cfg.get_path("discovery.min_post_reactions", 2) or 0)
    max_c = int(cfg.get_path("discovery.max_post_comments", 45) or 10_000)
    if reactions < min_r:
        detail["too_quiet"] = f"{reactions} reactions < {min_r}"
        score *= 0.5
    if comments > max_c:
        detail["pile_on"] = f"{comments} comments > {max_c}"
        score *= 0.3

    score = max(0.0, min(100.0, score))
    detail["raw"] = round(score, 1)
    return round(score, 1), detail
