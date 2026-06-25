#!/usr/bin/env python3
"""
Send daily analysis report via email as a markdown-styled block list.
"""
import json
import os
import re
import smtplib
import sys
from email.mime.text import MIMEText
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from source_repo import repo_dir_name

TZ_CN = timezone(timedelta(hours=8))
DATA_DIR = "data"


def load_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


# Display width of a character: CJK / fullwidth = 2, others = 1
_CJK_RE = re.compile(r"[\u3000-\u303f\u4e00-\u9fff\uff00-\uffef\u2014\u2018\u2019\u201c\u201d\u2026]")


def _char_width(ch):
    return 2 if _CJK_RE.match(ch) else 1


def wrap_text(s, width):
    """Wrap text by display width, breaking freely on CJK chars.
    Returns a list of lines."""
    if not s:
        return [""]
    out = []
    cur = ""
    cur_w = 0
    for ch in s:
        ch_w = _char_width(ch)
        if cur_w + ch_w > width and cur:
            out.append(cur)
            cur = ch
            cur_w = ch_w
        else:
            cur += ch
            cur_w += ch_w
    if cur:
        out.append(cur)
    return out or [""]


def _get_impact_fields(commit, repo):
    """Extract (ascend_info, test_info, test_areas) per repo schema.

    vllm repo uses ascend_impact.{functionality, testing, suggested_test_areas}.
    vllm-ascend repo uses test_impact.{reason, suggested_test_areas}.
    """
    if "ascend" in repo:
        ti = commit.get("test_impact") or {}
        return ("", ti.get("reason", "") or "", ti.get("suggested_test_areas") or [])
    ai = commit.get("ascend_impact") or {}
    ascend_info = ai.get("functionality", "") or "" if ai.get("ascend_affected") else ""
    test_info = ai.get("testing", "") or "" if ai.get("needs_test_update") else ""
    return (ascend_info, test_info, ai.get("suggested_test_areas") or [])


def build_table(repos, date_str, data_dir="data"):
    out = []
    out.append(f"vLLM Commit Report — {date_str}")
    out.append("=" * 80)

    for repo in repos:
        repo_dir = repo_dir_name(repo)
        analysis = load_json(f"{data_dir}/{repo_dir}/analysis/{date_str}.json")
        commits_data = load_json(f"{data_dir}/{repo_dir}/commits/{date_str}.json") or {}
        short = repo.split("/")[-1]

        out.append("")
        out.append("## " + short)
        out.append("-" * 60)

        if not analysis:
            out.append("  (暂无分析数据)")
            continue

        commits = analysis.get("commits", [])
        daily_summary = analysis.get("daily_summary", "")

        message_map = {}
        for c in commits_data.get("commits", []):
            msg = c.get("message", "") or ""
            message_map[c["sha"]] = msg.split("\n")[0]

        # Filter out auto-skipped (Chores)
        visible = [c for c in commits if "自动判定" not in c.get("comment", "")]

        # All visible commits are categorized as either "primary" (needs attention)
        # or "other". Grouping is per-repo because the schema differs.
        if "ascend" in repo:
            primary = [c for c in visible if (c.get("test_impact") or {}).get("needs_test_update")]
            other = [c for c in visible if not (c.get("test_impact") or {}).get("needs_test_update")]
        else:
            primary = [
                c for c in visible
                if (c.get("ascend_impact") or {}).get("ascend_affected")
                or (c.get("ascend_impact") or {}).get("needs_test_update")
            ]
            other = [c for c in visible if c not in primary]

        if not visible:
            auto_count = len(commits)
            out.append(f"统计: 总计 {len(commits)}  |  全部为自动跳过")
            if daily_summary:
                out.append("")
                out.append("每日总结:")
                for ln in wrap_text(daily_summary, 70):
                    out.append("  " + ln)
            out.append("")
            out.append("  (所有 commit 均为自动跳过)")
            continue

        ascend_count = sum(1 for c in visible if (c.get("ascend_impact") or {}).get("ascend_affected"))
        high_risk_count = sum(1 for c in visible if "high-risk" in c.get("tags", []))
        needs_test_count = sum(1 for c in visible if _get_impact_fields(c, repo)[1])
        auto_count = len(commits) - len(visible)

        if "ascend" in repo:
            stats_line = (
                f"统计: 总计 {len(commits)}  |  高风险 {high_risk_count}  |  "
                f"需新增测试 {needs_test_count}  |  自动跳过 {auto_count}"
            )
        else:
            stats_line = (
                f"统计: 总计 {len(commits)}  |  高风险 {high_risk_count}  |  "
                f"昇腾影响 {ascend_count}  |  需新增测试 {needs_test_count}  |  "
                f"自动跳过 {auto_count}"
            )
        out.append(stats_line)

        if daily_summary:
            out.append("")
            out.append("每日总结:")
            for ln in wrap_text(daily_summary, 70):
                out.append("  " + ln)

        def render_block(c, idx):
            sha_full = c.get("sha", "")
            sha = sha_full[:8]
            gh_url = f"https://github.com/{repo}/commit/{sha_full}"
            commit_title = message_map.get(sha_full, sha)
            tags = c.get("tags", [])
            comment = c.get("comment", "") or ""
            ai_first = comment.split("\n")[0] if comment else ""
            ascend_info, test_info, test_areas = _get_impact_fields(c, repo)

            lines = []
            tag_str = " ".join(f"[{t}]" for t in tags)
            lines.append(f"{idx}. {commit_title}" + (f"  {tag_str}" if tag_str else ""))
            lines.append(f"   🔗 {sha}  →  {gh_url}")
            if ai_first:
                lines.append("   AI 分析:")
                for ln in wrap_text(ai_first, 70):
                    lines.append("     " + ln)
            if ascend_info and ascend_info not in ("无影响", "无影响。", "无直接影响", "无直接影响。"):
                lines.append("   昇腾影响:")
                for ln in wrap_text(ascend_info, 70):
                    lines.append("     " + ln)
            if test_info and test_info not in ("无影响", "无影响。", "无直接影响", "无直接影响。"):
                lines.append("   ⚠ 需新增测试:")
                for ln in wrap_text(test_info, 70):
                    lines.append("     " + ln)
                if test_areas:
                    lines.append("   建议范围: " + ", ".join(test_areas))
            return lines

        if primary:
            out.append("")
            out.append("▎昇腾影响 / 需新增测试" if "ascend" not in repo else "▎需新增测试")
            for i, c in enumerate(primary, 1):
                for ln in render_block(c, i):
                    out.append(ln)
                out.append("")

        if other:
            out.append("")
            out.append("▎其他变更")
            for i, c in enumerate(other, 1):
                for ln in render_block(c, i):
                    out.append(ln)
                out.append("")

    out.append("")
    out.append("=" * 80)
    out.append(f"Generated by vLLM Report Bot · {datetime.now(TZ_CN).strftime('%Y-%m-%d %H:%M CST')}")
    return "\n".join(out)


def send_email(subject, body):
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

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = ", ".join(recipients)

    print(f"From: {user}, To: {recipients}")

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

    body = build_table(repos, date_str, data_dir=args.data_dir)
    print(body)
    subject = f"vLLM Report — {date_str}"
    send_email(subject, body)


if __name__ == "__main__":
    main()
