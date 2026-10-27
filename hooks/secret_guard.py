#!/usr/bin/env python3
"""PreToolUse hook: block writing live-looking credentials into files.

Reads the Claude Code hook payload on stdin. For Write, Edit, MultiEdit and
NotebookEdit it scans the text that would be written. A high-confidence
credential pattern blocks the tool call with exit code 2; the reason on
stderr goes back to Claude, which should ask the user to place the secret in
a local secret store instead.

Allowed without blocking:
- files whose purpose is to hold local secrets and that the repository
  ignores: .env*, client_secret*.json, service_account*.json, *r2.config.json,
  google-api.json, backlinks-api.json under ~/.config/claude-seo;
- documented placeholders containing EXAMPLE, DUMMY, YOUR_, REDACTED, XXXX.

Set WEB_SEO_SECRET_GUARD=off to disable (for example in a test fixture repo).
Any internal error exits 0 so the guard never breaks editing.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import PurePath

PATTERNS = [
    ("Google API key", re.compile(r"AIza[0-9A-Za-z_-]{35}")),
    ("AWS access key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b|\bgithub_pat_[A-Za-z0-9_]{40,}\b")),
    ("Google OAuth client secret", re.compile(r"\bGOCSPX-[A-Za-z0-9_-]{20,}")),
    ("Anthropic API key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}")),
    ("OpenAI API key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{32,}")),
    ("Slack token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}")),
    ("Stripe live key", re.compile(r"\b[rs]k_live_[A-Za-z0-9]{16,}")),
    ("Private key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |ENCRYPTED )?PRIVATE KEY-----\s*\n?\s*[A-Za-z0-9+/=]{40,}")),
    ("Webhook URL", re.compile(r"https://hooks\.slack\.com/services/T[A-Z0-9]+/B[A-Z0-9]+/[A-Za-z0-9]{20,}")),
]
PLACEHOLDER = re.compile(r"EXAMPLE|DUMMY|YOUR_|REDACTED|XXXX|<[A-Z_]+>", re.I)
SECRET_STORE_NAMES = re.compile(
    r"^(?:\.env(?:\..+)?|client_secret.*\.json|service_account.*\.json|.*r2\.config\.json|"
    r"google-api\.json|backlinks-api\.json|matomo-api\.json)$",
    re.I,
)


def texts_from(tool_name: str, tool_input: dict) -> list[str]:
    if tool_name == "Write":
        return [str(tool_input.get("content", ""))]
    if tool_name == "Edit":
        return [str(tool_input.get("new_string", ""))]
    if tool_name == "MultiEdit":
        return [str(e.get("new_string", "")) for e in tool_input.get("edits") or []]
    if tool_name == "NotebookEdit":
        return [str(tool_input.get("new_source", ""))]
    return []


def find_secrets(text: str) -> list[str]:
    hits = []
    for label, pattern in PATTERNS:
        for match in pattern.finditer(text):
            line_start = text.rfind("\n", 0, match.start()) + 1
            line_end = text.find("\n", match.end())
            line = text[line_start: line_end if line_end != -1 else len(text)]
            if PLACEHOLDER.search(match.group(0)) or PLACEHOLDER.search(line):
                continue
            hits.append(label)
            break
    return hits


def main() -> int:
    if os.environ.get("WEB_SEO_SECRET_GUARD", "").lower() in ("off", "0", "false"):
        return 0
    try:
        payload = json.load(sys.stdin)
    except (ValueError, OSError):
        return 0
    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input") or {}
    path = str(tool_input.get("file_path") or tool_input.get("notebook_path") or "")
    if path and SECRET_STORE_NAMES.match(PurePath(path).name):
        return 0
    hits = sorted({h for t in texts_from(tool_name, tool_input) for h in find_secrets(t)})
    if not hits:
        return 0
    sys.stderr.write(
        f"Web-SEO secret guard blocked this {tool_name} on {path or 'a file'}: it contains what looks like "
        f"a live credential ({', '.join(hits)}). Do not write credentials into project files, reports or "
        "skill output. Ask the user to put the value in a local secret store (an ignored .env file, "
        "~/.config/claude-seo/*.json, or the OS keychain) and reference it by name. If this is a "
        "documented placeholder, mark it with EXAMPLE or REDACTED.\n"
    )
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001 - a guard bug must never block editing
        sys.exit(0)
