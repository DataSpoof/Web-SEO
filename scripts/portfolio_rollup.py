#!/usr/bin/env python3
"""
Portfolio roll-up: combine findings from many sites or clients into one view.

Reads Web-SEO findings files (see findings_export.py), keeps the latest file
per site as current and the one before it as the comparison point, and
writes a roll-up JSON, a CSV and a self-contained, white-label HTML executive
summary (no external assets, every value escaped).

Inputs are explicit files or a workspace root, where it reads
clients/*/audits/*.json.

Usage:
    portfolio_rollup.py --files a.json b.json [--out-dir reports/portfolio]
    portfolio_rollup.py --workspace [--root seo-workspace] [--client acme] [--out-dir ...]
        [--brand-name "Acme Agency"] [--brand-color "#1e3a5f"] [--title "Q4 SEO portfolio"]

Output: JSON summary on stdout; files in --out-dir (default ./portfolio-report).
Exit 0 on success, 1 when no valid findings files were found.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import html
import json
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPTS_DIR)

from audit_log import workspace_root  # noqa: E402
from findings_export import SEVERITIES, finding_key, validate  # noqa: E402

COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
SEV_WEIGHT = {"critical": 10, "high": 5, "medium": 2, "low": 1, "info": 0}


def collect(files: list[Path]) -> tuple[dict[str, list[dict]], list[str]]:
    by_site: dict[str, list[dict]] = defaultdict(list)
    skipped = []
    for path in files:
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            skipped.append(f"{path}: {exc}")
            continue
        if not isinstance(doc, dict) or validate(doc):
            skipped.append(f"{path}: not a valid findings file")
            continue
        site = doc.get("site") or path.stem
        doc["_file"] = str(path)
        by_site[site].append(doc)
    for docs in by_site.values():
        docs.sort(key=lambda d: (d.get("generated_at") or "", d["_file"]))
    return by_site, skipped


def site_summary(site: str, docs: list[dict]) -> dict:
    cur = docs[-1]
    prev = docs[-2] if len(docs) > 1 else None
    open_items = [f for f in cur["findings"] if f.get("status", "open") in ("open", "in_progress")]
    counts = Counter(f.get("severity") for f in open_items)
    score = cur.get("health_score")
    prev_score = prev.get("health_score") if prev else None
    new_keys = set()
    if prev:
        old = {finding_key(f) for f in prev["findings"]}
        new_keys = {finding_key(f) for f in cur["findings"]} - old
    return {
        "site": site,
        "client": cur.get("client"),
        "generated_at": cur.get("generated_at"),
        "health_score": score,
        "score_delta": (score - prev_score) if isinstance(score, (int, float)) and isinstance(prev_score, (int, float)) else None,
        "open_by_severity": {s: counts.get(s, 0) for s in SEVERITIES},
        "risk_points": sum(SEV_WEIGHT.get(f.get("severity"), 0) for f in open_items),
        "new_since_previous": len(new_keys),
        "top_issues": [{"id": finding_key(f), "severity": f["severity"], "title": f["title"]}
                       for f in sorted(open_items, key=lambda f: SEVERITIES.index(f["severity"]))[:5]],
        "source_file": cur["_file"],
    }


def rollup(by_site: dict[str, list[dict]]) -> dict:
    sites = sorted((site_summary(s, d) for s, d in by_site.items()),
                   key=lambda x: (-x["risk_points"], x["site"]))
    scores = [s["health_score"] for s in sites if isinstance(s["health_score"], (int, float))]
    common = Counter()
    for docs in by_site.values():
        seen = {(f.get("category") or "uncategorized", f["title"].strip().lower())
                for f in docs[-1]["findings"] if f.get("status", "open") in ("open", "in_progress")}
        common.update(seen)
    totals = Counter()
    for s in sites:
        totals.update(s["open_by_severity"])
    return {
        "generated_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "sites": sites,
        "portfolio": {
            "site_count": len(sites),
            "average_health_score": round(sum(scores) / len(scores), 1) if scores else None,
            "open_by_severity": {s: totals.get(s, 0) for s in SEVERITIES},
            "sites_with_critical": sum(1 for s in sites if s["open_by_severity"]["critical"]),
            "declining_sites": [s["site"] for s in sites if (s["score_delta"] or 0) < 0],
        },
        "common_issues": [{"category": c, "title": t, "sites": n}
                          for (c, t), n in common.most_common(15) if n > 1],
    }


def render_html(data: dict, title: str, brand: str, color: str) -> str:
    e = html.escape
    p = data["portfolio"]
    rows = "".join(
        f"<tr><td>{e(s['site'])}</td><td>{e(str(s['client'] or ''))}</td>"
        f"<td class=num>{e(str(s['health_score'] if s['health_score'] is not None else '-'))}</td>"
        f"<td class=num>{e(_delta(s['score_delta']))}</td>"
        + "".join(f"<td class=num>{s['open_by_severity'][k]}</td>" for k in ("critical", "high", "medium", "low"))
        + f"<td>{e('; '.join(i['title'] for i in s['top_issues'][:3]))}</td></tr>"
        for s in data["sites"])
    common = "".join(f"<li>{e(c['title'])} <span class=muted>({e(c['category'])}, {c['sites']} sites)</span></li>"
                     for c in data["common_issues"]) or "<li class=muted>No issue repeats across sites.</li>"
    avg = p["average_health_score"]
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(title)}</title>
<style>
:root{{--brand:{color};--ink:#1f2933;--muted:#616e7c;--line:#e4e7eb;--bg:#faf9f7}}
body{{margin:0;font:15px/1.5 system-ui,-apple-system,Segoe UI,sans-serif;color:var(--ink);background:var(--bg)}}
header{{background:var(--brand);color:#fff;padding:28px 32px}}header h1{{margin:0;font-size:24px}}
main{{padding:24px 32px;max-width:1200px}}.kpis{{display:flex;gap:16px;flex-wrap:wrap;margin-bottom:24px}}
.kpi{{background:#fff;border:1px solid var(--line);border-radius:8px;padding:14px 18px;min-width:150px}}
.kpi b{{display:block;font-size:26px;color:var(--brand)}}table{{border-collapse:collapse;width:100%;background:#fff}}
th,td{{border-bottom:1px solid var(--line);padding:8px 10px;text-align:left;vertical-align:top}}
th{{font-size:13px;color:var(--muted);text-transform:uppercase}}.num{{text-align:right;font-variant-numeric:tabular-nums}}
.muted{{color:var(--muted)}}.wrap{{overflow-x:auto}}footer{{padding:16px 32px;color:var(--muted);font-size:13px}}
</style></head><body>
<header><h1>{e(title)}</h1><div>{e(brand)} | generated {e(data['generated_at'][:10])}</div></header>
<main>
<section class=kpis>
<div class=kpi><b>{p['site_count']}</b>sites</div>
<div class=kpi><b>{e(str(avg) if avg is not None else '-')}</b>average health score</div>
<div class=kpi><b>{p['open_by_severity']['critical']}</b>open critical issues</div>
<div class=kpi><b>{p['sites_with_critical']}</b>sites with critical issues</div>
<div class=kpi><b>{len(p['declining_sites'])}</b>sites declining</div>
</section>
<h2>Sites by risk</h2>
<div class=wrap><table><thead><tr><th>Site</th><th>Client</th><th class=num>Score</th><th class=num>Change</th>
<th class=num>Critical</th><th class=num>High</th><th class=num>Medium</th><th class=num>Low</th><th>Top issues</th></tr></thead>
<tbody>{rows}</tbody></table></div>
<h2>Issues that repeat across sites</h2><ul>{common}</ul>
</main><footer>Risk points weight open issues: critical 10, high 5, medium 2, low 1.</footer>
</body></html>
"""


def _delta(v: float | None) -> str:
    if v is None:
        return "-"
    return f"+{v:g}" if v > 0 else f"{v:g}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--files", nargs="+")
    src.add_argument("--workspace", action="store_true")
    ap.add_argument("--root")
    ap.add_argument("--client", help="limit --workspace to one client slug")
    ap.add_argument("--out-dir", default="portfolio-report")
    ap.add_argument("--title", default="SEO portfolio summary")
    ap.add_argument("--brand-name", default="")
    ap.add_argument("--brand-color", default="#1e3a5f")
    args = ap.parse_args(argv)

    if args.workspace:
        root = workspace_root(args.root)
        pattern = f"clients/{args.client or '*'}/audits/*.json"
        files = sorted(root.glob(pattern))
        if args.client and not args.brand_name:
            profile = root / "clients" / args.client / "client.json"
            if profile.is_file():
                brand = json.loads(profile.read_text(encoding="utf-8")).get("brand") or {}
                args.brand_name = brand.get("display_name", "")
                args.brand_color = brand.get("primary_color") or args.brand_color
    else:
        files = [Path(f) for f in args.files]
    if not COLOR_RE.match(args.brand_color):
        print(json.dumps({"status": "ERROR", "errors": ["--brand-color must be #rrggbb"]}))
        return 1

    by_site, skipped = collect(files)
    if not by_site:
        print(json.dumps({"status": "ERROR", "errors": ["no valid findings files"], "skipped": skipped}))
        return 1
    data = rollup(by_site)
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "portfolio.json").write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    with (out / "portfolio.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["site", "client", "health_score", "score_delta", *SEVERITIES, "risk_points", "new_since_previous"])
        for s in data["sites"]:
            w.writerow([s["site"], s["client"] or "", s["health_score"], s["score_delta"],
                        *[s["open_by_severity"][k] for k in SEVERITIES], s["risk_points"], s["new_since_previous"]])
    (out / "portfolio.html").write_text(render_html(data, args.title, args.brand_name, args.brand_color), encoding="utf-8")
    print(json.dumps({"status": "PASS", "sites": data["portfolio"]["site_count"],
                      "average_health_score": data["portfolio"]["average_health_score"],
                      "out_dir": str(out), "files": ["portfolio.json", "portfolio.csv", "portfolio.html"],
                      "skipped": skipped}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
