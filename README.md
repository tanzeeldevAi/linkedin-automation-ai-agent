<div align="center">

# LinkedIn Agent — Run Your LinkedIn Presence on Autopilot

**One post a day, thoughtful comments on the right people's posts, connection requests with a real
note, and profile copy it keeps sharp — all at human pace, all from one config file.**

It is built around the assumption that your LinkedIn account matters. Every rail exists because
losing the account costs more than any amount of extra volume is worth.

[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://python.org)
[![Playwright](https://img.shields.io/badge/Playwright-Chromium-2EAD33?logo=playwright&logoColor=white)](https://playwright.dev)
[![Docker](https://img.shields.io/badge/Docker-ready-2496ED?logo=docker&logoColor=white)](#running-it-on-a-server)
[![Developed by Tanzeel](https://img.shields.io/badge/Developed%20by-Tanzeel-6C3EF5)](https://github.com/tanzeeldevAi)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](#contributing)

</div>

---

## What is this?

Everybody knows what works on LinkedIn: post consistently, comment usefully on your customers'
posts, connect with a real note. Almost nobody does it, because doing it properly takes an hour a
day forever.

This agent does that hour. It:

1. **Reads your own profile** and learns your niche, services and voice from it
2. **Finds the right people** — decision makers in your industries and regions, filtered against
   an ICP you define, with recruiters and crypto bros dropped before scoring
3. **Finds the right posts** — fresh, with real engagement, not a 60-comment pile-on
4. **Writes** a daily post, comments and connection notes in your voice
5. **Acts** at human pace inside strict caps
6. **Keeps your profile sharp**, proposing headline and About rewrites on a cadence

```
learn  ──►  discover  ──►  score  ──►  write  ──►  act  ──►  track
                                         │
                              two guards reject bad drafts
```

> **You log into LinkedIn yourself, once, in a real browser window.** The agent never types your
> password and never stores it.

---

## What keeps the account alive

This is the part worth reading before anything else.

| | |
|---|---|
| 🚦 **Hard rails in Python** | 1 post, 12 comments, 12 invites a day. 80 invites a week, under LinkedIn's own ~100 ceiling. Checked before every single action, not suggested to a model |
| 🕗 **Human clock** | Active hours only (08:00–21:00 local), minimum gaps between actions, weekend damping, and a 14-day warm-up that ramps every cap from 25% to 100% |
| 🔌 **Circuit breaker** | Reads the page before and after every action. A security challenge, a restriction notice or a login wall pauses everything and alerts you. It never tries to answer a challenge — that needs a human |
| 🚫 **Banned-phrase guard** | Rejects the LinkedIn-AI dialect: "in today's digital landscape", "game changer", "unlock the power", "Thoughts?", rocket emojis. Two rewrite attempts, then the draft is skipped rather than sent |
| 🔢 **No invented numbers** | A draft may only state a figure that appears in your `proof:` block. Everything else is written without a number. This is enforced in code, not asked for in a prompt |
| 🛑 **Fails safe** | Three consecutive failures stops the agent and raises an alert |
| 🎛️ **Kill switch** | `python run.py pause "reason"` from anywhere, or one click on the dashboard |

If a rail blocks the agent, **that is the system working**. Raising a cap to get more volume is how
accounts get restricted, and a restricted account cannot be un-restricted by editing a config file.

---

## Requirements

Before you start, make sure you have:

- **[Python 3.10 or newer](https://python.org/downloads)** — on Windows, tick
  **"Add Python to PATH"** while installing
- **A LinkedIn account** with some existing history — do not point this at a brand new profile
- **A Claude subscription** (Claude Pro/Max) with the
  [Claude Code CLI](https://docs.claude.com/en/docs/claude-code) signed in

> Writing runs through the Claude CLI on your **existing subscription**, so there is no API key and
> no per-post bill. A free [Groq](https://console.groq.com) key can handle the higher-volume comment
> stream instead if you prefer.

---

## Install — step by step

It takes about **20 minutes**, most of which is filling in the config honestly.

### Step 1 — Download

```bash
git clone https://github.com/tanzeeldevAi/linkedin-automation-ai-agent.git
cd linkedin-automation-ai-agent
```

No git? Click the green **Code** button above → **Download ZIP** → unzip it.

### Step 2 — Install

```bash
pip install -r requirements.txt
python -m playwright install chromium
```

### Step 3 — Prove it works before touching LinkedIn

```bash
python smoke_test.py
```

**58 checks, completely offline.** Stubbed model, no browser, no LinkedIn. It exercises the ICP,
the rails, the circuit breaker, both writing guards and the scheduler.

```bash
python test_targeting.py
```

**26 realistic profiles and posts** pushed through the ICP, each with the verdict it should get —
a plumbing company owner queues, a recruiter is dropped, a four-day-old post is dropped as stale.

Re-run this one after **every** `icp:` edit. A missed prospect is invisible in production: the
agent simply never invites them and you never find out.

### Step 4 — Fill in the config

Open **`config.yaml`**. Two blocks matter more than everything else:

**`proof:`** — the only numbers the writer is ever allowed to state. It ships with `<N>`
placeholders. Replace each line with something you could pull up in Search Console or an invoice
if a prospect asked on a call.

> If you have no numbers yet, **delete every line and leave the list empty.** The agent then writes
> posts with no figures in them, which is correct and honest. Do not invent a number here — the
> whole guard exists so that your posts can't contain one.

**`icp:`** — who you are trying to reach. Regions, decision-maker titles, industries, and the
`exclude_headline` list that drops recruiters, job seekers and other agencies before scoring.

Then set `me.profile_url` to your own LinkedIn URL, and set `limits.timezone` to your timezone.

### Step 5 — Log in

```bash
python run.py login
```

A real Chrome window opens on LinkedIn. **You sign in by hand**, including any 2FA. The session is
saved to a persistent profile and reused forever after.

### Step 6 — Let it read your profile

```bash
python run.py learn
```

It reads your live profile and fills in the rest of the `me:` block — name, headline, About,
location, services. Open `config.yaml` afterwards and correct anything it got wrong.

### Step 7 — Check everything is healthy

```bash
python run.py doctor
```

Config, rails, session health. **Touches nothing.**

### Step 8 — See a post before anything is published

```bash
python run.py draft
```

Writes a post and prints it to your terminal. **Publishes nothing.** Run it a few times until the
voice sounds like you. If it doesn't, edit `voice:` in the config — that block is the difference
between a post that reads like you and one that reads like ChatGPT.

### Step 9 — Go live

```bash
python run.py block      # one work block - this DOES act on LinkedIn
```

Watch that first block. When you're happy:

```bash
python run.py worker     # the autopilot loop
python run.py dashboard  # http://localhost:4300
```

**That's it — you're running.** 🎉

> On Windows, **`START-AGENT.bat`** runs the worker and dashboard together, **`STOP-AGENT.bat`**
> stops them, and **`PREVIEW-POST.bat`** is Step 8 as a double-click.

---

## Commands

| Command | What it does |
|---|---|
| `python run.py doctor` | Config, keys, rails, session health. Touches nothing |
| `python run.py login` | Opens the browser so **you** can sign in |
| `python run.py learn` | Reads your profile, fills in the config's `me:` block |
| `python run.py draft` | Writes a post and prints it. Publishes nothing |
| `python run.py status` | Pipeline, budgets, recent activity |
| `python run.py rescore` | Re-judge stored rows after tuning the ICP |
| `python run.py block` | One work block — **this acts on LinkedIn** |
| `python run.py worker` | The autopilot loop |
| `python run.py dashboard` | Serve the dashboard |
| `python run.py pause "reason"` · `resume` | Kill switch |

---

## Running it on a server

The stack builds a container with Chrome, x11vnc and noVNC inside, so you can log in by hand
through a browser view even though the server has no screen.

```bash
cp .env.example .env      # set DASHBOARD_PASSWORD and VNC_PASSWORD
docker compose up -d --build
```

It binds to **localhost only**. Reach it through an SSH tunnel:

```bash
ssh -L 4300:127.0.0.1:4300 -L 6080:127.0.0.1:6080 user@your-server
```

Then the dashboard is at <http://localhost:4300> and the browser view at <http://localhost:6080>.

> ⚠️ **The entrypoint refuses to start without both passwords set.** That is deliberate: an
> unlocked noVNC view is a browser already logged into your LinkedIn account, exposed to the
> internet. There is a Traefik override in `docker-compose.traefik.yml` for putting it on a domain,
> which adds basic auth on top of the VNC password.

`config.yaml` is **mounted, not baked into the image** — `run.py learn` writes back to it, and
tuning should be an edit plus a restart rather than a rebuild.

---

## Troubleshooting

<details>
<summary><b>Nothing gets queued / the agent does nothing</b></summary>

Run `python run.py status` first — it shows the budgets and the pipeline.

Most common causes: you're outside `limits.active_hours`, you're still inside the 14-day warm-up
(caps are ramped, so early days are quiet on purpose), or the ICP is too narrow. Run
`python test_targeting.py` and `python run.py rescore` after any `icp:` change.
</details>

<details>
<summary><b>Everything paused and there's an alert</b></summary>

The circuit breaker caught a security challenge, a restriction notice or a login wall. **Do not
just resume.** Open LinkedIn in a normal browser as yourself, deal with whatever it's asking, then
`python run.py resume`.
</details>

<details>
<summary><b>Posts get skipped instead of published</b></summary>

Both rewrite attempts failed a guard. Two likely reasons: the model keeps reaching for a banned
phrase, or it keeps wanting to state a number that isn't in your `proof:` block. `run.py draft`
prints the problems. Adding real proof lines fixes the second one.
</details>

<details>
<summary><b>Selectors broke after a LinkedIn update</b></summary>

**Every** LinkedIn selector in the project lives in `la/selectors.py`, on purpose. That's the only
file to fix. Open an issue with what you see and it'll get patched.
</details>

<details>
<summary><b>"DASHBOARD_PASSWORD is not set, refusing to serve"</b></summary>

You're running non-locally without a password. Copy `.env.example` to `.env` and set it. The
dashboard shows your account's activity and holds the kill switch — it doesn't get served unlocked.
</details>

<details>
<summary><b>The invite ceiling was hit</b></summary>

LinkedIn enforces roughly 100 invites a week on a free account itself. The config caps at 80 to
stay under it. If you're hitting the ceiling anyway, you have invites pending from before — cancel
some old ones.
</details>

---

## Layout

```
config.yaml            all tuning lives here
run.py                 CLI
keep_chrome.py         holds the one Chrome open (container only)
la/
  config.py            config + paths
  db.py                SQLite store
  browser.py           Playwright: launch, attach over CDP, human pacing
  session.py           page-state classification + the circuit breaker
  selectors.py         EVERY LinkedIn selector in the project
  profile.py           read own profile, learn the niche, propose edits
  niche.py             the ICP: pure, offline, testable
  discover.py          feed, hashtags, people search, engagers
  score.py             ranking pass over stored rows
  llm.py               Claude for posts, Groq for comments
  write.py             prompts, voice, and the two guards
  limits.py            the rails
  act.py               the only module that changes anything on LinkedIn
  schedule.py          budget-burn pacing, work blocks, the worker loop
  dashboard.py         FastAPI: stats, activity, kill switch
deploy/                supervisord + entrypoint for the container
```

---

## Please use this responsibly

LinkedIn's terms restrict automated access. This tool is written to behave like an attentive
person rather than a scraper, and to stop rather than push when the platform pushes back — but you
are the one accepting that risk, on your own account.

If a comment wouldn't be worth reading from a human, don't let it post.

---

## Contributing

Issues and pull requests are welcome — especially resilient selectors, better ICP scoring, and
alternative writing backends.

## License

[MIT](LICENSE) — free to use, change and share. If it helps you, a ⭐ on the repo is appreciated.

---

<div align="center">

**Built by [@tanzeeldevAi](https://github.com/tanzeeldevAi)**

*linkedin automation · linkedin bot · social media automation · AI agent · lead generation
personal branding · content automation · Playwright · Python · Claude AI · Groq · Docker
linkedin outreach · b2b prospecting · ICP targeting · autopilot*

</div>
