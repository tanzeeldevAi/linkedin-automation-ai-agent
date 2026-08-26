"""Config loading + project paths. One config file drives the whole agent."""
from __future__ import annotations

import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(os.environ.get("LA_ROOT") or Path(__file__).resolve().parent.parent)
DATA_DIR = Path(os.environ.get("LA_DATA") or ROOT / "data")
DB_PATH = DATA_DIR / "linkedin.db"
SHOT_DIR = DATA_DIR / "shots"
PROFILE_DIR = Path(os.environ.get("LA_CHROME_PROFILE") or DATA_DIR / "chrome-profile")

load_dotenv(ROOT / ".env")


class Config(dict):
    """Dict with dotted access, so cfg.get_path('limits.comments_per_day') works."""

    def get_path(self, dotted: str, default=None):
        node = self
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node


def load(path: str | Path | None = None) -> Config:
    path = Path(path) if path else ROOT / "config.yaml"
    if not path.exists():
        raise SystemExit(f"config not found: {path}")
    with open(path, "r", encoding="utf-8") as fh:
        return Config(yaml.safe_load(fh) or {})


def save_me(me: dict, niche: str = "", path: str | Path | None = None) -> None:
    """Write the learned `me:` block back into config.yaml, keeping comments.

    `learn` is the only thing that writes to this file, and it must not destroy
    it in the process: config.yaml carries the explanation of what every knob
    does, including the warning above the rate limits. yaml.safe_dump would strip
    all of that, so this does a round-trip edit and touches only the keys that
    actually changed.
    """
    path = Path(path) if path else ROOT / "config.yaml"
    try:
        from ruamel.yaml import YAML
    except ImportError:
        # Better to skip persisting than to silently shred the documentation.
        raise SystemExit(
            "ruamel.yaml is required to write config.yaml without destroying its "
            "comments. Install it: pip install ruamel.yaml")

    y = YAML()
    y.preserve_quotes = True
    y.width = 100

    with open(path, "r", encoding="utf-8") as fh:
        data = y.load(fh)

    if "me" not in data or data["me"] is None:
        data["me"] = {}
    for key, val in me.items():
        data["me"][key] = val
    if niche:
        data["detected_niche"] = niche

    with open(path, "w", encoding="utf-8") as fh:
        y.dump(data, fh)


def env(key: str, required: bool = False) -> str | None:
    val = os.environ.get(key)
    if required and not val:
        raise SystemExit(f"missing {key}. Put it in {ROOT / '.env'} -- see SETUP.md")
    return val


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SHOT_DIR.mkdir(parents=True, exist_ok=True)
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
