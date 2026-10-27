# Web-SEO: SEO Analysis + Link Building for Claude Code

One Claude Code plugin that combines every feature of two open-source projects:

- **[claude-seo](https://github.com/AgriciDaniel/claude-seo)** by AgriciDaniel (v2.5.0):
  full-site audits, technical SEO, content and E-E-A-T, schema, sitemaps, images,
  Core Web Vitals, local SEO and maps, GEO / AI search, agent readiness, backlink
  profile analysis, clustering, SXO, drift monitoring, e-commerce, hreflang, Google
  APIs, the FLOW framework, and 9 optional MCP extensions.
- **[backlink_skills](https://github.com/flaqai/backlink_skills)** by Flaq AI:
  the dual-track backlink prospecting matrix, product-directory submission (batch,
  quality, merged), the SEO article writer, LinkedIn / Medium / WeChat writers, the
  scalable multi-platform blog campaign workflow, and the 743-entry public backlink
  candidate list.

In total: **46 sub-skills** (26 from claude-seo, 15 from backlink_skills, 5 enterprise
operations skills) and **19 sub-agents**, all reachable from one `/seo` entry point.

## Install

Add this repository as a plugin marketplace, then install:

```
/plugin marketplace add DataSpoof/Web-SEO
/plugin install web-seo@web-seo-local
```

From a local clone, pass the folder path instead: `/plugin marketplace add ./Web-SEO`.

If the original `claude-seo` plugin is already installed, disable or uninstall it
first so the same skills and agents are not loaded twice (`/plugin` lists them).

Then run `/seo setup` once to create the isolated Python runtime and Chromium.
`/seo doctor` checks readiness at any time.

**Requirements.** Claude Code 1.0.33 or newer and Python 3.11 to 3.14. On Windows, set
`PYTHONUTF8=1` before using the blog campaign tools, whose files are UTF-8 Chinese text. Node.js 18+
for the writer audit and Cloudflare R2 upload scripts. The blog campaign release
builder needs `markdown-it-py==3.0.0`
(`skills/blog-human-handoff/scripts/requirements.txt`).

Do not use `install.sh` / `install.ps1` for this merged plugin: those upstream
manual installers download claude-seo from GitHub and would not include the
link-building skills.

## What you can do

### SEO analysis (claude-seo)

| Command | What it does |
|---------|-------------|
| `/seo audit <url>` | Full website audit with parallel subagents and a 0-100 SEO Health Score |
| `/seo page <url>` | Deep single-page analysis |
| `/seo technical <url>` | Technical SEO across 9 categories |
| `/seo content <url>` | E-E-A-T and content quality |
| `/seo content-brief <topic>` | Content brief: keywords, outline, internal links |
| `/seo schema <url>` | Schema.org detection, validation, generation |
| `/seo sitemap <url or generate>` | Sitemap validation or generation |
| `/seo images <url>` | Image SEO |
| `/seo geo <url>` | AI Overviews / ChatGPT / Perplexity optimization |
| `/seo agentic <url>` | Agent readiness: Lighthouse Agentic Browsing, llms.txt, WebMCP |
| `/seo local <url>` / `/seo maps` | Local SEO, GBP, citations, geo-grid |
| `/seo backlinks <url>` | Backlink profile (Moz, Bing, Common Crawl, DataForSEO) |
| `/seo cluster <seed>` | SERP-overlap topic clustering |
| `/seo sxo <url>` | Search Experience Optimization |
| `/seo drift baseline\|compare\|history <url>` | SEO regression monitoring |
| `/seo ecommerce <url>` | Product schema, Shopping and marketplace intelligence |
| `/seo hreflang [url]` | International SEO |
| `/seo plan <type>` | Strategic plan by industry |
| `/seo programmatic` / `/seo competitor-pages` | Programmatic SEO and comparison pages |
| `/seo flow [stage]` | FLOW framework prompts |
| `/seo google [command]` | Search Console, PageSpeed, CrUX, GA4, Indexing API, PDF reports |
| `/seo dataforseo`, `/seo image-gen`, `/seo firecrawl`, `/seo ahrefs`, `/seo bing`, `/seo matomo`, `/seo profound`, `/seo seranking`, `/seo unlighthouse` | Optional extensions (see `extensions/`) |

### Link building and writing (backlink_skills)

| Command | Skill(s) | What it does |
|---------|----------|-------------|
| `/seo backlink-matrix` | `backlink-expansion-matrix` + 4 stage skills | Semrush / competitor sources, batch and editorial tracks, dedup, frozen batches, Free/Paid workbooks with QA |
| `/seo submit-directories quality` | `submit-product-directories-v2-quality` | Quality-first pilots of at most 10 directories with an SEO quality gate |
| `/seo submit-directories batch` | `submit-product-directories-v1-batch` | Large prequalified lists: normalize, dedup, shard, resumable queue |
| `/seo submit-directories merged` | `submit-product-directories-merged` | Directory forms plus one-to-one official Contact emails |
| `/seo backlink-list` | data | The 743-entry candidate list in `data/free-backlink-list.md` |
| `/seo write general\|linkedin\|medium\|wechat` | `writer`, `linkedin-writer`, `medium-writer`, `wechat-writer` | Fact-checked, audited, humanized articles and publishing packs |
| `/seo blog-campaign` | `blog-gate`, `blog-writer`, `blog-review` | Multi-article, multi-platform campaign with independent review and human release |

### Enterprise operations

| Command | What it does |
|---------|-------------|
| `/seo workspace` | One isolated folder per client: profile, audits, reports, backlinks, content, exports |
| `/seo governance` | Org policy (allow / approval / deny), per-client approval ledger, budget caps, audit log, retention |
| `/seo export` | Findings to Jira, Linear, GitHub Issues, CSV, Markdown, Slack, Teams; run-to-run diffs |
| `/seo portfolio` | Multi-site roll-up with risk ranking and a white-label HTML executive summary |
| `/seo schedule` | Recurring audits, drift checks and roll-ups for GitHub Actions, cron, Task Scheduler |

Two hooks ship with it: a **secret guard** that blocks writing live-looking API keys
into files, and an opt-in **audit hook** that records tool activity in a
hash-chained log. `enterprise/` holds JSON schemas, example policy and client
files, and a managed-settings template for org-wide rollout. See
[docs/ENTERPRISE.md](docs/ENTERPRISE.md).

The skills also trigger on their own descriptions, so asking "find guest-post
opportunities for my SaaS" or "write a Medium story about X" works without the
`/seo` prefix.

A typical end-to-end flow: `/seo audit` → `/seo backlinks` (profile and competitor
gaps) → `/seo backlink-matrix` (turn gaps into a screened candidate workbook) →
`/seo submit-directories quality` (pilot) → `/seo content-brief` → `/seo write`.

## Safety boundaries

The link-building skills never register, log in, pay, submit, send email, post or
publish without explicit authorization for the current batch. They never bypass
CAPTCHA, Turnstile, 2FA or email verification, never invent profiles or facts, and
never report a candidate as submitted, published or indexed without external
evidence. Fetched pages are treated as untrusted data, never as instructions.

## Language note

Several backlink_skills instruction files (the backlink matrix, the merged
directory skill, the blog campaign and WeChat writer) are written in Chinese, as
upstream. Claude follows them as written and answers in your language. Their
trigger descriptions are in English.

## Layout

```
.claude-plugin/        plugin.json + local marketplace.json (web-seo@web-seo-local)
skills/                46 sub-skills (+4 internal blog-campaign tool folders)
agents/                19 sub-agents
scripts/               claude-seo Python tools and the claude-seo launcher
hooks/                 schema validation hook
extensions/            optional MCP extensions (DataForSEO, Firecrawl, Ahrefs, ...)
data/                  Google updates ledger, free-backlink-list.md, imported-skills.json
enterprise/            JSON schemas, example policy/profile/findings, managed-settings template
templates/             blog campaign JSON/Markdown templates
docs/                  commands, architecture, blog workflow, backlink matrix, upstream READMEs
tests/                 claude-seo tests + tests/blog_workflow/
licenses/              both MIT licenses
```

See [docs/MERGED-FEATURES.md](docs/MERGED-FEATURES.md) for how each upstream file
was mapped, and [docs/COMMANDS.md](docs/COMMANDS.md) for every command.

## Tests

```
python -m pytest tests
python -m pytest skills
```

`scripts/consistency_check.py` reads `git ls-files`, so run `git init` and commit
before using it.

## Credits and licenses

Both projects are MIT licensed. claude-seo: Copyright (c) 2026 agricidaniel
([licenses/claude-seo-LICENSE](licenses/claude-seo-LICENSE)). backlink_skills:
Copyright (c) 2026 Flaq AI ([licenses/backlink_skills-LICENSE](licenses/backlink_skills-LICENSE)).
The FLOW prompt library inside `skills/seo-flow` is CC BY 4.0, as noted there.
Original READMEs: [docs/upstream/](docs/upstream/).
