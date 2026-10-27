#!/usr/bin/env python3
"""PostToolUse hook: record tool activity in the Web-SEO audit log (opt-in).

Logging is off unless one of these is true:
- the environment sets WEB_SEO_AUDIT=1, or
- the project has seo-workspace/org-policy.json (or $WEB_SEO_HOME/org-policy.json)
  with "audit": {"enabled": true}.

Each record holds the tool name, session id, and a short redacted target: the
file path for edits, the URL for fetches, or the first 300 characters of a
shell command. File contents, tool output and fetched pages are never logged.
The hook never blocks: every outcome exits 0.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

HOOK_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(HOOK_DIR.parent / "scripts"))


def _enabled(root: Path) -> bool:
    if os.environ.get("WEB_SEO_AUDIT", "") == "1":
        return True
    policy = root / "org-policy.json"
    try:
        return bool(json.loads(policy.read_text(encoding="utf-8")).get("audit", {}).get("enabled"))
    except (OSError, ValueError):
        return False


def _target(tool_name: str, tool_input: dict) -> str:
    for key in ("file_path", "notebook_path", "url", "pattern", "path"):
        if tool_input.get(key):
            return str(tool_input[key])[:500]
    if tool_name == "Bash":
        return str(tool_input.get("command", ""))[:300]
    return ""


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (ValueError, OSError):
        return 0
    cwd = Path(payload.get("cwd") or os.getcwd())
    home = os.environ.get("WEB_SEO_HOME")
    root = Path(home) if home else cwd / "seo-workspace"
    if not _enabled(root):
        return 0
    from audit_log import append_event  # imported lazily: most calls stop above

    tool_name = str(payload.get("tool_name", ""))
    tool_input = payload.get("tool_input") or {}
    response = payload.get("tool_response")
    ok = not (isinstance(response, dict) and (response.get("is_error") or response.get("error")))
    append_event(
        root,
        "tool." + tool_name,
        actor="claude",
        detail={"session": str(payload.get("session_id", ""))[:64], "target": _target(tool_name, tool_input),
                "ok": ok},
    )
    return 0


if __name__ == "__main__":
    try:
        main()
    except Exception:  # noqa: BLE001 - auditing must never break a tool call
        pass
    sys.exit(0)
