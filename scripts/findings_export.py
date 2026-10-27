#!/usr/bin/env python3
"""
Validate, convert and diff SEO findings in the Web-SEO findings format.

The findings format (enterprise/schemas/findings.schema.json) is the hand-off
between audits and the rest of an organization: issue trackers, chat
channels, portfolio roll-ups and regression tracking. This script never sends
anything over the network; it writes files or prints payloads that a person
or an approved step then imports or posts.

Usage:
    findings_export.py validate findings.json
    findings_export.py convert findings.json --to jira|linear|github|csv|markdown|slack|teams [--out file]
        [--min-severity low] [--top 10] [--labels seo,web]
    findings_export.py diff old.json new.json [--out diff.json]
    findings_export.py template [--out findings.json]

Output: JSON status on stdout (or the converted payload when --out is omitted).
Exit 0 on success, 1 on validation errors or bad input.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import io
import json
import re
import sys
from pathlib import Path

SEVERITIES = ("critical", "high", "medium", "low", "info")
SEV_RANK = {s: i for i, s in enumerate(SEVERITIES)}
STATUSES = ("open", "in_progress", "fixed", "wont_fix", "accepted_risk")
JIRA_PRIORITY = {"critical": "Highest", "high": "High", "medium": "Medium", "low": "Low", "info": "Lowest"}
LINEAR_PRIORITY = {"critical": 1, "high": 2, "medium": 3, "low": 4, "info": 0}
FORMULA_PREFIX = ("=", "+", "-", "@", "\t", "\r")


def finding_key(f: dict) -> str:
    """Stable identity: explicit id, else a hash of category + title + url."""
    if f.get("id"):
        return str(f["id"])
    raw = "|".join(str(f.get(k, "")).strip().lower() for k in ("category", "title", "url"))
    return "F-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:10]


def load(path: str) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, list):
        data = {"site": "", "findings": data}
    return data


def validate(doc: dict) -> list[str]:
    errors = []
    if not isinstance(doc.get("findings"), list):
        return ["findings must be a list"]
    if "health_score" in doc and doc["health_score"] is not None:
        hs = doc["health_score"]
        if not isinstance(hs, (int, float)) or not 0 <= hs <= 100:
            errors.append("health_score must be 0-100")
    seen = set()
    for i, f in enumerate(doc["findings"]):
        where = f"findings[{i}]"
        if not str(f.get("title", "")).strip():
            errors.append(f"{where}: title is required")
        if f.get("severity") not in SEVERITIES:
            errors.append(f"{where}: severity must be one of {', '.join(SEVERITIES)}")
        if f.get("status", "open") not in STATUSES:
            errors.append(f"{where}: status must be one of {', '.join(STATUSES)}")
        key = finding_key(f)
        if key in seen:
            errors.append(f"{where}: duplicate finding {key}")
        seen.add(key)
    return errors


def _cell(value: object) -> str:
    """Neutralize spreadsheet formula injection in exported cells."""
    text = "" if value is None else str(value)
    return "'" + text if text.startswith(FORMULA_PREFIX) else text


def _filtered(doc: dict, min_severity: str, top: int | None) -> list[dict]:
    limit = SEV_RANK[min_severity]
    items = [f for f in doc["findings"] if SEV_RANK.get(f.get("severity"), 99) <= limit
             and f.get("status", "open") in ("open", "in_progress")]
    items.sort(key=lambda f: (SEV_RANK[f["severity"]], str(f.get("title", ""))))
    return items[:top] if top else items


def _body(f: dict, doc: dict) -> str:
    parts = [f.get("description", "").strip()]
    if f.get("url"):
        parts.append(f"URL: {f['url']}")
    if f.get("recommendation"):
        parts.append(f"Recommendation: {f['recommendation']}")
    if f.get("evidence"):
        parts.append(f"Evidence: {f['evidence']}")
    if f.get("verification"):
        parts.append(f"How we will know it worked: {f['verification']}")
    parts.append(f"Finding ID: {finding_key(f)} | Site: {doc.get('site', '')} | Source: {doc.get('source', 'web-seo')}")
    return "\n\n".join(p for p in parts if p)


def _csv(rows: list[list[object]]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    for row in rows:
        writer.writerow([_cell(c) for c in row])
    return buf.getvalue()


def convert(doc: dict, target: str, min_severity: str = "info", top: int | None = None,
            labels: list[str] | None = None) -> str:
    items = _filtered(doc, min_severity, top)
    labels = labels or ["seo"]
    if target == "jira":
        rows = [["Summary", "Description", "Issue Type", "Priority", "Labels"]]
        rows += [[f["title"], _body(f, doc), "Task", JIRA_PRIORITY[f["severity"]],
                  " ".join(labels + [str(f.get("category", "")).replace(" ", "-")]).strip()] for f in items]
        return _csv(rows)
    if target == "linear":
        rows = [["Title", "Description", "Priority", "Labels"]]
        rows += [[f["title"], _body(f, doc), LINEAR_PRIORITY[f["severity"]],
                  ",".join(labels + [str(f.get("category", ""))]).strip(",")] for f in items]
        return _csv(rows)
    if target == "csv":
        rows = [["id", "severity", "category", "title", "url", "recommendation", "status", "owner", "effort"]]
        rows += [[finding_key(f), f["severity"], f.get("category", ""), f["title"], f.get("url", ""),
                  f.get("recommendation", ""), f.get("status", "open"), f.get("owner", ""), f.get("effort", "")]
                 for f in items]
        return _csv(rows)
    if target == "github":
        issues = [{"title": f"[{f['severity'].upper()}] {f['title']}", "body": _body(f, doc),
                   "labels": labels + [f"severity:{f['severity']}"] + ([f["category"]] if f.get("category") else [])}
                  for f in items]
        return json.dumps(issues, indent=2, ensure_ascii=False)
    counts = {s: sum(1 for f in doc["findings"] if f.get("severity") == s) for s in SEVERITIES}
    score = doc.get("health_score")
    headline = f"SEO findings for {doc.get('site') or 'site'}" + (f": health score {score}/100" if score is not None else "")
    if target == "markdown":
        lines = [f"# {headline}", "", "| Severity | Count |", "|---|---|"]
        lines += [f"| {s} | {counts[s]} |" for s in SEVERITIES]
        lines += ["", "| ID | Severity | Title | URL | Recommendation |", "|---|---|---|---|---|"]
        esc = lambda v: str(v or "").replace("|", "\\|").replace("\n", " ")  # noqa: E731
        lines += [f"| {finding_key(f)} | {f['severity']} | {esc(f['title'])} | {esc(f.get('url'))} | "
                  f"{esc(f.get('recommendation'))} |" for f in items]
        return "\n".join(lines) + "\n"
    summary = ", ".join(f"{counts[s]} {s}" for s in SEVERITIES if counts[s])
    top_lines = [f"{f['severity'].upper()}: {f['title']}" for f in items[: top or 5]]
    if target == "slack":
        return json.dumps({"text": headline, "blocks": [
            {"type": "header", "text": {"type": "plain_text", "text": headline[:150]}},
            {"type": "section", "text": {"type": "mrkdwn", "text": summary or "No findings"}},
            {"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(f"- {t}" for t in top_lines) or "-"}},
        ]}, indent=2, ensure_ascii=False)
    if target == "teams":
        return json.dumps({"type": "message", "attachments": [{
            "contentType": "application/vnd.microsoft.card.adaptive",
            "content": {"$schema": "http://adaptivecards.io/schemas/adaptive-card.json", "type": "AdaptiveCard",
                        "version": "1.4", "body": [
                            {"type": "TextBlock", "size": "Large", "weight": "Bolder", "text": headline, "wrap": True},
                            {"type": "TextBlock", "text": summary or "No findings", "wrap": True},
                            *[{"type": "TextBlock", "text": t, "wrap": True} for t in top_lines]]}}]},
            indent=2, ensure_ascii=False)
    raise ValueError(f"unknown target {target}")


def diff(old: dict, new: dict) -> dict:
    """Classify findings as new, resolved, persisting or changed severity."""
    o = {finding_key(f): f for f in old["findings"]}
    n = {finding_key(f): f for f in new["findings"]}
    persisting = [k for k in n if k in o]
    changed = [{"id": k, "from": o[k].get("severity"), "to": n[k].get("severity")}
               for k in persisting if o[k].get("severity") != n[k].get("severity")]
    regressions = [c for c in changed if SEV_RANK.get(c["to"], 9) < SEV_RANK.get(c["from"], 9)]
    return {
        "site": new.get("site") or old.get("site"),
        "from": old.get("generated_at"), "to": new.get("generated_at"),
        "health_score": {"from": old.get("health_score"), "to": new.get("health_score")},
        "new": [n[k] for k in n if k not in o],
        "resolved": [o[k] for k in o if k not in n],
        "persisting": len(persisting),
        "severity_changed": changed,
        "regression": bool(regressions) or any(
            n[k].get("severity") in ("critical", "high") for k in n if k not in o),
    }


TEMPLATE = {
    "schema_version": 1,
    "site": "example.com",
    "client": "example-client",
    "source": "web-seo /seo audit",
    "generated_at": "2026-01-01T00:00:00+00:00",
    "health_score": 72,
    "findings": [{
        "id": "TECH-001",
        "title": "Canonical tags point to non-indexable URLs",
        "severity": "high",
        "category": "technical",
        "url": "https://example.com/products/",
        "description": "12 product pages declare a canonical that returns 404.",
        "recommendation": "Point each canonical at the live, indexable product URL.",
        "evidence": "Crawl sample of 50 product URLs",
        "verification": "URL Inspection shows the declared canonical selected by Google",
        "effort": "S",
        "owner": "",
        "status": "open",
    }],
}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    sub = ap.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("validate")
    v.add_argument("file")
    c = sub.add_parser("convert")
    c.add_argument("file")
    c.add_argument("--to", required=True, choices=["jira", "linear", "github", "csv", "markdown", "slack", "teams"])
    c.add_argument("--out")
    c.add_argument("--min-severity", choices=SEVERITIES, default="info")
    c.add_argument("--top", type=int)
    c.add_argument("--labels", default="seo")
    d = sub.add_parser("diff")
    d.add_argument("old")
    d.add_argument("new")
    d.add_argument("--out")
    t = sub.add_parser("template")
    t.add_argument("--out")
    args = ap.parse_args(argv)

    try:
        if args.cmd == "template":
            text = json.dumps(dict(TEMPLATE, generated_at=dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()),
                              indent=2)
            if args.out:
                Path(args.out).write_text(text + "\n", encoding="utf-8")
                print(json.dumps({"status": "PASS", "out": args.out}))
            else:
                print(text)
            return 0
        if args.cmd == "diff":
            old, new = load(args.old), load(args.new)
            errors = validate(old) + validate(new)
            if errors:
                print(json.dumps({"status": "ERROR", "errors": errors}))
                return 1
            result = diff(old, new)
            if args.out:
                Path(args.out).write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            print(json.dumps({"status": "PASS", "new": len(result["new"]), "resolved": len(result["resolved"]),
                              "persisting": result["persisting"], "regression": result["regression"],
                              **({"out": args.out} if args.out else {"diff": result})}, ensure_ascii=False))
            return 0
        doc = load(args.file)
        errors = validate(doc)
        if args.cmd == "validate" or errors:
            print(json.dumps({"status": "ERROR" if errors else "PASS", "errors": errors,
                              "findings": len(doc.get("findings") or [])}))
            return 1 if errors else 0
        labels = [x.strip() for x in re.split(r"[,\s]+", args.labels) if x.strip()]
        text = convert(doc, args.to, args.min_severity, args.top, labels)
        if args.out:
            Path(args.out).write_text(text, encoding="utf-8", newline="")
            print(json.dumps({"status": "PASS", "target": args.to, "out": args.out,
                              "exported": len(_filtered(doc, args.min_severity, args.top))}))
        else:
            sys.stdout.write(text)
        return 0
    except (OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "ERROR", "errors": [str(exc)]}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
