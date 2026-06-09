#!/usr/bin/env python3
"""
Send daily analysis report via email as styled HTML.

Environment variables:
  SMTP_HOST       SMTP server (default: smtp.gmail.com)
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
body { margin:0; padding:0; background:#f4f5f7; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif; }
.container { max-width:600px; margin:0 auto; padding:20px; }
.header { background:#1a1a2e; border-radius:10px; padding:24px; text-align:center; margin-bottom:20px; }
.header h1 { color:#3dd68c; margin:0; font-size:20px; font-weight:700; }
.header .date { color:#8b949e; font-size:13px; margin-top:4px; }
.card { background:#fff; border-radius:8px; padding:20px; margin-bottom:16px; box-shadow:0 1px 3px rgba(0,0,0,0.08); }
.card-title { font-size:13px; font-weight:600; color:#5a6370; text-transform:uppercase; letter-spacing:0.05em; margin-bottom:12px; }
.stats { display:flex; gap:8px; flex-wrap:wrap; margin-bottom:14px; }
.stat { background:#f0f2f5; border-radius:6px; padding:10px 14px; text-align:center; min-width:70px; flex:1; }
.stat-value { font-size:22px; font-weight:700; color:#1a1a2e; line-height:1.2; }
.stat-label { font-size:11px; color:#8b949e; margin-top:2px; }
.stat.high-risk .stat-value { color:#e53e3e; }
.stat.ascend .stat-value { color:#3182ce; }
.stat.test .stat-value { color:#dd6b20; }
.item { border-left:3px solid #e2e8f0; padding:8px 12px; margin-bottom:6px; font-size:13px; line-height:1.5; color:#2d3748; }
.item-danger { border-left-color:#e53e3e; background:#fff5f5; }
.item-ascend { border-left-color:#3182ce; background:#f0f7ff; }
.item-sha { color:#3182ce; font-family:'SF Mono',Consolas,monospace; font-size:12px; font-weight:600; }
.item-text { color:#4a5568; margin-top:2px; font-size:12px; }
.heatmap { margin-top:10px; }
.hm-row { display:flex; align-items:center; gap:8px; margin-bottom:4px; font-size:12px; }
.hm-path { min-width:140px; flex:1; color:#4a5568; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; font-family:'SF Mono',Consolas,monospace; font-size:11px; }
.hm-bar { flex:1; height:12px; background:#edf2f7; border-radius:3px; overflow:hidden; }
.hm-fill { height:100%; background:#3dd68c; border-radius:3px; opacity:0.7; }
.hm-count { width:28px; text-align:right; color:#718096; font-size:11px; }
.footer { text-align:center; font-size:11px; color:#a0aec0; margin-top:24px; }
.coverage { display:flex; align-items:center; gap:8px; margin-top:12px; font-size:12px; color:#718096; }
.coverage-track { flex:1; height:6px; background:#edf2f7; border-radius:3px; overflow:hidden; }
.coverage-fill { height:100%; border-radius:3px; }
.empty { color:#a0aec0; font-size:13px; text-align:center; padding:20px; }
"""


def load_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def icon(name):
    icons = {
        "vllm": "⚡", "ascend": "⬆", "high_risk": "⚠️",
        "test": "🧪", "check": "✅", "warning": "⚡",
    }
    return icons.get(name, "•")


def analyze_day(repo, date_str):
    repo_dir = repo_dir_name(repo)
    analysis = load_json(f"{DATA_DIR}/{repo_dir}/analysis/{date_str}.json")
    if not analysis:
        return None

    commits = analysis.get("commits", [])
    summary = {
        "repo": repo,
        "total": len(commits),
        "high_risk": 0,
        "ascend_affected": 0,
        "needs_test": 0,
        "auto_triaged": 0,
        "high_risk_items": [],
        "ascend_items": [],
        "modules": {},
    }

    for c in commits:
        tags = c.get("tags", [])
        if "high-risk" in tags:
            summary["high_risk"] += 1
            summary["high_risk_items"].append({
                "sha": c["sha"][:8],
                "text": c.get("comment", "").split("\n")[0][:120],
            })

        ai = c.get("ascend_impact")
        if ai and ai.get("ascend_affected") is True:
            summary["ascend_affected"] += 1
            summary["ascend_items"].append({
                "sha": c["sha"][:8],
                "text": ai.get("functionality", "")[:100],
            })

        ti = c.get("test_impact")
        if ti and ti.get("needs_test_update"):
            summary["needs_test"] += 1

        if "自动判定" in c.get("comment", ""):
            summary["auto_triaged"] += 1

        for f in c.get("files", []):
            parts = f["filename"].split("/")
            key = "/".join(parts[:3]) + "/" if len(parts) >= 3 else f["filename"]
            summary["modules"][key] = summary["modules"].get(key, 0) + 1

    mods = sorted(summary["modules"].items(), key=lambda x: -x[1])[:8]
    summary["modules"] = mods
    return summary


def build_html(repos, date_str):
    ps = []
    ps.append(f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>{CSS}</style></head><body>
<div class="container">
<div class="header"><h1>{icon('vllm')} vLLM Commit Report</h1><div class="date">{date_str}</div></div>""")

    any_data = False
    for repo in repos:
        s = analyze_day(repo, date_str)
        short = repo.split("/")[-1]
        if not s:
            ps.append(f"""<div class="card"><div class="card-title">{icon('vllm')} {short}</div><div class="empty">暂无分析数据</div></div>""")
            continue

        any_data = True
        has_issues = s["high_risk"] > 0 or s["ascend_affected"] > 0

        ps.append(f"""<div class="card">
<div class="card-title">{icon('vllm') if 'ascend' not in repo else icon('ascend')} {short}</div>
<div class="stats">
<div class="stat"><div class="stat-value">{s['total']}</div><div class="stat-label">Commits</div></div>
<div class="stat"><div class="stat-value">{s['auto_triaged']}</div><div class="stat-label">Auto-skipped</div></div>
<div class="stat ascend"><div class="stat-value">{s['ascend_affected']}</div><div class="stat-label">{icon('ascend')} Ascend</div></div>
<div class="stat high-risk"><div class="stat-value">{s['high_risk']}</div><div class="stat-label">{icon('high_risk')} High Risk</div></div>
<div class="stat test"><div class="stat-value">{s['needs_test']}</div><div class="stat-label">{icon('test')} Needs Test</div></div>
</div>""")

        if s["high_risk_items"]:
            ps.append(f"""<div style="margin-bottom:4px;font-size:12px;font-weight:600;color:#e53e3e;">{icon('high_risk')} High Risk</div>""")
            for it in s["high_risk_items"]:
                ps.append(f"""<div class="item item-danger"><span class="item-sha">{it['sha']}</span><div class="item-text">{it['text']}</div></div>""")

        if s["ascend_items"]:
            ps.append(f"""<div style="margin:8px 0 4px;font-size:12px;font-weight:600;color:#3182ce;">{icon('ascend')} Affects vllm-ascend</div>""")
            for it in s["ascend_items"]:
                ps.append(f"""<div class="item item-ascend"><span class="item-sha">{it['sha']}</span><div class="item-text">{it['text']}</div></div>""")

        if s["modules"]:
            max_n = max(n for _, n in s["modules"]) if s["modules"] else 1
            rows = []
            for path, n in s["modules"]:
                pct = round(n / max_n * 100)
                rows.append(f"""<div class="hm-row"><span class="hm-path">{path}</span><div class="hm-bar"><div class="hm-fill" style="width:{pct}%"></div></div><span class="hm-count">{n}</span></div>""")
            ps.append(f"""<div style="margin-top:10px;padding-top:10px;border-top:1px solid #e2e8f0;"><div style="font-size:11px;color:#718096;margin-bottom:6px;">Hot modules</div>{"".join(rows)}</div>""")

        # Coverage indicator for this repo
        repo_dir2 = repo_dir_name(repo)
        commits_idx = load_json(f"{DATA_DIR}/{repo_dir2}/dates.json")
        analysis_idx = load_json(f"{DATA_DIR}/{repo_dir2}/analysis-dates.json")
        if commits_idx and analysis_idx:
            all_d = set(commits_idx.get("dates", []))
            done_d = set(analysis_idx.get("dates", []))
            analyzed = len(all_d & done_d)
            total = len(all_d)
            pct = round(analyzed / total * 100) if total else 0
            color = "#3dd68c" if analyzed == total else ("#d29922" if total - analyzed < 5 else "#e53e3e")
            ps.append(f"""<div class="coverage"><span>Coverage</span><div class="coverage-track"><div class="coverage-fill" style="width:{pct}%;background:{color}"></div></div><span>{analyzed}/{total}</span></div>""")

        ps.append("</div>")

        if has_issues:
            url = f"https://github.com/vllm-ascend/vllm-report"
            ps.append(f"""<div style="text-align:center;margin-bottom:16px;"><a href="{url}" style="display:inline-block;background:#1a1a2e;color:#fff;text-decoration:none;padding:8px 20px;border-radius:6px;font-size:13px;font-weight:500;">View on Dashboard →</a></div>""")

    if not any_data:
        ps.append(f"""<div class="card"><div class="empty">今日无新 commit 数据</div></div>""")

    ps.append(f"""<div class="footer">Generated by vLLM Report Bot · {datetime.now(TZ_CN).strftime('%Y-%m-%d %H:%M CST')}</div></div></body></html>""")
    return "\n".join(ps)


def send_email(subject, html_body):
    host = os.environ.get("SMTP_HOST", "smtp.qq.com")
    port = int(os.environ.get("SMTP_PORT", 587))
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
