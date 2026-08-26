"""Writing, through the Claude Code CLI.

There is one engine: the `claude` command already installed and signed in on
this machine. That means drafting runs on your existing Claude subscription
with no API key anywhere in this project, and no per-post billing.

    claude -p --system-prompt <voice> --model <alias> --output-format text

The user prompt goes in on stdin rather than argv: prompts here run to a few
thousand characters, and Windows argv quoting mangles anything containing
newlines or quotes.

Notes on the flags, all of which matter:
  --system-prompt          REPLACES Claude Code's coding-agent system prompt.
                           Without this the output arrives wrapped in helpful
                           commentary about the task.
  --output-format text     plain text on stdout, nothing to parse
  --no-session-persistence don't write a session file for every comment
  --strict-mcp-config      ignore this machine's MCP servers; we want a writer,
                           not an agent with tools
  --allowedTools (none)    no tool is ever needed to write a LinkedIn comment,
                           and a tool call would block waiting for permission
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time

from . import db

# Model aliases the CLI understands. Anything else is passed through as-is so a
# full model id in config.yaml also works.
KNOWN_ALIASES = {"opus", "sonnet", "haiku", "fable"}


class LLMError(RuntimeError):
    pass


def cli_path() -> str | None:
    """Locate the claude CLI. On Windows this is usually a .cmd shim."""
    for name in ("claude", "claude.cmd", "claude.exe"):
        found = shutil.which(name)
        if found:
            return found
    return None


def available() -> dict[str, bool]:
    return {"claude_code": cli_path() is not None}


def check() -> tuple[bool, str]:
    """Is the CLI present and signed in? Used by `run.py doctor`."""
    exe = cli_path()
    if not exe:
        return False, ("the `claude` command is not on PATH. Install Claude Code and "
                       "sign in, then reopen the terminal.")
    try:
        out = subprocess.run([exe, "--version"], capture_output=True, text=True,
                             timeout=60, shell=_needs_shell(exe))
        if out.returncode != 0:
            return False, f"`claude --version` failed: {(out.stderr or '').strip()[:200]}"
        return True, (out.stdout or "").strip()
    except Exception as exc:
        return False, f"could not run the claude CLI: {type(exc).__name__}: {exc}"


def _needs_shell(exe: str) -> bool:
    """.cmd/.bat shims cannot be executed directly by CreateProcess."""
    return os.name == "nt" and exe.lower().endswith((".cmd", ".bat"))


def complete(cfg, system: str, user: str, model: str | None = None,
             max_tokens: int = 900, temperature: float = 0.8) -> str:
    """Generate text. `max_tokens` and `temperature` are accepted for call-site
    compatibility; the CLI does not expose either, and length is controlled by
    the instructions in the prompt instead."""
    exe = cli_path()
    if not exe:
        raise LLMError("the `claude` command is not on PATH -- nothing can be written. "
                       "Install Claude Code and sign in.")

    model = model or cfg.get_path("writing.comment_model", "sonnet")
    timeout = float(cfg.get_path("writing.cli_timeout_seconds", 240) or 240)

    args = [
        exe, "-p",
        "--system-prompt", system,
        "--model", model,
        "--output-format", "text",
        "--no-session-persistence",
        "--strict-mcp-config",
    ]

    last_err = None
    for attempt in range(3):
        try:
            proc = subprocess.run(
                args,
                input=user,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                shell=_needs_shell(exe),
                # Run detached from this project's directory so CLAUDE.md and the
                # repo's own context never leak into a LinkedIn post.
                cwd=os.path.expanduser("~"),
            )
            if proc.returncode != 0:
                last_err = (proc.stderr or proc.stdout or "").strip()[:400]
                # A usage limit is not going to clear in four seconds.
                if "limit" in (last_err or "").lower():
                    raise LLMError(f"claude CLI refused: {last_err}")
                time.sleep(2 ** attempt * 2)
                continue

            out = (proc.stdout or "").strip()
            if out:
                return out
            last_err = "empty response"
        except subprocess.TimeoutExpired:
            last_err = f"claude CLI timed out after {timeout:.0f}s"
        except LLMError:
            raise
        except Exception as exc:
            last_err = f"{type(exc).__name__}: {exc}"
        time.sleep(2 ** attempt * 2)

    db.log("error", "llm", f"generation failed: {last_err}")
    raise LLMError(f"claude CLI failed after 3 attempts: {last_err}")
