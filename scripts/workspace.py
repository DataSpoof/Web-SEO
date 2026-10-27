#!/usr/bin/env python3
"""
Multi-client workspace manager for Web-SEO enterprise and agency use.

A workspace keeps every client's profile, authorizations, audits, reports,
backlink workbooks, content and exports in its own folder, so work for one
client never mixes with another:

    <root>/
      org-policy.json                 (created by governance.py policy init)
      audit/audit-log.jsonl           (created by audit_log.py)
      clients/<slug>/client.json
      clients/<slug>/{audits,reports,backlinks,content,exports,baselines,authorizations}/
      archive/<slug>/                 (archived clients, never deleted)

The root is --root, else $WEB_SEO_HOME, else ./seo-workspace.

Usage:
    workspace.py init
    workspace.py add-client --name "Acme Inc" --domain acme.example [--domain shop.acme.example]
        [--competitor rival.example] [--market US:en] [--industry saas] [--approver alice]
        [--brand-color "#1e3a5f"] [--retention-days 365] [--monthly-budget-usd 100]
    workspace.py list
    workspace.py show <slug>
    workspace.py find --domain www.acme.example
    workspace.py path <slug> <area>
    workspace.py validate [<slug>]
    workspace.py archive <slug>

Output: JSON on stdout. Exit 0 on success, 1 on validation errors or bad input.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import sys
from pathlib import Path

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPTS_DIR)

from audit_log import append_event, workspace_root  # noqa: E402

AREAS = ("audits", "reports", "backlinks", "content", "exports", "baselines", "authorizations")
INDUSTRIES = ("saas", "ecommerce", "local", "publisher", "agency", "other")
SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
HOST_RE = re.compile(r"^(?=.{1,253}$)(?!-)[a-z0-9-]{1,63}(?<!-)(?:\.(?!-)[a-z0-9-]{1,63}(?<!-))+$")
COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
MARKET_RE = re.compile(r"^[A-Z]{2}:[a-z]{2,3}(?:-[A-Za-z]{2,4})?$")
ALIAS_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
SCHEMA_VERSION = 1


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug[:64].strip("-") or "client"


def normalize_host(value: str) -> str:
    host = value.strip().lower()
    host = re.sub(r"^[a-z][a-z0-9+.-]*://", "", host)
    host = host.split("/", 1)[0].split(":", 1)[0]
    return host[4:] if host.startswith("www.") else host


def clients_dir(root: Path) -> Path:
    return root / "clients"


def load_client(root: Path, slug: str) -> dict:
    path = clients_dir(root) / slug / "client.json"
    if not path.is_file():
        raise FileNotFoundError(f"no client '{slug}' in {clients_dir(root)}")
    return json.loads(path.read_text(encoding="utf-8"))


def validate_profile(profile: dict) -> list[str]:
    """Return a list of human-readable problems; empty means valid."""
    errors = []
    if profile.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION}")
    if not SLUG_RE.match(str(profile.get("slug", ""))):
        errors.append("slug must be kebab-case, 1-64 chars")
    if not str(profile.get("name", "")).strip():
        errors.append("name is required")
    domains = profile.get("domains") or []
    if not domains:
        errors.append("at least one domain is required")
    for d in [*domains, *(profile.get("competitors") or [])]:
        if not HOST_RE.match(str(d)):
            errors.append(f"invalid hostname: {d!r} (use a bare host like example.com)")
    if profile.get("primary_domain") not in domains:
        errors.append("primary_domain must be one of domains")
    for m in profile.get("markets") or []:
        if not MARKET_RE.match(f"{m.get('country', '')}:{m.get('language', '')}"):
            errors.append(f"invalid market {m!r} (use country US, language en)")
    if profile.get("industry") not in INDUSTRIES:
        errors.append(f"industry must be one of {', '.join(INDUSTRIES)}")
    brand = profile.get("brand") or {}
    for key in ("primary_color", "accent_color"):
        if brand.get(key) and not COLOR_RE.match(brand[key]):
            errors.append(f"brand.{key} must be a #rrggbb color")
    for alias in (profile.get("contacts") or {}).get("approver_aliases") or []:
        if not ALIAS_RE.match(alias) or "@" in alias:
            errors.append(f"approver alias {alias!r} must be a short alias, not an email address")
    retention = profile.get("data_retention_days")
    if not isinstance(retention, int) or retention < 1:
        errors.append("data_retention_days must be a positive integer")
    budget = (profile.get("budgets") or {}).get("monthly_api_usd")
    if not isinstance(budget, (int, float)) or budget < 0:
        errors.append("budgets.monthly_api_usd must be a non-negative number")
    return errors


def build_profile(args: argparse.Namespace) -> dict:
    domains = list(dict.fromkeys(normalize_host(d) for d in args.domain))
    markets = []
    for m in args.market or []:
        country, _, language = m.partition(":")
        markets.append({"country": country.upper(), "language": language.lower()})
    return {
        "schema_version": SCHEMA_VERSION,
        "slug": args.slug or slugify(args.name),
        "name": args.name.strip(),
        "domains": domains,
        "primary_domain": domains[0] if domains else "",
        "competitors": list(dict.fromkeys(normalize_host(c) for c in args.competitor or [])),
        "markets": markets,
        "industry": args.industry,
        "brand": {
            "display_name": args.name.strip(),
            "primary_color": args.brand_color,
            "accent_color": args.accent_color,
            "logo_url": args.logo_url or "",
        },
        "contacts": {"owner_alias": args.owner or "", "approver_aliases": args.approver or []},
        "allowed_actions": [],
        "budgets": {"monthly_api_usd": args.monthly_budget_usd},
        "data_retention_days": args.retention_days,
        "status": "active",
        "created_at": dt.date.today().isoformat(),
        "notes": "",
    }


def cmd_init(root: Path) -> dict:
    for sub in ("clients", "audit", "archive"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    readme = root / "README.md"
    if not readme.exists():
        readme.write_text(
            "# SEO workspace\n\nManaged by the Web-SEO plugin (`/seo workspace`).\n"
            "Contains client data: keep it out of public repositories.\n",
            encoding="utf-8",
        )
    append_event(root, "workspace.init")
    return {"status": "PASS", "root": str(root)}


def cmd_add(root: Path, args: argparse.Namespace) -> tuple[dict, int]:
    profile = build_profile(args)
    errors = validate_profile(profile)
    if errors:
        return {"status": "ERROR", "errors": errors}, 1
    folder = clients_dir(root) / profile["slug"]
    if (folder / "client.json").exists():
        return {"status": "ERROR", "errors": [f"client '{profile['slug']}' already exists"]}, 1
    for area in AREAS:
        (folder / area).mkdir(parents=True, exist_ok=True)
    (folder / "client.json").write_text(json.dumps(profile, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    append_event(root, "workspace.client_added", client=profile["slug"], detail={"domains": profile["domains"]})
    return {"status": "PASS", "client": profile, "path": str(folder)}, 0


def cmd_list(root: Path) -> dict:
    out = []
    base = clients_dir(root)
    for path in sorted(base.glob("*/client.json")) if base.is_dir() else []:
        p = json.loads(path.read_text(encoding="utf-8"))
        out.append({"slug": p.get("slug"), "name": p.get("name"), "primary_domain": p.get("primary_domain"),
                    "industry": p.get("industry"), "status": p.get("status")})
    return {"status": "PASS", "root": str(root), "clients": out}


def cmd_find(root: Path, domain: str) -> tuple[dict, int]:
    host = normalize_host(domain)
    base = clients_dir(root)
    for path in sorted(base.glob("*/client.json")) if base.is_dir() else []:
        p = json.loads(path.read_text(encoding="utf-8"))
        if any(host == d or host.endswith("." + d) for d in p.get("domains", [])):
            return {"status": "PASS", "slug": p["slug"], "name": p["name"], "path": str(path.parent)}, 0
    return {"status": "NOT_FOUND", "domain": host}, 1


def cmd_validate(root: Path, slug: str | None) -> tuple[dict, int]:
    slugs = [slug] if slug else [c["slug"] for c in cmd_list(root)["clients"]]
    results, bad = {}, 0
    for s in slugs:
        try:
            errs = validate_profile(load_client(root, s))
        except (FileNotFoundError, json.JSONDecodeError) as exc:
            errs = [str(exc)]
        results[s] = errs
        bad += bool(errs)
    return {"status": "PASS" if not bad else "ERROR", "results": results}, 1 if bad else 0


def cmd_archive(root: Path, slug: str) -> tuple[dict, int]:
    src = clients_dir(root) / slug
    if not (src / "client.json").exists():
        return {"status": "ERROR", "errors": [f"no client '{slug}'"]}, 1
    dest = root / "archive" / f"{slug}-{dt.date.today().isoformat()}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dest))
    append_event(root, "workspace.client_archived", client=slug, detail={"to": dest.name})
    return {"status": "PASS", "archived_to": str(dest)}, 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--root", help="workspace root (default $WEB_SEO_HOME or ./seo-workspace)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init")
    a = sub.add_parser("add-client")
    a.add_argument("--name", required=True)
    a.add_argument("--slug")
    a.add_argument("--domain", action="append", required=True)
    a.add_argument("--competitor", action="append")
    a.add_argument("--market", action="append", help="COUNTRY:language, e.g. US:en")
    a.add_argument("--industry", choices=INDUSTRIES, default="other")
    a.add_argument("--owner", help="owner alias (not an email address)")
    a.add_argument("--approver", action="append", help="approver alias (repeatable)")
    a.add_argument("--brand-color", default="#1e3a5f")
    a.add_argument("--accent-color", default="#b8860b")
    a.add_argument("--logo-url")
    a.add_argument("--retention-days", type=int, default=365)
    a.add_argument("--monthly-budget-usd", type=float, default=0.0)
    sub.add_parser("list")
    s = sub.add_parser("show")
    s.add_argument("slug")
    f = sub.add_parser("find")
    f.add_argument("--domain", required=True)
    p = sub.add_parser("path")
    p.add_argument("slug")
    p.add_argument("area", choices=AREAS)
    v = sub.add_parser("validate")
    v.add_argument("slug", nargs="?")
    r = sub.add_parser("archive")
    r.add_argument("slug")
    args = ap.parse_args(argv)
    root = workspace_root(args.root)

    code = 0
    if args.cmd == "init":
        result = cmd_init(root)
    elif args.cmd == "add-client":
        root.mkdir(parents=True, exist_ok=True)
        result, code = cmd_add(root, args)
    elif args.cmd == "list":
        result = cmd_list(root)
    elif args.cmd == "show":
        try:
            result = {"status": "PASS", "client": load_client(root, args.slug)}
        except FileNotFoundError as exc:
            result, code = {"status": "ERROR", "errors": [str(exc)]}, 1
    elif args.cmd == "find":
        result, code = cmd_find(root, args.domain)
    elif args.cmd == "path":
        folder = clients_dir(root) / args.slug
        if not (folder / "client.json").exists():
            result, code = {"status": "ERROR", "errors": [f"no client '{args.slug}'"]}, 1
        else:
            (folder / args.area).mkdir(parents=True, exist_ok=True)
            result = {"status": "PASS", "path": str(folder / args.area)}
    elif args.cmd == "validate":
        result, code = cmd_validate(root, args.slug)
    else:
        result, code = cmd_archive(root, args.slug)
    print(json.dumps(result, ensure_ascii=False))
    return code


if __name__ == "__main__":
    sys.exit(main())
