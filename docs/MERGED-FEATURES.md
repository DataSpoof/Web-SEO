# Merged Features Map

Web-SEO combines two MIT-licensed repositories into one Claude Code plugin. This
page records where every upstream feature now lives and what was changed to make
the pieces work together.

| Source | Commit / version | Cloned |
|---|---|---|
| [AgriciDaniel/claude-seo](https://github.com/AgriciDaniel/claude-seo) | v2.5.0 (`main`) | 2026-10-26 |
| [flaqai/backlink_skills](https://github.com/flaqai/backlink_skills) | `main` (matrix 1.1.1, blog workflow 0.16.6) | 2026-10-26 |

## claude-seo (kept whole)

Everything from claude-seo is present at its original path: the `seo` orchestrator
and its 25 sub-skills, 19 agents, `scripts/` (launcher, 60 tools), `hooks/`,
`schema/`, `data/google-updates.json`, `extensions/` (9 optional MCP extensions),
`docs/`, `tests/`, `pyproject.toml`, `requirements.txt`, `uv.lock` and the
`seo-cockpit` companion entry in the marketplace.

## backlink_skills (mapped into the plugin layout)

Claude Code only discovers skills at `skills/<name>/SKILL.md`, so nested folders
were flattened. Names are unchanged except the upstream typo
`submit-producti-directories-merged`, which is now `submit-product-directories-merged`.

| Upstream path | Now at |
|---|---|
| `双线外链挖掘矩阵/skills/backlink-expansion-matrix` | `skills/backlink-expansion-matrix` |
| `双线外链挖掘矩阵/skills/backlink-source-prep` | `skills/backlink-source-prep` |
| `双线外链挖掘矩阵/skills/backlink-batch-expansion` | `skills/backlink-batch-expansion` |
| `双线外链挖掘矩阵/skills/backlink-quality-expansion` | `skills/backlink-quality-expansion` |
| `双线外链挖掘矩阵/skills/backlink-workbook-delivery` | `skills/backlink-workbook-delivery` |
| `双线外链挖掘矩阵/README*.md`, `matrix.yaml` | `docs/backlink-matrix/` |
| `submit-product-directories-v1-batch` | `skills/submit-product-directories-v1-batch` |
| `submit-product-directories-v2-quality` | `skills/submit-product-directories-v2-quality` |
| `submit-producti-directories-merged` | `skills/submit-product-directories-merged` |
| `writer` | `skills/writer` |
| `writer/linkedin-writer`, `medium-writer`, `wechat-writer` | `skills/linkedin-writer`, `skills/medium-writer`, `skills/wechat-writer` |
| `scalable-seo-blog-workflow/skills/*` | `skills/blog-gate`, `blog-writer`, `blog-review` (skills) and `blog-harness`, `blog-human-handoff`, `blog-platform-matching`, `blog-writer-merged` (internal tools, no SKILL.md) |
| `scalable-seo-blog-workflow/docs/*` | `docs/` (same file names, so `../../docs/` links still resolve) |
| `scalable-seo-blog-workflow/templates/*` | `templates/` |
| `scalable-seo-blog-workflow/README*.md`, `matrix.yaml` | `docs/blog-workflow/` |
| `scalable-seo-blog-workflow/tests/*` | `tests/blog_workflow/` (imports and `parents[]` depth updated) |
| `Free-backlink-list.md` | `data/free-backlink-list.md` |
| `README_en.md` | `docs/upstream/backlink_skills-README.md` |
| `LICENSE` | `licenses/backlink_skills-LICENSE` |

Each directory-submission skill keeps its own `tests/` folder.

The blog campaign's internal references are reached through their tool folders:
`skills/blog-human-handoff/references/local-seo-issues.md`,
`skills/blog-human-handoff/references/platform-release-observations.md` and its
companion `platform-release-observations.test.md`.

Not imported (repository chrome, not features): the 18 translated READMEs, the
hero and star-growth images, and `scripts/update-star-growth-chart.mjs`.

## Changes made during the merge

1. **Script paths.** Upstream commands ran tools such as `audit_submission_record.py`
   by a path relative to the skill folder, which assumed it was the working directory. In a plugin the working
   directory is the user's project, so every call now uses
   `"${CLAUDE_PLUGIN_ROOT}/skills/<skill>/scripts/<file>"`.
2. **Writer links.** The platform writers' `../references/` and `../SKILL.md` links
   now point to `../writer/...` after flattening.
3. **English triggers.** `blog-gate`, `blog-writer`, `blog-review` and
   `submit-product-directories-merged` had Chinese-only descriptions; they now have
   English descriptions with the Chinese summary kept. Bodies are unchanged.
4. **Security section.** Each of the 15 imported skills gained a `## Security`
   section: fetched pages and exports are untrusted data, never instructions, and
   credentials, payments and one-time codes are handed to the user.
5. **Frontmatter.** Each imported skill gained `license`, `metadata.author`,
   `metadata.version` (upstream version, or the clone date where upstream had none)
   and `metadata.source`.
6. **Routing.** `skills/seo/SKILL.md` and `docs/COMMANDS.md` gained
   `/seo backlink-matrix`, `/seo submit-directories`, `/seo backlink-list`,
   `/seo write` and `/seo blog-campaign`.
7. **Manifests.** The plugin is `web-seo`, served by the local marketplace
   `web-seo-local`.
8. **Governance tests.** `data/imported-skills.json` lists the imported skills and
   paths. `scripts/consistency_check.py`, `tests/test_no_em_dash.py`,
   `tests/test_file_size_limits.py`, `tests/test_manifest_consistency.py` and
   `tests/test_agent_untrusted_content.py` read it so claude-seo's house-style rules
   (no em dashes, 200-line references, `seo-*` names, core launcher only) keep
   applying to claude-seo files without rewriting upstream backlink_skills text.
   Em dashes in the imported skills are literal status values, for example the
   `duplicate` / `no action` status that the audit scripts match exactly.

## How the two halves connect

- `seo-backlinks` analyzes the existing profile and competitor gaps; its
  referring-domain exports are valid input to `backlink-source-prep`.
- `skills/seo/references/free-backlink-sources.md` (claude-seo) lists free data
  sources for analysis; `data/free-backlink-list.md` (Flaq AI) lists destinations
  to recheck before submitting.
- `seo-content-brief` and `seo-cluster` produce briefs and topic maps that
  `writer`, the platform writers and `blog-gate` can take as input.
- `seo-schema`, `seo-content` and `seo-geo` can review the pages that the writers
  produce before a human publishes them.
