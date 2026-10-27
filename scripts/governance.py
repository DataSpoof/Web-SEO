#!/usr/bin/env python3
"""
Organization policy and authorization ledger for Web-SEO consequential actions.

The org policy (<root>/org-policy.json) sets, per action, whether it is
allowed, needs a recorded approval, or is denied. Approvals are recorded in a
per-client, hash-chained ledger (clients/<slug>/authorizations/ledger.jsonl)
that names the approver alias, scope, count limit and expiry. `check` decides
whether an action may proceed now; `consume` records each use.

This is a record of decisions the user made in chat. It makes approvals
explicit and auditable; it is not an access-control system on its own.

Usage:
    governance.py policy init [--org-name "Acme Agency"]
    governance.py policy show
    governance.py actions
    governance.py check --client acme --action directory_submission [--cost-usd 4.5] [--count 1]
    governance.py authorize --client acme --action directory_submission --approver alice
        --scope "Pilot of 10 AI-tool directories from batch B03" [--max-count 10] [--expires-days 7]
        [--reference "chat message 2026-10-26 14:02"]
    governance.py consume --client acme --auth-id AUTH-... [--count 1] [--note "submitted to x.example"]
    governance.py revoke --client acme --auth-id AUTH-... [--reason "..."]
    governance.py ledger --client acme [--verify]

Output: JSON on stdout. `check` exits 0 = allow, 3 = needs approval, 4 = denied.
Other commands exit 0 on success and 1 on error.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import secrets
import sys
from pathlib import Path

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPTS_DIR)

from audit_log import GENESIS, append_event, read_records, redact, workspace_root  # noqa: E402

EXIT_ALLOW, EXIT_APPROVAL, EXIT_DENY = 0, 3, 4

# Default modes. "allow" proceeds, "require_approval" needs a live ledger
# authorization, "deny" can never proceed through Claude.
ACTIONS: dict[str, dict] = {
    "read_only_analysis": {"mode": "allow", "about": "Fetch public pages, run audits, read exports"},
    "paid_api_call": {"mode": "allow", "about": "DataForSEO, Moz, Ahrefs and other metered APIs (budget-capped)"},
    "google_api_write": {"mode": "require_approval", "about": "Indexing API notify, sitemap submit, GSC changes"},
    "indexnow_submit": {"mode": "require_approval", "about": "IndexNow URL submission"},
    "account_registration": {"mode": "require_approval", "about": "Create an account on a directory or platform"},
    "account_login": {"mode": "require_approval", "about": "Log in with an already authorized account"},
    "directory_submission": {"mode": "require_approval", "about": "Submit a product listing form"},
    "outreach_email": {"mode": "require_approval", "about": "One-to-one email to an official contact"},
    "image_host_upload": {"mode": "require_approval", "about": "Upload final article images to a public host"},
    "file_write_client_site": {"mode": "require_approval", "about": "Edit files in the client's site repository"},
    "content_publish": {"mode": "deny", "about": "Publishing on blogs or platforms is human-only"},
    "payment": {"mode": "deny", "about": "Any payment, paid listing or subscription"},
    "reciprocal_link": {"mode": "deny", "about": "Adding a forced backlink or badge"},
    "captcha_bypass": {"mode": "deny", "about": "Bypassing CAPTCHA, 2FA or verification"},
    "mass_outreach": {"mode": "deny", "about": "Bulk, CC/BCC or automated follow-up email"},
}

DEFAULT_POLICY = {
    "schema_version": 1,
    "org_name": "",
    "actions": {name: {"mode": spec["mode"]} for name, spec in ACTIONS.items()},
    "budgets": {"per_run_usd": 10.0, "monthly_usd": 200.0},
    "approvals": {"max_validity_days": 30, "require_known_approver": True},
    "data": {"retention_days": 365, "store_raw_html": False, "redact_pii_in_logs": True},
    "audit": {"enabled": True},
    "prohibited_tactics": [
        "link schemes or paid links that pass ranking signals",
        "invented personas, reviews, credentials or facts",
        "doorway or scaled low-value pages",
        "cloaking or sneaky redirects",
        "bypassing site rate limits, robots.txt or terms of service",
    ],
}
MODES = ("allow", "require_approval", "deny")
MODE_RANK = {m: i for i, m in enumerate(MODES)}


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def policy_path(root: Path) -> Path:
    return root / "org-policy.json"


def load_policy(root: Path) -> dict:
    path = policy_path(root)
    if not path.is_file():
        return json.loads(json.dumps(DEFAULT_POLICY))
    policy = json.loads(path.read_text(encoding="utf-8"))
    merged = json.loads(json.dumps(DEFAULT_POLICY))
    for key, value in policy.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key].update(value)
        else:
            merged[key] = value
    return merged


def validate_policy(policy: dict) -> list[str]:
    errors = []
    for name, spec in (policy.get("actions") or {}).items():
        if name not in ACTIONS:
            errors.append(f"unknown action {name!r}")
        elif spec.get("mode") not in MODES:
            errors.append(f"action {name}: mode must be one of {', '.join(MODES)}")
    for name in ("content_publish", "payment", "captcha_bypass"):
        if (policy.get("actions") or {}).get(name, {}).get("mode") != "deny":
            errors.append(f"action {name} must stay 'deny'; it is never delegated to Claude")
    return errors


def client_dir(root: Path, slug: str) -> Path:
    path = root / "clients" / slug
    if not (path / "client.json").is_file():
        raise FileNotFoundError(f"no client '{slug}' (run workspace.py add-client first)")
    return path


def ledger_path(root: Path, slug: str) -> Path:
    return client_dir(root, slug) / "authorizations" / "ledger.jsonl"


def _digest(record: dict) -> str:
    body = {k: v for k, v in record.items() if k != "hash"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _append(root: Path, slug: str, record: dict) -> dict:
    path = ledger_path(root, slug)
    path.parent.mkdir(parents=True, exist_ok=True)
    prev = GENESIS
    for rec in read_records(path):
        prev = rec.get("hash", GENESIS)
    record = dict(record, prev_hash=prev)
    record["hash"] = _digest(record)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def verify_ledger(root: Path, slug: str) -> dict:
    prev, n = GENESIS, 0
    for n, rec in enumerate(read_records(ledger_path(root, slug)), 1):
        if rec.get("prev_hash") != prev or _digest(rec) != rec.get("hash"):
            return {"status": "BROKEN", "at_record": n}
        prev = rec["hash"]
    return {"status": "PASS", "records": n}


def authorizations(root: Path, slug: str) -> dict[str, dict]:
    """Fold the ledger into the current state of every authorization."""
    state: dict[str, dict] = {}
    for rec in read_records(ledger_path(root, slug)):
        kind, auth_id = rec.get("type"), rec.get("auth_id")
        if kind == "grant":
            state[auth_id] = dict(rec, used=0, revoked=False)
        elif auth_id in state and kind == "consume":
            state[auth_id]["used"] += int(rec.get("count", 1))
        elif auth_id in state and kind == "revoke":
            state[auth_id]["revoked"] = True
    return state


def _live(auth: dict, now: dt.datetime) -> bool:
    if auth["revoked"] or dt.datetime.fromisoformat(auth["expires_at"]) <= now:
        return False
    return auth.get("max_count") is None or auth["used"] < auth["max_count"]


def effective_mode(policy: dict, client: dict, action: str) -> str:
    mode = (policy.get("actions") or {}).get(action, {}).get("mode", ACTIONS[action]["mode"])
    override = (client.get("action_overrides") or {}).get(action)
    # A client profile may tighten the org policy, never loosen it.
    if override in MODES and MODE_RANK[override] > MODE_RANK[mode]:
        mode = override
    return mode


def month_to_date_spend(root: Path, slug: str) -> float:
    """Sum governance.spend events for this client in the current UTC month."""
    month = _now().strftime("%Y-%m")
    total = 0.0
    for rec in read_records(root / "audit" / "audit-log.jsonl"):
        if rec.get("event") == "governance.spend" and rec.get("client") == slug and rec.get("ts", "").startswith(month):
            total += float((rec.get("detail") or {}).get("usd", 0) or 0)
    return round(total, 4)


def check(root: Path, slug: str, action: str, cost_usd: float = 0.0, count: int = 1) -> tuple[dict, int]:
    if action not in ACTIONS:
        return {"decision": "deny", "reasons": [f"unknown action {action!r}"]}, EXIT_DENY
    policy = load_policy(root)
    client = json.loads((client_dir(root, slug) / "client.json").read_text(encoding="utf-8"))
    mode = effective_mode(policy, client, action)
    reasons = [f"policy mode for {action} is {mode}"]
    if mode == "deny":
        return {"decision": "deny", "action": action, "client": slug, "reasons": reasons}, EXIT_DENY

    budgets = policy.get("budgets") or {}
    per_run = float(budgets.get("per_run_usd", 0) or 0)
    if cost_usd > 0 and per_run and cost_usd > per_run:
        reasons.append(f"cost ${cost_usd:.2f} is above the per-run budget ${per_run:.2f}")
        mode = "require_approval"
    caps = [float(v) for v in (budgets.get("monthly_usd"),
                               (client.get("budgets") or {}).get("monthly_api_usd")) if v]
    if cost_usd > 0 and caps:
        spent = month_to_date_spend(root, slug)
        if spent + cost_usd > min(caps):
            reasons.append(f"month-to-date spend ${spent:.2f} + ${cost_usd:.2f} exceeds the monthly cap ${min(caps):.2f}")
            mode = "require_approval"

    if mode == "allow":
        return {"decision": "allow", "action": action, "client": slug, "reasons": reasons}, EXIT_ALLOW

    now = _now()
    for auth in authorizations(root, slug).values():
        if auth.get("action") != action or not _live(auth, now):
            continue
        remaining = None if auth.get("max_count") is None else auth["max_count"] - auth["used"]
        if remaining is not None and remaining < count:
            continue
        if auth.get("max_cost_usd") is not None and cost_usd > auth["max_cost_usd"]:
            continue
        reasons.append(f"covered by {auth['auth_id']} approved by {auth['approver']}")
        return {"decision": "allow", "action": action, "client": slug, "auth_id": auth["auth_id"],
                "remaining": remaining, "expires_at": auth["expires_at"], "scope": auth["scope"],
                "reasons": reasons}, EXIT_ALLOW
    reasons.append("no live authorization covers this action; ask the user, then run authorize")
    return {"decision": "needs_approval", "action": action, "client": slug, "reasons": reasons}, EXIT_APPROVAL


def authorize(root: Path, args: argparse.Namespace) -> tuple[dict, int]:
    if args.action not in ACTIONS:
        return {"status": "ERROR", "errors": [f"unknown action {args.action!r}"]}, 1
    policy = load_policy(root)
    client = json.loads((client_dir(root, args.client) / "client.json").read_text(encoding="utf-8"))
    if effective_mode(policy, client, args.action) == "deny":
        return {"status": "ERROR", "errors": [f"{args.action} is denied by policy and cannot be authorized"]}, 1
    max_days = int((policy.get("approvals") or {}).get("max_validity_days", 30))
    if not 1 <= args.expires_days <= max_days:
        return {"status": "ERROR", "errors": [f"--expires-days must be 1..{max_days}"]}, 1
    approvers = (client.get("contacts") or {}).get("approver_aliases") or []
    if (policy.get("approvals") or {}).get("require_known_approver", True) and args.approver not in approvers:
        return {"status": "ERROR", "errors": [
            f"approver {args.approver!r} is not in the client's approver_aliases {approvers}"]}, 1
    if not args.scope.strip():
        return {"status": "ERROR", "errors": ["--scope is required"]}, 1
    now = _now()
    record = {
        "type": "grant",
        "auth_id": f"AUTH-{now.strftime('%Y%m%d')}-{secrets.token_hex(3).upper()}",
        "action": args.action,
        "approver": args.approver,
        "scope": redact(args.scope),
        "max_count": args.max_count,
        "max_cost_usd": args.max_cost_usd,
        "reference": redact(args.reference or ""),
        "created_at": now.isoformat(),
        "expires_at": (now + dt.timedelta(days=args.expires_days)).isoformat(),
    }
    rec = _append(root, args.client, record)
    append_event(root, "governance.authorize", actor=args.approver, client=args.client,
                 detail={"auth_id": rec["auth_id"], "action": args.action, "max_count": args.max_count})
    return {"status": "PASS", "authorization": rec}, 0


def consume(root: Path, args: argparse.Namespace) -> tuple[dict, int]:
    state = authorizations(root, args.client)
    auth = state.get(args.auth_id)
    if not auth:
        return {"status": "ERROR", "errors": [f"unknown authorization {args.auth_id}"]}, 1
    if not _live(auth, _now()):
        return {"status": "ERROR", "errors": [f"{args.auth_id} is expired, revoked or used up"]}, 1
    if auth.get("max_count") is not None and auth["used"] + args.count > auth["max_count"]:
        return {"status": "ERROR", "errors": [f"{args.auth_id} has {auth['max_count'] - auth['used']} uses left"]}, 1
    rec = _append(root, args.client, {"type": "consume", "auth_id": args.auth_id, "count": args.count,
                                       "note": redact(args.note or ""), "at": _now().isoformat()})
    append_event(root, "governance.consume", client=args.client,
                 detail={"auth_id": args.auth_id, "count": args.count, "note": args.note or ""})
    return {"status": "PASS", "record": rec}, 0


def revoke(root: Path, args: argparse.Namespace) -> tuple[dict, int]:
    if args.auth_id not in authorizations(root, args.client):
        return {"status": "ERROR", "errors": [f"unknown authorization {args.auth_id}"]}, 1
    rec = _append(root, args.client, {"type": "revoke", "auth_id": args.auth_id,
                                       "reason": redact(args.reason or ""), "at": _now().isoformat()})
    append_event(root, "governance.revoke", client=args.client, detail={"auth_id": args.auth_id})
    return {"status": "PASS", "record": rec}, 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--root", help="workspace root (default $WEB_SEO_HOME or ./seo-workspace)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    pol = sub.add_parser("policy")
    pol_sub = pol.add_subparsers(dest="policy_cmd", required=True)
    pinit = pol_sub.add_parser("init")
    pinit.add_argument("--org-name", default="")
    pinit.add_argument("--force", action="store_true")
    pol_sub.add_parser("show")
    pol_sub.add_parser("validate")
    sub.add_parser("actions")
    c = sub.add_parser("check")
    c.add_argument("--client", required=True)
    c.add_argument("--action", required=True)
    c.add_argument("--cost-usd", type=float, default=0.0)
    c.add_argument("--count", type=int, default=1)
    a = sub.add_parser("authorize")
    a.add_argument("--client", required=True)
    a.add_argument("--action", required=True)
    a.add_argument("--approver", required=True)
    a.add_argument("--scope", required=True)
    a.add_argument("--max-count", type=int)
    a.add_argument("--max-cost-usd", type=float)
    a.add_argument("--expires-days", type=int, default=7)
    a.add_argument("--reference")
    u = sub.add_parser("consume")
    u.add_argument("--client", required=True)
    u.add_argument("--auth-id", required=True)
    u.add_argument("--count", type=int, default=1)
    u.add_argument("--note")
    r = sub.add_parser("revoke")
    r.add_argument("--client", required=True)
    r.add_argument("--auth-id", required=True)
    r.add_argument("--reason")
    sp = sub.add_parser("spend")
    sp.add_argument("--client", required=True)
    sp.add_argument("--usd", type=float, required=True)
    sp.add_argument("--source", default="api", help="e.g. dataforseo, moz, ahrefs")
    sp.add_argument("--note")
    lg = sub.add_parser("ledger")
    lg.add_argument("--client", required=True)
    lg.add_argument("--verify", action="store_true")
    args = ap.parse_args(argv)
    root = workspace_root(args.root)

    try:
        if args.cmd == "policy":
            if args.policy_cmd == "init":
                path = policy_path(root)
                if path.exists() and not args.force:
                    result, code = {"status": "ERROR", "errors": [f"{path} exists (use --force)"]}, 1
                else:
                    root.mkdir(parents=True, exist_ok=True)
                    policy = dict(json.loads(json.dumps(DEFAULT_POLICY)), org_name=args.org_name)
                    path.write_text(json.dumps(policy, indent=2) + "\n", encoding="utf-8")
                    append_event(root, "governance.policy_init")
                    result, code = {"status": "PASS", "path": str(path)}, 0
            elif args.policy_cmd == "show":
                result, code = {"status": "PASS", "policy": load_policy(root)}, 0
            else:
                errors = validate_policy(load_policy(root))
                result, code = {"status": "ERROR" if errors else "PASS", "errors": errors}, 1 if errors else 0
        elif args.cmd == "actions":
            policy = load_policy(root)
            result = {"status": "PASS", "actions": {
                name: {"mode": policy["actions"].get(name, {}).get("mode", spec["mode"]), "about": spec["about"]}
                for name, spec in ACTIONS.items()}}
            code = 0
        elif args.cmd == "check":
            result, code = check(root, args.client, args.action, args.cost_usd, args.count)
            append_event(root, "governance.check", client=args.client,
                         detail={"action": args.action, "decision": result["decision"]})
        elif args.cmd == "authorize":
            result, code = authorize(root, args)
        elif args.cmd == "consume":
            result, code = consume(root, args)
        elif args.cmd == "revoke":
            result, code = revoke(root, args)
        elif args.cmd == "spend":
            client_dir(root, args.client)
            if args.usd < 0:
                result, code = {"status": "ERROR", "errors": ["--usd must be >= 0"]}, 1
            else:
                append_event(root, "governance.spend", client=args.client,
                             detail={"usd": args.usd, "source": args.source, "note": args.note or ""})
                result = {"status": "PASS", "month_to_date_usd": month_to_date_spend(root, args.client)}
                code = 0
        else:
            if args.verify:
                result = verify_ledger(root, args.client)
                code = 0 if result["status"] == "PASS" else 1
            else:
                result, code = {"status": "PASS", "authorizations": list(authorizations(root, args.client).values())}, 0
    except FileNotFoundError as exc:
        result, code = {"status": "ERROR", "errors": [str(exc)]}, 1
    print(json.dumps(result, ensure_ascii=False))
    return code


if __name__ == "__main__":
    sys.exit(main())
