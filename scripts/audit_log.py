#!/usr/bin/env python3
"""
Tamper-evident, hash-chained audit log for Web-SEO enterprise workspaces.

Every record carries the SHA-256 of the previous record, so editing or
deleting a line breaks the chain and `verify` reports where. Values that look
like credentials or email addresses are redacted before they are written.

The log lives at <workspace>/audit/audit-log.jsonl. The workspace root is
--root, else $WEB_SEO_HOME, else ./seo-workspace.

Usage:
    audit_log.py append --event audit.run --actor alice --client acme --detail '{"url": "https://acme.example"}'
    audit_log.py verify
    audit_log.py query [--client acme] [--event audit.run] [--since 2026-10-01] [--limit 50]
    audit_log.py export --format csv|json [--out file]
    audit_log.py purge --older-than-days 365

Output: JSON on stdout. Exit 0 on success, 1 on a broken chain or bad input.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import io
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Iterator

GENESIS = "0" * 64
LOG_NAME = "audit-log.jsonl"

SECRET_PATTERNS = [
    re.compile(r"AIza[0-9A-Za-z_-]{35}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{40,}"),
    re.compile(r"GOCSPX-[A-Za-z0-9_-]{20,}"),
    re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}"),
    re.compile(r"sk-(?:proj-)?[A-Za-z0-9]{20,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"sk_live_[A-Za-z0-9]{16,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)\b(?:password|passwd|secret|token|api[_-]?key)\s*[=:]\s*\S+"),
]
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def workspace_root(root: str | None = None) -> Path:
    """Resolve the workspace root from --root, $WEB_SEO_HOME, or ./seo-workspace."""
    return Path(root or os.environ.get("WEB_SEO_HOME") or "seo-workspace").resolve()


def log_path(root: Path) -> Path:
    return root / "audit" / LOG_NAME


def redact(value: Any) -> Any:
    """Recursively redact secret-like strings and email addresses."""
    if isinstance(value, str):
        out = value
        for pat in SECRET_PATTERNS:
            out = pat.sub("[REDACTED]", out)
        return EMAIL_RE.sub("[EMAIL]", out)
    if isinstance(value, dict):
        return {k: redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


def _digest(record: dict) -> str:
    body = {k: v for k, v in record.items() if k != "hash"}
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


class _Lock:
    """Cross-platform advisory lock using an exclusive lock file."""

    def __init__(self, path: Path, timeout: float = 10.0):
        self.path = path.with_suffix(path.suffix + ".lock")
        self.timeout = timeout

    def __enter__(self) -> "_Lock":
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(fd)
                return self
            except FileExistsError:
                # A lock older than 60 s is from a crashed writer.
                try:
                    if time.time() - self.path.stat().st_mtime > 60:
                        self.path.unlink(missing_ok=True)
                        continue
                except OSError:
                    pass
                if time.monotonic() > deadline:
                    raise TimeoutError(f"audit log is locked: {self.path}")
                time.sleep(0.05)

    def __exit__(self, *exc: object) -> None:
        self.path.unlink(missing_ok=True)


def read_records(path: Path) -> Iterator[dict]:
    if not path.exists():
        return
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)


def _last_hash(path: Path) -> tuple[str, int]:
    last, count = GENESIS, 0
    for rec in read_records(path):
        last, count = rec.get("hash", GENESIS), count + 1
    return last, count


def append_event(root: Path, event: str, actor: str = "claude", client: str | None = None,
                 detail: dict | None = None) -> dict:
    """Append one redacted, chained record and return it."""
    path = log_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _Lock(path):
        prev, count = _last_hash(path)
        record = {
            "seq": count + 1,
            "ts": _now(),
            "event": event,
            "actor": redact(actor),
            "client": client,
            "detail": redact(detail or {}),
            "prev_hash": prev,
        }
        record["hash"] = _digest(record)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def verify(root: Path) -> dict:
    path = log_path(root)
    prev, n = None, 0
    for n, rec in enumerate(read_records(path), 1):
        if prev is None:
            # The first record is the genesis or a purge anchor.
            if rec.get("prev_hash") != GENESIS and rec.get("event") != "audit.purge":
                return {"status": "BROKEN", "at_seq": rec.get("seq"), "reason": "first record has no genesis"}
        elif rec.get("prev_hash") != prev:
            return {"status": "BROKEN", "at_seq": rec.get("seq"), "reason": "prev_hash mismatch"}
        if _digest(rec) != rec.get("hash"):
            return {"status": "BROKEN", "at_seq": rec.get("seq"), "reason": "record hash mismatch"}
        prev = rec["hash"]
    return {"status": "PASS", "records": n, "path": str(path)}


def query(root: Path, client: str | None = None, event: str | None = None,
          since: str | None = None, limit: int = 100) -> list[dict]:
    out = []
    for rec in read_records(log_path(root)):
        if client and rec.get("client") != client:
            continue
        if event and not str(rec.get("event", "")).startswith(event):
            continue
        if since and rec.get("ts", "") < since:
            continue
        out.append(rec)
    return out[-limit:] if limit else out


def purge(root: Path, older_than_days: int) -> dict:
    """Drop records older than the retention window and re-anchor the chain."""
    path = log_path(root)
    cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=older_than_days)).isoformat()
    with _Lock(path):
        records = list(read_records(path))
        keep = [r for r in records if r.get("ts", "") >= cutoff]
        dropped = len(records) - len(keep)
        if dropped == 0:
            return {"status": "PASS", "dropped": 0, "kept": len(keep)}
        anchor = {
            "seq": 1, "ts": _now(), "event": "audit.purge", "actor": "retention",
            "client": None,
            "detail": {"dropped": dropped, "cutoff": cutoff,
                       "last_dropped_hash": records[dropped - 1].get("hash")},
            "prev_hash": records[dropped - 1].get("hash", GENESIS),
        }
        anchor["hash"] = _digest(anchor)
        chained, prev = [anchor], anchor["hash"]
        for i, rec in enumerate(keep, 2):
            rec = dict(rec, seq=i, prev_hash=prev)
            rec["hash"] = _digest(rec)
            chained.append(rec)
            prev = rec["hash"]
        tmp = path.with_suffix(".tmp")
        tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in chained), encoding="utf-8")
        os.replace(tmp, path)
    return {"status": "PASS", "dropped": dropped, "kept": len(keep), "cutoff": cutoff}


def _csv(records: list[dict]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["seq", "ts", "event", "actor", "client", "detail", "hash"])
    for r in records:
        writer.writerow([r.get("seq"), r.get("ts"), r.get("event"), r.get("actor"), r.get("client"),
                         json.dumps(r.get("detail"), ensure_ascii=False), r.get("hash")])
    return buf.getvalue()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--root", help="workspace root (default $WEB_SEO_HOME or ./seo-workspace)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("append")
    a.add_argument("--event", required=True)
    a.add_argument("--actor", default="claude")
    a.add_argument("--client")
    a.add_argument("--detail", default="{}", help="JSON object")
    sub.add_parser("verify")
    q = sub.add_parser("query")
    q.add_argument("--client")
    q.add_argument("--event")
    q.add_argument("--since")
    q.add_argument("--limit", type=int, default=100)
    e = sub.add_parser("export")
    e.add_argument("--format", choices=["csv", "json"], default="json")
    e.add_argument("--out")
    p = sub.add_parser("purge")
    p.add_argument("--older-than-days", type=int, required=True)
    args = ap.parse_args(argv)
    root = workspace_root(args.root)

    if args.cmd == "append":
        try:
            detail = json.loads(args.detail)
            if not isinstance(detail, dict):
                raise ValueError
        except ValueError:
            print(json.dumps({"status": "ERROR", "error": "--detail must be a JSON object"}))
            return 1
        print(json.dumps(append_event(root, args.event, args.actor, args.client, detail)))
        return 0
    if args.cmd == "verify":
        result = verify(root)
        print(json.dumps(result))
        return 0 if result["status"] == "PASS" else 1
    if args.cmd == "query":
        print(json.dumps(query(root, args.client, args.event, args.since, args.limit), ensure_ascii=False))
        return 0
    if args.cmd == "export":
        records = query(root, limit=0)
        text = _csv(records) if args.format == "csv" else json.dumps(records, indent=2, ensure_ascii=False)
        if args.out:
            Path(args.out).write_text(text, encoding="utf-8")
            print(json.dumps({"status": "PASS", "records": len(records), "out": args.out}))
        else:
            sys.stdout.write(text)
        return 0
    if args.cmd == "purge":
        if args.older_than_days < 1:
            print(json.dumps({"status": "ERROR", "error": "--older-than-days must be >= 1"}))
            return 1
        print(json.dumps(purge(root, args.older_than_days)))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
