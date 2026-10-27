# Web-SEO Enterprise Guide

This guide covers running Web-SEO for an agency or an in-house team: deployment,
client isolation, approvals, spend control, audit trail, data handling, reporting,
automation, release integrity and incident response. Every enterprise tool is
standard-library Python, prints JSON, and never calls a network service.

## 1. What the enterprise layer adds

| Capability | Where | Command |
|---|---|---|
| Multi-client workspaces | `scripts/workspace.py`, `skills/seo-workspace` | `/seo workspace` |
| Org policy for consequential actions | `scripts/governance.py`, `skills/seo-governance` | `/seo governance policy` |
| Per-client approval ledger (hash-chained) | `governance.py authorize / consume / revoke / ledger` | `/seo governance authorize` |
| Budget caps and month-to-date spend | `governance.py check --cost-usd`, `spend` | `/seo governance spend` |
| Tamper-evident audit log and retention | `scripts/audit_log.py`, `hooks/audit_hook.py` | `/seo governance audit` |
| Secret guard on file writes | `hooks/secret_guard.py` (PreToolUse) | always on |
| Standard findings format | `enterprise/schemas/findings.schema.json` | `/seo export template` |
| Jira, Linear, GitHub, Slack, Teams exports and run diffs | `scripts/findings_export.py`, `skills/seo-export` | `/seo export` |
| Portfolio roll-up and white-label summary | `scripts/portfolio_rollup.py`, `skills/seo-portfolio` | `/seo portfolio` |
| Scheduled monitoring | `scripts/schedule_plan.py`, `skills/seo-schedule` | `/seo schedule` |
| Org-wide deployment template | `enterprise/managed-settings.example.json` | n/a |
| CI gates and pre-commit hooks | `.github/workflows/ci.yml`, `.pre-commit-config.yaml` | n/a |

## 2. Deployment

1. **Host the plugin.** The public source is `DataSpoof/Web-SEO`. For internal use,
   fork or mirror it to a private repository your team can read (for example
   `your-org/web-seo`) and point managed settings at that copy. Protect the default branch and
   require review from `.github/CODEOWNERS` owners.
2. **Distribute through managed settings.** Copy
   `enterprise/managed-settings.example.json` into your Claude Code managed settings,
   replace the repository name, and roll it out with your device-management tool.
   It registers the marketplace, enables the plugin, turns on audit logging
   (`WEB_SEO_AUDIT=1`), blocks bypass-permissions mode, denies reading local secret
   files, and asks before paid MCP tools, `git push`, issue creation and `curl`.
   Check the keys against the Claude Code settings documentation for your version.
3. **Runtime.** Each user runs `/seo setup` once; `/seo doctor` reports readiness.
   Python 3.11 to 3.14 is supported.
4. **Workspace location.** Set `WEB_SEO_HOME` to a shared, access-controlled path
   (a private repository works well: history doubles as a change record), or let
   each project use `./seo-workspace`.
5. **Pilot first.** Start with one client and the default policy, review a week of
   audit log and ledger entries, then widen.

## 3. Roles

| Role | Responsibilities | Typical alias field |
|---|---|---|
| Workspace admin | Org policy, managed settings, retention, plugin upgrades | n/a |
| Account lead | Client profile, audits, reports | `contacts.owner_alias` |
| Approver | Approves consequential actions for one client in chat | `contacts.approver_aliases` |
| Analyst | Runs analyses and drafts; cannot approve | n/a |
| Auditor | Reads `audit_log.py query/export` and ledgers; changes nothing | n/a |

Claude Code runs as the signed-in user, so roles are enforced by who may edit the
workspace (file or repository permissions) and by the approval rules below, not by
the plugin alone.

## 4. Governance model

`org-policy.json` gives each governed action one mode:

- **allow**: read-only analysis; paid API calls within budget.
- **require_approval**: Indexing API and IndexNow submissions, account registration
  and login, directory submission, outreach email, public image uploads, edits to a
  client's site repository, and paid runs above budget.
- **deny**: publishing, payments, reciprocal links, CAPTCHA or 2FA bypass, mass
  outreach. `governance.py policy validate` refuses a policy that loosens
  publishing, payment or CAPTCHA bypass.

A client profile can tighten a mode with `action_overrides` (for example deny
outreach for a regulated client) and can never loosen one.

Approvals: the user approves in chat; Claude records it with `authorize`, naming
an approver alias from the client profile, a scope, a count limit and an expiry
(at most `approvals.max_validity_days`). Each use is recorded with `consume`.
`check` only allows a governed action while a live authorization covers it.

## 5. Spend control

Set `budgets.per_run_usd` and `budgets.monthly_usd` in the org policy and
`budgets.monthly_api_usd` per client. Before metered runs, `check --action
paid_api_call --cost-usd N` compares the estimate with the per-run cap and with
month-to-date spend from `governance.py spend` records. For DataForSEO, estimate
with `dataforseo_costs.py`; the optional `seo-cockpit` companion adds a live spend
guard on top.

## 6. Audit trail

- `seo-workspace/audit/audit-log.jsonl` records workspace changes, governance checks,
  approvals, spend, and (when enabled) tool activity from `hooks/audit_hook.py`.
- Each record carries the previous record's SHA-256; `audit_log.py verify` names
  the first broken sequence number after any edit or deletion.
- Recorded: tool name, session id, a short target (file path, URL, first 300
  characters of a shell command). Not recorded: file contents, tool output, page
  bodies. Credential-like strings and email addresses are redacted.
- Export for reviews with `audit_log.py export --format csv --out audit.csv`.

The chain is tamper-evident, not tamper-proof: anyone who can write the file can
rewrite the whole chain. For stronger guarantees, commit the workspace to a
repository with protected branches, or ship the log to write-once storage.

## 7. Secrets

- Credentials live outside projects: `~/.config/claude-seo/*.json` (Google, Moz,
  Bing, Matomo), the ignored `writer/config/r2.config.json`, environment variables,
  or the OS keychain.
- `hooks/secret_guard.py` blocks Write and Edit calls that would put a live-looking
  key (Google, AWS, GitHub, Anthropic, OpenAI, Slack, Stripe, private keys, Slack
  webhooks) into any other file. Placeholders marked EXAMPLE or REDACTED pass.
  `WEB_SEO_SECRET_GUARD=off` disables it for a session.
- CI runs a credential scan on every push; `.pre-commit-config.yaml` adds gitleaks
  before commit.
- Rotate any key that was pasted into a chat, a log or a file, even if it was
  removed afterwards.

## 8. Data handling and privacy

- **Minimize.** Store findings, scores and evidence summaries, not raw pages.
  `data.store_raw_html` defaults to false.
- **Personal data.** Profiles hold aliases, never personal emails or phone numbers.
  Analytics exports (GA4, Search Console, Matomo) are aggregate; do not add user-level
  data to findings or reports.
- **Retention.** `data.retention_days` (org) and `data_retention_days` (client) set
  the window. `audit_log.py purge` applies it to the log; the `seo-governance`
  retention step lists expired client files for the user to delete.
- **Separation.** One folder per client; portfolio reports that combine clients are
  for internal audiences unless every client in them may be shown.
- **Crawling.** Respect robots.txt, rate limits and site terms. All URL fetchers use
  `scripts/url_safety.py`, which blocks private addresses and DNS rebinding.
- **Subprocessors.** Model calls go to Anthropic under your organization's agreement;
  optional extensions send data to their providers (DataForSEO, Ahrefs, SE Ranking,
  Profound, Firecrawl, Google, Bing, Cloudflare R2). Enable only the ones your data
  processing agreements cover.

## 9. Reporting and integrations

- After an audit, save findings in the standard format to the client's `audits/`.
- `/seo export` converts them to Jira or Linear CSV imports, GitHub issue JSON,
  Markdown or CSV, and builds Slack or Teams payloads. Nothing is sent without the
  user's approval.
- `/seo export diff` compares two runs (new, resolved, severity changes, regression).
- `/seo portfolio` produces leadership summaries; `/seo google report` produces
  branded PDFs.

## 10. Automation

`/seo schedule` generates GitHub Actions workflows, cron lines, Windows tasks or
Claude routines for weekly audits, daily drift checks and monthly roll-ups. Every
generated prompt is read-only; governed actions never run unattended. Store
`ANTHROPIC_API_KEY` as an encrypted secret and keep the allowed-tools list minimal.

## 11. Quality gates and release integrity

- `ci.yml` runs lint, the full test suite on Python 3.11, 3.12 and 3.14, the
  imported skills' tests, a credential scan, Windows and macOS portability,
  `pip-audit` on both ends of the Python range, and the enterprise checks (schema
  validation of examples, enterprise tests, a workspace round trip).
- `scripts/consistency_check.py` gates dead references, routing drift and orphans.
- `scripts/release_sign.py` writes a SHA-256 manifest for a release;
  `scripts/verify_release.py` checks a checkout against it. Verify before rolling a
  new version out through managed settings.
- Dependencies are bounded with CVE floors in `requirements.txt` and locked in
  `uv.lock`. Generate an SBOM for procurement with, for example,
  `uv export --format requirements-txt > sbom-requirements.txt` or a CycloneDX tool.

## 12. Upgrades

1. Pull upstream changes for claude-seo and backlink_skills into a branch.
2. Re-apply the merge rules in `docs/MERGED-FEATURES.md`; update
   `data/imported-skills.json` for new or renamed imported skills.
3. Run CI, sign the release, update the marketplace pointer, announce the change.

## 13. Incident runbooks

**A credential was exposed** (chat, log, file, commit):
1. Revoke and rotate it at the provider first.
2. Remove it from files; if committed, purge history and force-rotate anyway.
3. `audit_log.py query --since <date>` to see which sessions touched it.
4. Record the incident and the rotation in your incident system.

**An unapproved or wrong submission or email happened:**
1. Stop the session. Run `governance.py ledger --client <slug>` and
   `audit_log.py query --client <slug> --since <date>`.
2. Revoke related authorizations with `governance.py revoke`.
3. Contact the site owner yourself to withdraw the listing or apologize; do not let
   automation follow up.
4. Tighten the client's `action_overrides` and review the policy.

**`audit_log.py verify` reports BROKEN:**
1. Preserve the file as evidence; do not purge or rewrite it.
2. Compare with the repository history or backup to find the change.
3. Start a new log segment after review and record the finding.

**A scheduled job failed or produced wrong numbers:**
1. Check the job log and `/seo doctor`.
2. Re-run the job manually; compare with `/seo export diff`.
3. Do not publish portfolio numbers from a partial run.

## 14. Known limits

- The plugin cannot enforce identity: approver aliases are recorded, not verified.
  Use your identity provider and repository permissions for who may approve.
- Hooks run on the user's machine and can be disabled by that user unless managed
  settings lock them in.
- The secret guard matches high-confidence patterns only; it does not replace
  gitleaks, CI scanning or provider-side secret detection.
- This layer supports compliance work (approvals, audit trail, retention) but does
  not by itself make a deployment SOC 2, ISO 27001 or GDPR compliant.
