"""Everything the agent says, and the rules that stop it embarrassing anyone.

Two guards run on every generated string, and both can reject and regenerate:

  1. Banned phrases -- the LinkedIn-AI dialect. "In today's digital landscape",
     "game changer", "Thoughts?", rocket emoji. One hit and the text is rewritten.

  2. Unverified numbers -- the model may only use figures that appear in the
     `proof:` block of config.yaml. A fabricated "increased leads by 340%" in
     front of prospects is worse than posting nothing, and an LLM asked to write
     a marketing post will invent statistics unless actively stopped.

If a draft still fails after `writing.max_regenerations`, the caller is expected
to skip that action rather than post the failing text.
"""
from __future__ import annotations

import random
import re

from . import db, llm

# Figures the writer may always use: small counts, clock times, day counts and
# the years it has actually been doing this. Anything larger must come from proof.
_ALWAYS_OK = {
    "1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11", "12",
    "15", "20", "24", "30", "48", "60", "72", "90", "100",
    "2017", "2018", "2019", "2020", "2021", "2022", "2023", "2024", "2025", "2026",
}

_BIG_NUMBER = re.compile(r"\b\d[\d,]*\.?\d*\s*(?:%|k\b|m\b|x\b)?", re.I)


# --------------------------------------------------------------------------
# system prompt
# --------------------------------------------------------------------------

def voice_system(cfg, task: str) -> str:
    me = cfg.get("me", {}) or {}
    proof = cfg.get("proof", []) or []
    positioning = cfg.get("positioning", []) or []
    banned = cfg.get("banned_phrases", []) or []

    services = ", ".join(me.get("services") or []) or (
        "local SEO, Google Business Profile optimisation, websites for local "
        "businesses, and Google Ads")

    return f"""You are writing AS {me.get('name') or 'the account owner'}, in the first person, on their personal LinkedIn account. You are not an assistant writing on their behalf and you never refer to yourself as one.

WHO THEY ARE
{me.get('headline') or 'Local search specialist for local service businesses.'}
Based in {me.get('location') or 'Dubai, UAE'}. Services: {services}.

WHAT THEY HAVE ACTUALLY DONE (the ONLY facts and figures you may state):
{chr(10).join('- ' + p for p in proof)}

HOW THEY THINK (opinions you may express as your own):
{chr(10).join('- ' + p for p in positioning)}

HARD RULES
- Never state a statistic, percentage, client name, or result that is not in the list above. If you want a number and do not have one, write the sentence without a number.
- Never invent a client, a case study, a conversation, or an anecdote with specifics you were not given.
- Never pitch, never sell, never ask for a DM, never include a link.
- No em-dashes. Use a comma or a full stop.
- No markdown formatting of any kind. No bold, no headers, no numbered lists unless they are genuine steps.
- Never use any of these phrases or characters: {', '.join(repr(b) for b in banned)}
- Do not open with a question. Do not close with "Thoughts?" or any variant.
- Write only the text itself. No preamble, no title, no explanation, no quotation marks around it.

VOICE
{cfg.get('voice', '')}

TASK
{task}"""


# --------------------------------------------------------------------------
# guards
# --------------------------------------------------------------------------

def banned_hits(cfg, text: str) -> list[str]:
    low = text.lower()
    return [b for b in (cfg.get("banned_phrases") or []) if b.lower() in low]


def unverified_numbers(cfg, text: str) -> list[str]:
    """Figures in `text` that do not appear anywhere in the proof block.

    Deliberately lenient about small integers -- "three things to check" and
    "ten minutes" are fine. Deliberately strict about anything that reads like a
    result: percentages, thousands, multipliers.
    """
    allowed_blob = " ".join(cfg.get("proof") or []) + " " + " ".join(cfg.get("positioning") or [])
    allowed_digits = set(re.findall(r"\d[\d,]*\.?\d*", allowed_blob))
    # Normalise 4,930 and 4930 to the same thing for comparison.
    allowed_norm = {d.replace(",", "") for d in allowed_digits} | _ALWAYS_OK

    bad = []
    for raw in _BIG_NUMBER.findall(text):
        token = raw.strip()
        digits = re.sub(r"[^\d.]", "", token).rstrip(".")
        if not digits:
            continue
        norm = digits.replace(",", "")
        if norm in allowed_norm:
            continue
        # A bare number under 100 with no unit is almost always prose, not a claim.
        has_unit = bool(re.search(r"(%|k|m|x)$", token, re.I))
        try:
            small = float(norm) < 100
        except ValueError:
            small = False
        if small and not has_unit:
            continue
        bad.append(token)
    return bad


def clean(text: str) -> str:
    """Strip the tells that survive a good prompt anyway."""
    t = text.strip()
    # Models like wrapping the whole thing in quotes.
    if len(t) > 2 and t[0] in "\"'“" and t[-1] in "\"'”":
        t = t[1:-1].strip()
    # Em/en dashes -> comma. This is a voice rule, not cosmetics.
    t = t.replace(" — ", ", ").replace("—", ", ").replace(" – ", ", ")
    # Markdown that LinkedIn renders as literal characters.
    t = re.sub(r"\*\*(.+?)\*\*", r"\1", t)
    # Markdown headers only. The \s+ matters: without it this eats the leading
    # '#' of a hashtag line and turns "#localseo" into "localseo".
    t = re.sub(r"(?m)^#{1,6}\s+", "", t)
    t = re.sub(r"(?m)^\s*[\*\+]\s+", "- ", t)
    # Collapse the three-blank-line habit.
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def _generate(cfg, model: str, task: str, user: str, max_tokens: int,
              check_numbers: bool = True) -> tuple[str, str, list[str]]:
    """Generate, guard, regenerate. Returns (text, engine_label, problems_left)."""
    tries = int(cfg.get_path("writing.max_regenerations", 2) or 0) + 1
    system = voice_system(cfg, task)
    engine = f"claude:{model}"
    problems: list[str] = []
    text = ""

    for attempt in range(tries):
        prompt = user
        if problems:
            prompt = (f"{user}\n\nYour previous attempt was rejected. Fix these and "
                      f"rewrite completely:\n" + "\n".join(f"- {p}" for p in problems))
        text = clean(llm.complete(cfg, system, prompt, model=model, max_tokens=max_tokens))

        problems = []
        hits = banned_hits(cfg, text)
        if hits:
            problems.append(f"contains banned phrasing: {', '.join(hits)}")
        if check_numbers:
            nums = unverified_numbers(cfg, text)
            if nums:
                problems.append(
                    f"states figures that are not in the verified proof list: "
                    f"{', '.join(nums)}. Remove them or write without numbers.")
        if not problems:
            return text, engine, []

        db.log("warn", "write", f"attempt {attempt + 1} rejected: {'; '.join(problems)}")

    return text, engine, problems


# --------------------------------------------------------------------------
# posts
# --------------------------------------------------------------------------

def pick_pillar(cfg) -> dict:
    """Weighted choice, avoiding whatever was used most recently."""
    pillars = cfg.get_path("content.pillars", []) or []
    if not pillars:
        return {"name": "field_note", "brief": "A short observation from client work."}
    recent = db.recent_pillars(3)
    fresh = [p for p in pillars if p.get("name") not in recent] or pillars
    weights = [max(1, int(p.get("weight", 1))) for p in fresh]
    return random.choices(fresh, weights=weights, k=1)[0]


def post(cfg, pillar: dict | None = None) -> tuple[str, str, dict, list[str]]:
    """Write one post. Returns (body, engine, pillar, problems)."""
    pillar = pillar or pick_pillar(cfg)
    lo = int(cfg.get_path("content.post_min_words", 90))
    hi = int(cfg.get_path("content.post_max_words", 220))
    n_tags = int(cfg.get_path("content.hashtags_per_post", 3))
    recent = db.recent_post_bodies(8)

    avoid = ""
    if recent:
        joined = "\n---\n".join(r[:400] for r in recent)
        avoid = (f"\n\nYou have already posted the following recently. Do not repeat "
                 f"these angles, openings, or examples:\n{joined}")

    task = (f"Write one LinkedIn post for the '{pillar.get('name')}' theme.\n\n"
            f"Theme brief: {pillar.get('brief')}")

    user = (
        f"Write the post now.\n\n"
        f"Requirements:\n"
        f"- Between {lo} and {hi} words.\n"
        f"- Open with a concrete, specific statement, not a question and not a hook cliche.\n"
        f"- The audience is owners of local service businesses in the US, Canada, UK and the "
        f"Gulf. They are not marketers. Assume no jargon.\n"
        f"- Give away something genuinely useful. Someone should be able to act on it today "
        f"without hiring anyone.\n"
        f"- Short paragraphs, one idea each, blank line between them.\n"
        f"- End on a flat, factual sentence. No call to action, no question, no invitation to "
        f"comment.\n"
        f"- After the post, on a final separate line, list exactly {n_tags} lowercase hashtags "
        f"separated by spaces, most specific first."
        f"{avoid}"
    )

    model = cfg.get_path("writing.post_model", "opus")
    text, engine, problems = _generate(cfg, model, task, user, max_tokens=1200)

    # Split the trailing hashtag line back off so we control placement.
    lines = [ln for ln in text.split("\n")]
    tags: list[str] = []
    while lines and (not lines[-1].strip() or lines[-1].strip().startswith("#")):
        last = lines.pop().strip()
        if last.startswith("#"):
            tags = [t for t in last.split() if t.startswith("#")][:n_tags]
    body = "\n".join(lines).strip()
    if tags:
        body = f"{body}\n\n{' '.join(tags)}"

    return body, engine, pillar, problems


# --------------------------------------------------------------------------
# comments
# --------------------------------------------------------------------------

def comment(cfg, post_row) -> tuple[str, str, list[str]]:
    """Reply to someone else's post. This is the highest-leverage thing the
    agent does and the easiest to get wrong: a generic comment is worse than
    silence because it is visible to everyone the author knows."""
    lo = int(cfg.get_path("content.comment_min_words", 18))
    hi = int(cfg.get_path("content.comment_max_words", 65))

    author = post_row["author_name"] or "the author"
    headline = post_row["author_headline"] or ""
    body_text = (post_row["body"] or "")[:1800]

    task = ("Write one comment on another person's LinkedIn post. You are a local search "
            "specialist commenting in your own feed, as a peer, not as a vendor.")

    user = (
        f"The post was written by {author}"
        f"{f', whose headline is: {headline}' if headline else ''}.\n\n"
        f"Their post:\n\"\"\"\n{body_text}\n\"\"\"\n\n"
        f"Write your comment now.\n\n"
        f"Requirements:\n"
        f"- Between {lo} and {hi} words. Shorter is better.\n"
        f"- Add ONE concrete thing they did not say: a specific setting, a number of days, a "
        f"failure mode you have actually seen. If you have nothing concrete to add, write a "
        f"short genuine reaction to the most specific detail in their post instead.\n"
        f"- Do not summarise or restate their point back to them.\n"
        f"- Do not compliment the post. No 'great post', no 'well said', no 'spot on'.\n"
        f"- Do not mention your services, your agency, your availability, or any link.\n"
        f"- Do not use their name more than once, and never open with it.\n"
        f"- No hashtags. No emoji.\n"
        f"- Plain sentences. It should read like a comment typed on a phone by someone who "
        f"knows the subject."
    )

    model = cfg.get_path("writing.comment_model", "sonnet")
    return _generate(cfg, model, task, user, max_tokens=400)


# --------------------------------------------------------------------------
# connection notes
# --------------------------------------------------------------------------

def note(cfg, prospect) -> tuple[str, str, list[str]]:
    """A connection request note. 300 chars is LinkedIn's ceiling; we aim under
    280. The only job of this text is to not look like a template."""
    limit = int(cfg.get_path("content.note_max_chars", 280))
    name = (prospect["name"] or "").split(" ")[0]
    headline = prospect["headline"] or ""
    company = prospect["company"] or ""
    location = prospect["location"] or ""

    task = ("Write a short LinkedIn connection request note to a local business owner. "
            "The goal is that they accept, nothing more. You are not selling anything in "
            "this message and must not imply that you will.")

    user = (
        f"The person:\n"
        f"- First name: {name or 'unknown'}\n"
        f"- Headline: {headline or 'unknown'}\n"
        f"- Company: {company or 'unknown'}\n"
        f"- Location: {location or 'unknown'}\n\n"
        f"Write the note now.\n\n"
        f"Requirements:\n"
        f"- Under {limit} characters, total. This is a hard limit.\n"
        f"- Two or three short sentences.\n"
        f"- Reference something specific and true from their headline, company or location. "
        f"If their headline is uninformative, reference their industry or city instead. Never "
        f"invent a detail about them.\n"
        f"- Say plainly what you do in one short clause, as context for why you are reaching "
        f"out to someone in their trade.\n"
        f"- Do not ask for a call, a meeting, a reply, or their time. Do not offer an audit. "
        f"Do not say 'I'd love to connect' or 'let's connect'.\n"
        f"- No flattery, no 'I came across your profile', no 'hope this finds you well'.\n"
        f"- No emoji, no hashtags, no links."
    )

    model = cfg.get_path("writing.note_model", "sonnet")
    text, engine, problems = _generate(cfg, model, task, user, max_tokens=300)

    if len(text) > limit:
        # Trim at a sentence boundary rather than mid-word.
        cut = text[:limit]
        if "." in cut[40:]:
            cut = cut[:cut.rfind(".") + 1]
        text = cut.strip()
        db.log("warn", "write", f"note trimmed to {len(text)} chars")

    return text, engine, problems


# --------------------------------------------------------------------------
# profile copy
# --------------------------------------------------------------------------

def profile_field(cfg, field: str, current: str, extra: str = "") -> tuple[str, str, list[str]]:
    """Rewrite a profile field. Deliberately conservative: this text is read by
    every prospect who clicks through from a comment, so it changes rarely."""
    specs = {
        "headline": (
            "Rewrite the LinkedIn headline. Under 220 characters. It must say who they help, "
            "what result, and in what channel, in plain words a plumber would understand. No "
            "buzzwords, no pipe-separated keyword stuffing beyond what genuinely reads well, "
            "no 'helping businesses grow'."),
        "about": (
            "Rewrite the LinkedIn About section. Between 130 and 260 words. First person. "
            "Open with the specific problem the reader has, not with a job title. Include the "
            "verified results, stated exactly as given. State the honest 60 to 90 day timeline. "
            "Close with how someone starts, which is a free audit, with no phone number and no "
            "link."),
    }
    task = specs.get(field, f"Rewrite the LinkedIn {field} section.")

    user = (f"Their current {field}:\n\"\"\"\n{current or '(empty)'}\n\"\"\"\n\n"
            f"{extra}\n\nWrite the replacement now. Output only the new text.")

    model = cfg.get_path("writing.profile_model", "opus")
    return _generate(cfg, model, task, user, max_tokens=900)
