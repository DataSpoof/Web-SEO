"""Web-SEO enterprise layer: workspaces, governance, audit log, exports,
portfolio roll-up, schedule generation, and the secret-guard and audit hooks.

Every tool runs as a subprocess against a temporary workspace, the way the
claude-seo launcher runs it.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
HOOKS = ROOT / "hooks"
EXAMPLES = ROOT / "enterprise"


def run(script: str, *args: str, root: Path | None = None, stdin: str | None = None,
        env: dict | None = None, folder: Path = SCRIPTS) -> tuple[int, str, str]:
    full_env = {**os.environ, **(env or {})}
    full_env.pop("WEB_SEO_HOME", None)
    full_env.pop("WEB_SEO_AUDIT", None)
    full_env.update(env or {})
    cmd = [sys.executable, str(folder / script)]
    if root is not None:
        cmd += ["--root", str(root)]
    proc = subprocess.run(cmd + list(args), capture_output=True, text=True, input=stdin, env=full_env, timeout=60)
    return proc.returncode, proc.stdout, proc.stderr


def js(out: str) -> dict:
    return json.loads(out)


@pytest.fixture()
def ws(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    assert run("workspace.py", "init", root=root)[0] == 0
    assert run("governance.py", "policy", "init", "--org-name", "Test", root=root)[0] == 0
    code, out, _ = run("workspace.py", "add-client", "--name", "Acme Inc", "--domain", "https://www.Acme.example/x",
                       "--competitor", "rival.example", "--market", "US:en", "--industry", "saas",
                       "--approver", "alice", "--monthly-budget-usd", "50", root=root)
    assert code == 0, out
    return root


# -- workspace ---------------------------------------------------------------

def test_add_client_normalizes_domain_and_creates_folders(ws: Path) -> None:
    profile = js(run("workspace.py", "show", "acme-inc", root=ws)[1])["client"]
    assert profile["domains"] == ["acme.example"]
    for area in ("audits", "reports", "backlinks", "content", "exports", "baselines", "authorizations"):
        assert (ws / "clients" / "acme-inc" / area).is_dir()


def test_find_matches_subdomains_and_rejects_unknown(ws: Path) -> None:
    assert js(run("workspace.py", "find", "--domain", "blog.acme.example", root=ws)[1])["slug"] == "acme-inc"
    code, out, _ = run("workspace.py", "find", "--domain", "other.example", root=ws)
    assert code == 1 and js(out)["status"] == "NOT_FOUND"


def test_profile_validation_rejects_email_approver_and_bad_color(tmp_path: Path) -> None:
    code, out, _ = run("workspace.py", "add-client", "--name", "Bad", "--domain", "bad.example",
                       "--approver", "a@b.example", "--brand-color", "red", root=tmp_path)
    errors = " ".join(js(out)["errors"])
    assert code == 1 and "email" in errors and "color" in errors


def test_duplicate_client_is_refused(ws: Path) -> None:
    code, out, _ = run("workspace.py", "add-client", "--name", "Acme Inc", "--domain", "acme.example", root=ws)
    assert code == 1 and "already exists" in out


def test_archive_moves_instead_of_deleting(ws: Path) -> None:
    assert run("workspace.py", "archive", "acme-inc", root=ws)[0] == 0
    assert not (ws / "clients" / "acme-inc").exists()
    assert list((ws / "archive").glob("acme-inc-*/client.json"))


# -- governance --------------------------------------------------------------

def test_read_only_is_allowed_and_publish_is_denied(ws: Path) -> None:
    assert run("governance.py", "check", "--client", "acme-inc", "--action", "read_only_analysis", root=ws)[0] == 0
    code, out, _ = run("governance.py", "check", "--client", "acme-inc", "--action", "content_publish", root=ws)
    assert code == 4 and js(out)["decision"] == "deny"


def test_approval_loop_grants_counts_and_exhausts(ws: Path) -> None:
    args = ("--client", "acme-inc", "--action", "directory_submission")
    assert run("governance.py", "check", *args, root=ws)[0] == 3
    code, out, _ = run("governance.py", "authorize", *args, "--approver", "alice", "--scope", "pilot",
                       "--max-count", "2", "--expires-days", "3", root=ws)
    assert code == 0, out
    auth_id = js(out)["authorization"]["auth_id"]
    code, out, _ = run("governance.py", "check", *args, root=ws)
    assert code == 0 and js(out)["auth_id"] == auth_id and js(out)["remaining"] == 2
    for _ in range(2):
        assert run("governance.py", "consume", "--client", "acme-inc", "--auth-id", auth_id, root=ws)[0] == 0
    assert run("governance.py", "consume", "--client", "acme-inc", "--auth-id", auth_id, root=ws)[0] == 1
    assert run("governance.py", "check", *args, root=ws)[0] == 3


def test_unknown_approver_and_denied_actions_cannot_be_authorized(ws: Path) -> None:
    code, out, _ = run("governance.py", "authorize", "--client", "acme-inc", "--action", "outreach_email",
                       "--approver", "mallory", "--scope", "x", root=ws)
    assert code == 1 and "approver_aliases" in out
    code, out, _ = run("governance.py", "authorize", "--client", "acme-inc", "--action", "payment",
                       "--approver", "alice", "--scope", "x", root=ws)
    assert code == 1 and "denied" in out


def test_revoke_and_expiry_limit(ws: Path) -> None:
    base = ("--client", "acme-inc", "--action", "outreach_email", "--approver", "alice", "--scope", "x")
    assert run("governance.py", "authorize", *base, "--expires-days", "999", root=ws)[0] == 1
    auth_id = js(run("governance.py", "authorize", *base, root=ws)[1])["authorization"]["auth_id"]
    assert run("governance.py", "revoke", "--client", "acme-inc", "--auth-id", auth_id, root=ws)[0] == 0
    assert run("governance.py", "check", "--client", "acme-inc", "--action", "outreach_email", root=ws)[0] == 3


def test_client_override_tightens_but_never_loosens(ws: Path) -> None:
    path = ws / "clients" / "acme-inc" / "client.json"
    profile = json.loads(path.read_text(encoding="utf-8"))
    profile["action_overrides"] = {"read_only_analysis": "deny", "payment": "allow"}
    path.write_text(json.dumps(profile), encoding="utf-8")
    assert run("governance.py", "check", "--client", "acme-inc", "--action", "read_only_analysis", root=ws)[0] == 4
    assert run("governance.py", "check", "--client", "acme-inc", "--action", "payment", root=ws)[0] == 4


def test_budget_caps_and_month_to_date_spend(ws: Path) -> None:
    check = ("check", "--client", "acme-inc", "--action", "paid_api_call", "--cost-usd")
    assert run("governance.py", *check, "5", root=ws)[0] == 0
    assert run("governance.py", *check, "11", root=ws)[0] == 3  # above per_run_usd 10
    assert run("governance.py", "spend", "--client", "acme-inc", "--usd", "48", root=ws)[0] == 0
    code, out, _ = run("governance.py", *check, "5", root=ws)  # 48 + 5 > client cap 50
    assert code == 3 and "monthly cap" in out


def test_ledger_verify_detects_tampering(ws: Path) -> None:
    run("governance.py", "authorize", "--client", "acme-inc", "--action", "indexnow_submit",
        "--approver", "alice", "--scope", "sitemap urls", root=ws)
    assert js(run("governance.py", "ledger", "--client", "acme-inc", "--verify", root=ws)[1])["status"] == "PASS"
    ledger = ws / "clients" / "acme-inc" / "authorizations" / "ledger.jsonl"
    ledger.write_text(ledger.read_text(encoding="utf-8").replace("sitemap urls", "all urls"), encoding="utf-8")
    code, out, _ = run("governance.py", "ledger", "--client", "acme-inc", "--verify", root=ws)
    assert code == 1 and js(out)["status"] == "BROKEN"


def test_policy_validate_refuses_loosening_publish(ws: Path) -> None:
    path = ws / "org-policy.json"
    policy = json.loads(path.read_text(encoding="utf-8"))
    policy["actions"]["content_publish"]["mode"] = "allow"
    path.write_text(json.dumps(policy), encoding="utf-8")
    code, out, _ = run("governance.py", "policy", "validate", root=ws)
    assert code == 1 and "content_publish" in out


# -- audit log ---------------------------------------------------------------

def test_audit_log_redacts_and_detects_edits(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    fake_key = "AIza" + "B" * 35
    detail = json.dumps({"note": f"key {fake_key} from ops@example.org"})
    assert run("audit_log.py", "append", "--event", "test", "--detail", detail, root=root)[0] == 0
    run("audit_log.py", "append", "--event", "test2", root=root)
    text = (root / "audit" / "audit-log.jsonl").read_text(encoding="utf-8")
    assert fake_key not in text and "ops@example.org" not in text and "[REDACTED]" in text
    assert js(run("audit_log.py", "verify", root=root)[1])["status"] == "PASS"
    log = root / "audit" / "audit-log.jsonl"
    lines = log.read_text(encoding="utf-8").splitlines()
    log.write_text(lines[1] + "\n", encoding="utf-8")  # delete the first record
    assert run("audit_log.py", "verify", root=root)[0] == 1


def test_audit_purge_keeps_chain_verifiable(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    for i in range(3):
        run("audit_log.py", "append", "--event", f"e{i}", root=root)
    log = root / "audit" / "audit-log.jsonl"
    recs = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines()]
    recs[0]["ts"] = "2000-01-01T00:00:00+00:00"
    # Re-hash the doctored record so only the age differs.
    sys.path.insert(0, str(SCRIPTS))
    import audit_log
    prev = audit_log.GENESIS
    for r in recs:
        r["prev_hash"] = prev
        r["hash"] = audit_log._digest(r)
        prev = r["hash"]
    log.write_text("".join(json.dumps(r) + "\n" for r in recs), encoding="utf-8")
    out = js(run("audit_log.py", "purge", "--older-than-days", "30", root=root)[1])
    assert out["dropped"] == 1 and out["kept"] == 2
    assert js(run("audit_log.py", "verify", root=root)[1])["status"] == "PASS"


# -- findings export -----------------------------------------------------------

def _findings(tmp_path: Path, name: str, findings: list[dict], score: int = 70, site: str = "acme.example") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps({"site": site, "client": "acme-inc", "generated_at": f"2026-10-{name[1:3]}T00:00:00+00:00",
                                "health_score": score, "findings": findings}), encoding="utf-8")
    return path


def test_example_findings_validate() -> None:
    assert run("findings_export.py", "validate", str(EXAMPLES / "findings.example.json"))[0] == 0


def test_validation_catches_bad_severity_and_duplicates(tmp_path: Path) -> None:
    f = _findings(tmp_path, "d01.json", [{"id": "A", "title": "x", "severity": "urgent"},
                                         {"id": "A", "title": "y", "severity": "low"}])
    code, out, _ = run("findings_export.py", "validate", str(f))
    assert code == 1 and "severity" in out and "duplicate" in out


def test_jira_csv_blocks_formula_injection_and_filters(tmp_path: Path) -> None:
    f = _findings(tmp_path, "d01.json", [
        {"id": "A", "title": "=HYPERLINK(\"http://evil\")", "severity": "high"},
        {"id": "B", "title": "Minor", "severity": "low"},
        {"id": "C", "title": "Done", "severity": "critical", "status": "fixed"}])
    code, out, _ = run("findings_export.py", "convert", str(f), "--to", "jira", "--min-severity", "medium")
    assert code == 0
    assert "'=HYPERLINK" in out and "Minor" not in out and "Done" not in out and "High" in out


def test_github_slack_teams_and_markdown_outputs(tmp_path: Path) -> None:
    f = _findings(tmp_path, "d01.json", [{"id": "A", "title": "Broken canonical | tags", "severity": "critical"}])
    gh = json.loads(run("findings_export.py", "convert", str(f), "--to", "github")[1])
    assert gh[0]["title"].startswith("[CRITICAL]") and "severity:critical" in gh[0]["labels"]
    assert json.loads(run("findings_export.py", "convert", str(f), "--to", "slack")[1])["blocks"]
    assert json.loads(run("findings_export.py", "convert", str(f), "--to", "teams")[1])["attachments"]
    assert "Broken canonical \\| tags" in run("findings_export.py", "convert", str(f), "--to", "markdown")[1]


def test_diff_flags_regressions(tmp_path: Path) -> None:
    old = _findings(tmp_path, "d01.json", [{"id": "A", "title": "a", "severity": "low"},
                                           {"id": "B", "title": "b", "severity": "medium"}])
    new = _findings(tmp_path, "d08.json", [{"id": "A", "title": "a", "severity": "high"},
                                           {"id": "C", "title": "c", "severity": "low"}])
    out = js(run("findings_export.py", "diff", str(old), str(new))[1])
    assert out["new"] == 1 and out["resolved"] == 1 and out["regression"] is True


# -- portfolio -------------------------------------------------------------------

def test_portfolio_rollup_from_workspace(ws: Path, tmp_path: Path) -> None:
    audits = ws / "clients" / "acme-inc" / "audits"
    _findings(audits, "d01.json", [{"id": "A", "title": "a", "severity": "high"}], score=60)
    _findings(audits, "d08.json", [{"id": "A", "title": "a", "severity": "high"},
                                   {"id": "B", "title": "<script>x</script>", "severity": "critical"}], score=55)
    _findings(audits, "d09.json", [{"id": "Z", "title": "a", "severity": "low"}], score=90, site="shop.acme.example")
    out_dir = tmp_path / "report"
    code, out, _ = run("portfolio_rollup.py", "--workspace", "--client", "acme-inc", "--out-dir", str(out_dir), root=ws)
    assert code == 0, out
    data = json.loads((out_dir / "portfolio.json").read_text(encoding="utf-8"))
    first = data["sites"][0]
    assert first["site"] == "acme.example" and first["score_delta"] == -5 and first["new_since_previous"] == 1
    assert data["portfolio"]["declining_sites"] == ["acme.example"]
    html_text = (out_dir / "portfolio.html").read_text(encoding="utf-8")
    assert "<script>x</script>" not in html_text and "#1e3a5f" in html_text


def test_portfolio_rejects_css_injection_color(tmp_path: Path) -> None:
    f = _findings(tmp_path, "d01.json", [])
    code, _, _ = run("portfolio_rollup.py", "--files", str(f), "--brand-color", "red;}body{display:none",
                     "--out-dir", str(tmp_path / "r"))
    assert code == 1


# -- schedule --------------------------------------------------------------------

def test_schedule_github_actions_is_read_only_and_keyless(tmp_path: Path) -> None:
    code, out, _ = run("schedule_plan.py", "--job", "audit", "--url", "https://acme.example", "--client", "acme-inc",
                       "--cron", "0 6 * * 1", "--target", "github-actions")
    assert code == 0
    cfg = js(out)["config"]
    assert 'cron: "0 6 * * 1"' in cfg and "secrets.ANTHROPIC_API_KEY" in cfg and "do not register" in cfg
    assert "sk-" not in cfg


def test_schedule_windows_and_validation() -> None:
    code, out, _ = run("schedule_plan.py", "--job", "portfolio", "--cron", "0 7 * * 1,3", "--target", "windows-task")
    assert code == 0 and "/SC WEEKLY /D MON,WED /ST 07:00" in js(out)["config"]
    assert run("schedule_plan.py", "--job", "drift", "--url", "ftp://x", "--cron", "0 7 * * *", "--target", "cron")[0] == 1
    assert run("schedule_plan.py", "--job", "portfolio", "--cron", "61 7 * * *", "--target", "cron")[0] == 1


# -- hooks -----------------------------------------------------------------------

def _hook(script: str, payload: dict, env: dict | None = None) -> tuple[int, str]:
    code, _, err = run(script, stdin=json.dumps(payload), env=env, folder=HOOKS)
    return code, err


def test_secret_guard_blocks_live_keys_and_allows_placeholders(tmp_path: Path) -> None:
    key = "AKIA" + "Q" * 16
    blocked = {"tool_name": "Write", "tool_input": {"file_path": str(tmp_path / "report.md"), "content": f"key={key}"}}
    code, err = _hook("secret_guard.py", blocked)
    assert code == 2 and "AWS access key" in err
    placeholder = {"tool_name": "Edit", "tool_input": {"file_path": "x.md", "new_string": "AKIAIOSFODNN7EXAMPLE"}}
    assert _hook("secret_guard.py", placeholder)[0] == 0
    store = {"tool_name": "Write", "tool_input": {"file_path": str(tmp_path / ".env"), "content": f"K={key}"}}
    assert _hook("secret_guard.py", store)[0] == 0
    assert _hook("secret_guard.py", blocked, env={"WEB_SEO_SECRET_GUARD": "off"})[0] == 0
    multi = {"tool_name": "MultiEdit", "tool_input": {"file_path": "a.py", "edits": [
        {"new_string": "ok"}, {"new_string": "ghp_" + "a" * 36}]}}
    assert _hook("secret_guard.py", multi)[0] == 2


def test_secret_guard_never_breaks_on_bad_input() -> None:
    code, _, _ = run("secret_guard.py", stdin="not json", folder=HOOKS)
    assert code == 0


def test_audit_hook_is_opt_in(tmp_path: Path) -> None:
    payload = {"tool_name": "Bash", "tool_input": {"command": "echo hi"}, "cwd": str(tmp_path), "session_id": "s1"}
    assert _hook("audit_hook.py", payload)[0] == 0
    assert not (tmp_path / "seo-workspace" / "audit" / "audit-log.jsonl").exists()
    assert _hook("audit_hook.py", payload, env={"WEB_SEO_AUDIT": "1"})[0] == 0
    log = tmp_path / "seo-workspace" / "audit" / "audit-log.jsonl"
    rec = json.loads(log.read_text(encoding="utf-8").splitlines()[0])
    assert rec["event"] == "tool.Bash" and rec["detail"]["target"] == "echo hi"


def test_hooks_json_registers_guard_and_audit_after_schema_hook() -> None:
    cfg = json.loads((HOOKS / "hooks.json").read_text(encoding="utf-8"))["hooks"]
    assert cfg["PostToolUse"][0]["hooks"][0]["args"][1].endswith("validate-schema.py")
    assert cfg["PostToolUse"][1]["hooks"][0]["args"][1].endswith("audit_hook.py")
    assert cfg["PreToolUse"][0]["hooks"][0]["args"][1].endswith("secret_guard.py")


def test_enterprise_scripts_are_in_runtime_allowlist() -> None:
    sys.path.insert(0, str(SCRIPTS))
    import runtime
    for name in ("audit_log.py", "workspace.py", "governance.py", "findings_export.py",
                 "portfolio_rollup.py", "schedule_plan.py"):
        assert name in runtime.ALLOWED_CORE_SCRIPTS
