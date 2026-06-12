#!/usr/bin/env python3
"""
Send daily analysis report via email as styled HTML.

Environment variables:
  SMTP_HOST       SMTP server (default: smtp.qq.com)
  SMTP_PORT       SMTP port (default: 465)
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
.commit-message { background:#f0f5ff;border-radius:4px;padding:6px 10px;margin:4px 0 8px 0;font-size:13px;font-weight:600;color:#1a1a2e;font-family:'SF Mono',Consolas,monospace; }
.section-header { font-size:11px;font-weight:700;color:#5a6370;text-transform:uppercase;letter-spacing:0.04em;padding:4px 0 2px 0;margin-top:8px;border-bottom:1px solid #e2e8f0; }
.section-body { padding:2px 0 4px 0; }
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


def build_html(repos, date_str, data_dir="data"):
    ps = []
    ps.append(f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>{CSS}</style></head><body>
<div class="container">
<div class="header"><h1>{icon('vllm')} vLLM Commit Report</h1><div class="date">{date_str}</div></div>""")

    any_data = False
    for repo in repos:
        repo_dir = repo_dir_name(repo)
        analysis = load_json(f"{data_dir}/{repo_dir}/analysis/{date_str}.json")
        short = repo.split("/")[-1]
        is_vllm = "ascend" not in repo

        if not analysis:
            ps.append(f"""<div class="card"><div class="card-title">{icon('vllm')} {short}</div><div class="empty">暂无分析数据</div></div>""")
            continue

        any_data = True
        commits = analysis.get("commits", [])

        commits_data = load_json(f"{data_dir}/{repo_dir}/commits/{date_str}.json") or {}
        message_map = {}
        for c in commits_data.get("commits", []):
            msg = c.get("message", "") or ""
            message_map[c["sha"]] = msg.split("\n")[0]
        daily_summary = analysis.get("daily_summary", "")

        # ── Stats ──
        ascend_count = sum(1 for c in commits if c.get("ascend_impact",{}).get("ascend_affected"))
        high_risk_count = sum(1 for c in commits if "high-risk" in c.get("tags", []))
        needs_test_count = sum(1 for c in commits if c.get("test_impact",{}).get("needs_test_update") or c.get("ascend_impact",{}).get("needs_test_update"))
        auto_count = sum(1 for c in commits if "自动判定" in c.get("comment", ""))

        ps.append(f"""<div class="card">
<div class="card-title">{icon('vllm') if is_vllm else icon('ascend')} {short} &middot; {len(commits)} commits</div>""")

        if daily_summary:
            ps.append(f"""<div class="summary-box"><div class="label">每日总结</div><div class="text">{daily_summary}</div></div>""")

        if is_vllm:
            ps.append(f"""<div class="stats">
<div class="stat"><div class="stat-value">{len(commits)}</div><div class="stat-label">总计</div></div>
<div class="stat"><div class="stat-value">{auto_count}</div><div class="stat-label">自动跳过</div></div>
<div class="stat ascend"><div class="stat-value">{ascend_count}</div><div class="stat-label">{icon('ascend')} 昇腾影响</div></div>
<div class="stat high-risk"><div class="stat-value">{high_risk_count}</div><div class="stat-label">{icon('high_risk')} 高风险</div></div>
<div class="stat test"><div class="stat-value">{needs_test_count}</div><div class="stat-label">{icon('test')} 需测试</div></div>
</div>""")
        else:
            ps.append(f"""<div class="stats">
<div class="stat"><div class="stat-value">{len(commits)}</div><div class="stat-label">总计</div></div>
<div class="stat"><div class="stat-value">{auto_count}</div><div class="stat-label">自动跳过</div></div>
<div class="stat high-risk"><div class="stat-value">{high_risk_count}</div><div class="stat-label">{icon('high_risk')} 高风险</div></div>
</div>""")

        # ── Daily Summary ──
        if daily_summary:
            pass

        # ── Classify commits ──
        high_risk = []
        ascend_affected = []
        test_impact = []
        others = []
        for c in commits:
            if "自动判定" in c.get("comment", ""):
                continue
            tags = c.get("tags", [])
            if "high-risk" in tags:
                high_risk.append(c)
            elif is_vllm and c.get("ascend_impact", {}).get("ascend_affected"):
                ascend_affected.append(c)
            elif c.get("test_impact", {}).get("needs_test_update") or c.get("ascend_impact", {}).get("needs_test_update"):
                test_impact.append(c)
            else:
                others.append(c)

        def render_commit(c):
            tags = c.get("tags", [])
            comment = c.get("comment", "")
            sha = c.get("sha", "")[:12]
            sha_full = c.get("sha", "")
            is_high_risk = "high-risk" in tags
            is_ascend = c.get("ascend_impact", {}).get("ascend_affected") is True
            title = comment.split("\n")[0] if comment else ""
            body = "\n".join(comment.split("\n")[1:]).strip() if comment else ""
            item_cls = "item-danger" if is_high_risk else ("item-ascend" if is_ascend else "item-normal")
            gh_url = f"https://github.com/{repo}/commit/{sha_full}"
            commit_msg = message_map.get(sha_full, "")
            lines = [f"""<div class="item {item_cls}">
<a class="item-sha" href="{gh_url}" target="_blank">{sha}</a> {tag_html(tags)}"""]
            if commit_msg:
                lines.append(f"""<div class="commit-message">{commit_msg[:200]}</div>""")
            if title:
                lines.append(f"""<div class="section-header">🤖 AI 分析结果</div><div class="section-body">{title}</div>""")
                if body:
                    short_body = body[:300] + ("…" if len(body) > 300 else "")
                    lines.append(f"""<div class="item-comment">{short_body}</div>""")
            ai = c.get("ascend_impact")
            if is_vllm and ai and ai.get("ascend_affected") is True:
                func = ai.get("functionality", "")
                test_imp = ai.get("testing", "")
                if func:
                    lines.append(f"""<div class="section-header">⬆ vllm-ascend 影响分析</div><div class="section-body">{func[:200]}</div>""")
                if test_imp:
                    lines.append(f"""<div class="section-header">🧪 测试影响分析</div><div class="section-body">{test_imp[:200]}</div>""")
            ti = c.get("test_impact")
            if ti and ti.get("needs_test_update"):
                reason = ti.get("reason", "")
                areas = ti.get("suggested_test_areas", [])
                if reason:
                    lines.append(f"""<div class="section-header">🧪 测试影响</div><div class="section-body">{reason[:200]}</div>""")
                if areas:
                    lines.append(f"""<div class="item-comment" style="color:#dd6b20;"><strong>Areas:</strong> {', '.join(areas[:5])}</div>""")
            lines.append("</div>")
            return "\n".join(lines)

        # ── ⚠️ 高风险 ──
        if high_risk:
            ps.append(f"""<div class="card-title" style="margin-top:14px;">⚠️ 高风险</div>""")
            for c in high_risk:
                ps.append(render_commit(c))

        # ── ⬆ vllm-ascend 影响（仅vllm主库） ──
        if is_vllm and ascend_affected:
            ps.append(f"""<div class="card-title" style="margin-top:14px;">⬆ vllm-ascend 影响</div>""")
            for c in ascend_affected:
                ps.append(render_commit(c))

        # ── 🧪 测试影响（仅vllm-ascend） ──
        if not is_vllm and test_impact:
            ps.append(f"""<div class="card-title" style="margin-top:14px;">🧪 测试影响</div>""")
            for c in test_impact:
                ps.append(render_commit(c))

        # ── 📋 其他 ──
        if others:
            ps.append(f"""<div class="card-title" style="margin-top:14px;">📋 其他</div>""")
            for c in others:
                ps.append(render_commit(c))

        ps.append("</div>")

        if high_risk_count > 0 or ascend_count > 0:
            ps.append(f"""<div style="text-align:center;margin-bottom:16px;"><a href="https://github.com/vllm-ascend/vllm-report" style="display:inline-block;background:#1a1a2e;color:#fff;text-decoration:none;padding:8px 20px;border-radius:6px;font-size:13px;font-weight:500;">View on Dashboard →</a></div>""")

    if not any_data:
        ps.append(f"""<div class="card"><div class="empty">今日无新 commit 数据</div></div>""")

    ps.append(f"""<div class="footer">Generated by vLLM Report Bot · {datetime.now(TZ_CN).strftime('%Y-%m-%d %H:%M CST')}</div></div></body></html>""")
    return "\n".join(ps)


def send_email(subject, html_body):
    host = os.environ.get("SMTP_HOST") or "smtp.qq.com"
    port_str = os.environ.get("SMTP_PORT") or "465"
    port = int(port_str) if port_str else 465
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

    print(f"From: {user}, To: {recipients}")
    if not recipients or not recipients[0]:
        print("No recipients configured, skipping")
        return

    last_err = None
    for try_port in (465, 587):
        try:
            if try_port == 465:
                server = smtplib.SMTP_SSL(host, 465, timeout=30)
            else:
                server = smtplib.SMTP(host, 587, timeout=30)
                server.starttls()
            server.ehlo()
            server.login(user, password)
            server.sendmail(from_addr, recipients, msg.as_string())
            server.quit()
            print(f"Email sent via port {try_port}")
            return
        except Exception as e:
            last_err = e
            print(f"Port {try_port} failed: {e}")
            continue
    print(f"Failed to send email: {last_err}")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Send daily analysis report via email")
    parser.add_argument("date", nargs="?", default=None, help="Date (YYYY-MM-DD), defaults to today")
    parser.add_argument("--data-dir", default="data", help="Data directory")
    args = parser.parse_args()

    repos = ["vllm-project/vllm", "vllm-project/vllm-ascend"]
    date_str = args.date or datetime.now(TZ_CN).strftime("%Y-%m-%d")

    html = build_html(repos, date_str, data_dir=args.data_dir)
    print(html[:500] + "...")
    subject = f"vLLM Report — {date_str}"
    send_email(subject, html)


if __name__ == "__main__":
    main()
