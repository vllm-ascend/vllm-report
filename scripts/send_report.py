#!/usr/bin/env python3
"""
Send daily analysis report via email as styled HTML.

Environment variables:
  SMTP_HOST       SMTP server (default: smtp.qq.com)
  SMTP_PORT       SMTP port (default: 587)
  SMTP_USER       SMTP username
  SMTP_PASS       SMTP password or app password
  NOTIFY_EMAIL    Recipient email address(es), comma-separated
  FROM_EMAIL      Sender address (default: SMTP_USER)
"""
import json
import os
import smtplib
import sys
from email.mime.text import MIMEText
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from source_repo import repo_dir_name

TZ_CN = timezone(timedelta(hours=8))
DATA_DIR = "data"

CSS = """
body { margin:0;padding:0;background:#f4f5f7;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif; }
.container { max-width:600px;margin:0 auto;padding:20px; }
.header { background:#1a1a2e;border-radius:10px;padding:24px;text-align:center;margin-bottom:20px; }
.header h1 { color:#3dd68c;margin:0;font-size:20px;font-weight:700; }
.header .date { color:#8b949e;font-size:13px;margin-top:4px; }
.card { background:#fff;border-radius:8px;padding:20px;margin-bottom:16px;box-shadow:0 1px 3px rgba(0,0,0,0.08); }
.card-title { font-size:13px;font-weight:600;color:#5a6370;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:12px; }
.stats { display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px; }
.stat { background:#f0f2f5;border-radius:6px;padding:10px 14px;text-align:center;min-width:70px;flex:1; }
.stat-value { font-size:22px;font-weight:700;color:#1a1a2e;line-height:1.2; }
.stat-label { font-size:11px;color:#8b949e;margin-top:2px; }
.stat.high-risk .stat-value { color:#e53e3e; }
.stat.ascend .stat-value { color:#3182ce; }
.stat.test .stat-value { color:#dd6b20; }
.item { border-left:3px solid #e2e8f0;padding:8px 12px;margin-bottom:6px;font-size:13px;line-height:1.5;color:#2d3748; }
.item-danger { border-left-color:#e53e3e;background:#fff5f5; }
.item-ascend { border-left-color:#3182ce;background:#f0f7ff; }
.item-normal { border-left-color:#3dd68c;background:#f0faf4; }
.item-sha { color:#3182ce;font-family:'SF Mono',Consolas,monospace;font-size:12px;font-weight:600;text-decoration:none; }
.item-sha:hover { text-decoration:underline; }
.item-title { color:#2d3748;font-size:13px;font-weight:600;margin:2px 0; }
.item-comment { color:#4a5568;font-size:12px;margin:2px 0;line-height:1.5; }
.item-meta { display:flex;gap:6px;flex-wrap:wrap;margin-top:3px; }
.tag { display:inline-block;padding:1px 6px;border-radius:3px;font-size:10px;font-weight:600; }
.tag-feature { background:#e6f7ff;color:#1890ff; }
.tag-bugfix { background:#fff2f0;color:#e53e3e; }
.tag-refactor { background:#f0f5ff;color:#2f54eb; }
.tag-performance { background:#f6ffed;color:#52c41a; }
.tag-risk-high { background:#fff5f5;color:#e53e3e; }
.tag-risk { background:#fffbe6;color:#d48806; }
.tag-chore { background:#f5f5f5;color:#8c8c8c; }
.summary-box { background:#f8f9fa;border-radius:6px;padding:12px 16px;margin-bottom:14px;border-left:3px solid #3dd68c; }
.summary-box .label { font-size:11px;font-weight:600;color:#5a6370;text-transform:uppercase;letter-spacing:0.04em;margin-bottom:4px; }
.summary-box .text { color:#4a5568;font-size:12px;line-height:1.6; }
.heatmap { margin-top:12px; }
.hm-row { display:flex;align-items:center;gap:8px;margin-bottom:4px;font-size:12px; }
.hm-path { min-width:140px;flex:1;color:#4a5568;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-family:'SF Mono',Consolas,monospace;font-size:11px; }
.hm-bar { flex:1;height:12px;background:#edf2f7;border-radius:3px;overflow:hidden; }
.hm-fill { height:100%;background:#3dd68c;border-radius:3px;opacity:0.7; }
.hm-count { width:28px;text-align:right;color:#718096;font-size:11px; }
.footer { text-align:center;font-size:11px;color:#a0aec0;margin-top:24px; }
.coverage { display:flex;align-items:center;gap:8px;margin-top:12px;font-size:12px;color:#718096; }
.coverage-track { flex:1;height:6px;background:#edf2f7;border-radius:3px;overflow:hidden; }
.coverage-fill { height:100%;border-radius:3px; }
.empty { color:#a0aec0;font-size:13px;text-align:center;padding:20px; }
.detail-divider { border:none;border-top:1px solid #e2e8f0;margin:16px 0; }
.repo-link { color:#8b949e;font-size:12px; }
"""


def load_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def icon(name):
    icons = {"vllm":"⚡","ascend":"⬆","high_risk":"⚠️","test":"🧪","check":"✅","warning":"⚡"}
    return icons.get(name, "•")


def tag_html(tags):
    if not tags:
        return ""
    parts = []
    for t in tags:
        cls = "tag"
        if t in ("feature","bugfix","refactor","performance","chore"):
            cls += f" tag-{t}"
        elif "risk" in t:
            cls += " tag-risk" if t != "high-risk" else " tag-risk-high"
        else:
            cls += " tag-chore"
        parts.append(f'<span class="{cls}">{t}</span>')
    return " ".join(parts)


def load_commit_files(repo, date_str):
    """Load full commit data with file lists for a date."""
    repo_dir = repo_dir_name(repo)
    data = load_json(f"{DATA_DIR}/{repo_dir}/commits/{date_str}.json")
    if not data:
        return {}
    files_by_sha = {}
    for c in data.get("commits", []):
        files_by_sha[c["sha"]] = c.get("files", [])
    return files_by_sha


def build_html(repos, date_str):
    ps = []
    ps.append(f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>{CSS}</style></head><body>
<div class="container">
<div class="header"><h1>{icon('vllm')} vLLM Commit Report</h1><div class="date">{date_str}</div></div>""")

    any_data = False
    for repo in repos:
        repo_dir = repo_dir_name(repo)
        analysis = load_json(f"{DATA_DIR}/{repo_dir}/analysis/{date_str}.json")
        files_map = load_commit_files(repo, date_str)
        short = repo.split("/")[-1]
        is_vllm = "ascend" not in repo

        if not analysis:
            ps.append(f"""<div class="card"><div class="card-title">{icon('vllm')} {short}</div><div class="empty">暂无分析数据</div></div>""")
            continue

        any_data = True
        commits = analysis.get("commits", [])
        daily_summary = analysis.get("daily_summary", "")

        # ── Stats ──
        ascend_count = sum(1 for c in commits if c.get("ascend_impact",{}).get("ascend_affected"))
        high_risk_count = sum(1 for c in commits if "high-risk" in c.get("tags", []))
        needs_test_count = sum(1 for c in commits if c.get("test_impact",{}).get("needs_test_update") or c.get("ascend_impact",{}).get("needs_test_update"))
        auto_count = sum(1 for c in commits if "自动判定" in c.get("comment", ""))

        ps.append(f"""<div class="card">
<div class="card-title">{icon('vllm') if is_vllm else icon('ascend')} {short} &middot; {len(commits)} commits</div>""")

        if daily_summary:
            ps.append(f"""<div class="summary-box"><div class="label">Daily Summary</div><div class="text">{daily_summary}</div></div>""")

        ps.append(f"""<div class="stats">
<div class="stat"><div class="stat-value">{len(commits)}</div><div class="stat-label">Total</div></div>
<div class="stat"><div class="stat-value">{auto_count}</div><div class="stat-label">Auto-skip</div></div>
<div class="stat ascend"><div class="stat-value">{ascend_count}</div><div class="stat-label">{icon('ascend')} Ascend</div></div>
<div class="stat high-risk"><div class="stat-value">{high_risk_count}</div><div class="stat-label">{icon('high_risk')} High Risk</div></div>
<div class="stat test"><div class="stat-value">{needs_test_count}</div><div class="stat-label">{icon('test')} Needs Test</div></div>
</div>""")

        # ── Daily Summary ──
        if daily_summary:
            pass  # already shown above

        # ── Per-commit detail list ──
        for i, c in enumerate(commits):
            tags = c.get("tags", [])
            comment = c.get("comment", "")
            sha = c["sha"][:12]
            sha_full = c["sha"]
            is_high_risk = "high-risk" in tags
            is_ascend = c.get("ascend_impact",{}).get("ascend_affected") is True
            is_auto = "自动判定" in comment

            if is_auto:
                continue  # skip auto-triaged commits in detail list

            title = comment.split("\n")[0] if comment else ""
            body = "\n".join(comment.split("\n")[1:]).strip() if comment else ""

            item_cls = "item-danger" if is_high_risk else ("item-ascend" if is_ascend else "item-normal")
            gh_url = f"https://github.com/{repo}/commit/{sha_full}"

            ps.append(f"""<div class="item {item_cls}">
<a class="item-sha" href="{gh_url}" target="_blank">{sha}</a> {tag_html(tags)}
<div class="item-title">{title}</div>""")

            if body:
                # Truncate to first 300 chars for email
                short_body = body[:300] + ("…" if len(body) > 300 else "")
                ps.append(f"""<div class="item-comment">{short_body}</div>""")

            # Ascend impact details
            ai = c.get("ascend_impact")
            if ai and ai.get("ascend_affected") is True:
                func = ai.get("functionality", "")
                test_imp = ai.get("testing", "")
                if func:
                    ps.append(f"""<div class="item-comment" style="color:#3182ce;"><strong>⬆ Ascend:</strong> {func[:200]}</div>""")
                if test_imp:
                    ps.append(f"""<div class="item-comment" style="color:#3182ce;"><strong>  Testing:</strong> {test_imp[:200]}</div>""")

            # Test impact details
            ti = c.get("test_impact")
            if ti and ti.get("needs_test_update"):
                reason = ti.get("reason", "")
                areas = ti.get("suggested_test_areas", [])
                if reason:
                    ps.append(f"""<div class="item-comment" style="color:#dd6b20;"><strong>🧪 Test:</strong> {reason[:200]}</div>""")
                if areas:
                    ps.append(f"""<div class="item-comment" style="color:#dd6b20;"><strong>  Areas:</strong> {', '.join(areas[:5])}</div>""")

            # Changed files
            files = files_map.get(sha_full, [])
            if files:
                file_names = [f["filename"] for f in files[:5]]
                file_text = "; ".join(file_names)
                if len(files) > 5:
                    file_text += f" … +{len(files)-5} more"
                ps.append(f"""<div class="item-comment" style="color:#8b949e;font-size:11px;"><strong>Files:</strong> {file_text}</div>""")

            # Module-level stats
            mod_paths = {}
            for f in files:
                parts = f["filename"].split("/")
                key = "/".join(parts[:3]) + "/" if len(parts) >= 3 else f["filename"]
                mod_paths[key] = mod_paths.get(key, 0) + 1
            if mod_paths:
                mod_text = "; ".join(sorted(mod_paths.keys()))
                ps.append(f"""<div class="item-comment" style="color:#8b949e;font-size:11px;"><strong>Modules:</strong> {mod_text}</div>""")

            ps.append("</div>")

        # ── Module heatmap ──
        mod_counts = {}
        for c in commits:
            files = files_map.get(c["sha"], [])
            for f in files:
                parts = f["filename"].split("/")
                key = "/".join(parts[:3]) + "/" if len(parts) >= 3 else f["filename"]
                mod_counts[key] = mod_counts.get(key, 0) + 1
        top_mods = sorted(mod_counts.items(), key=lambda x: -x[1])[:8]

        if top_mods:
            max_n = max(n for _, n in top_mods)
            rows = []
            for path, n in top_mods:
                pct = round(n / max_n * 100)
                rows.append(f"""<div class="hm-row"><span class="hm-path">{path}</span><div class="hm-bar"><div class="hm-fill" style="width:{pct}%"></div></div><span class="hm-count">{n}</span></div>""")
            ps.append(f"""<div style="margin-top:12px;padding-top:12px;border-top:1px solid #e2e8f0;"><div style="font-size:11px;color:#718096;margin-bottom:6px;">Hot modules</div>{"".join(rows)}</div>""")

        # ── Coverage ──
        commits_idx = load_json(f"{DATA_DIR}/{repo_dir}/dates.json")
        analysis_idx = load_json(f"{DATA_DIR}/{repo_dir}/analysis-dates.json")
        if commits_idx and analysis_idx:
            all_d = set(commits_idx.get("dates", []))
            done_d = set(analysis_idx.get("dates", []))
            analyzed = len(all_d & done_d)
            total = len(all_d)
            pct = round(analyzed / total * 100) if total else 0
            color = "#3dd68c" if analyzed == total else ("#d29922" if total - analyzed < 5 else "#e53e3e")
            ps.append(f"""<div class="coverage"><span>Coverage</span><div class="coverage-track"><div class="coverage-fill" style="width:{pct}%;background:{color}"></div></div><span>{analyzed}/{total}</span></div>""")

        ps.append("</div>")

        if high_risk_count > 0 or ascend_count > 0:
            ps.append(f"""<div style="text-align:center;margin-bottom:16px;"><a href="https://github.com/vllm-ascend/vllm-report" style="display:inline-block;background:#1a1a2e;color:#fff;text-decoration:none;padding:8px 20px;border-radius:6px;font-size:13px;font-weight:500;">View on Dashboard →</a></div>""")

    if not any_data:
        ps.append(f"""<div class="card"><div class="empty">今日无新 commit 数据</div></div>""")

    ps.append(f"""<div class="footer">Generated by vLLM Report Bot · {datetime.now(TZ_CN).strftime('%Y-%m-%d %H:%M CST')}</div></div></body></html>""")
    return "\n".join(ps)


def send_email(subject, html_body):
    host = os.environ.get("SMTP_HOST", "smtp.qq.com")
    port_str = os.environ.get("SMTP_PORT", "587")
    port = int(port_str) if port_str else 587
    user = os.environ.get("SMTP_USER", "")
    password = os.environ.get("SMTP_PASS", "")
    recipients = os.environ.get("NOTIFY_EMAIL", "").split(",")
    from_addr = os.environ.get("FROM_EMAIL", user)

    if not user or not password or not recipients[0]:
        print("SMTP not configured — skipping email")
        return

    msg = MIMEText(html_body, "html", "utf-8")
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = ", ".join(recipients)

    try:
        with smtplib.SMTP(host, port) as server:
            server.starttls()
            server.login(user, password)
            server.sendmail(from_addr, recipients, msg.as_string())
        print(f"Email sent to {', '.join(recipients)}")
    except Exception as e:
        print(f"Failed to send email: {e}")


def main():
    repos = ["vllm-project/vllm", "vllm-project/vllm-ascend"]
    date_str = datetime.now(TZ_CN).strftime("%Y-%m-%d")
    if len(sys.argv) > 1:
        date_str = sys.argv[1]

    html = build_html(repos, date_str)
    print(html[:500] + "...")
    subject = f"vLLM Report — {date_str}"
    send_email(subject, html)


if __name__ == "__main__":
    main()
