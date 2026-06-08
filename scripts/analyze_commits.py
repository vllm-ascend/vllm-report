#!/usr/bin/env python3
import argparse
import json
import os
import sys
import subprocess
import tempfile
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from source_repo import ensure_repo, repo_dir_name

TZ_CN = timezone(timedelta(hours=8))

PROMPT_TEMPLATE = """你是一个代码变更分析专家。请对以下 commit 逐个进行分析。

## 仓库信息
- 仓库：{repo}
- 日期：{date}
- 分支：main

## 项目架构上下文
以下是该项目的架构摘要，请基于此上下文进行分析，避免对项目结构和模块关系进行猜测：
{context_section}

## 本地源码路径
{source_section}

## 待分析 commits
{commits_json}

## 分析要求
对每个 commit 逐一分析，输出以下内容：

1. **comment**：对该 commit 的分析评论（变更意图、实现方式、潜在风险）。分析时请参考上面的架构上下文，准确判断变更涉及的模块和影响范围，不要硬猜
2. **tags**：分类标签，从以下候选中选择或新增：
   - 类型：feature, bugfix, refactor, performance, docs, test, chore, ci
   - 风险：high-risk, medium-risk, low-risk
   - 模块：根据架构上下文中的模块定义标注（如 attention, scheduler, tokenizer, model-runner 等）
 3. {test_requirement}
 4. {ascend_requirement}

另外请提供以下两段摘要放在 JSON 顶层：

5. **daily_summary**：一段话总结当日变更的主要方向和重点
6. {test_summary_desc}
7. {ascend_summary_desc}

## 输出格式
严格输出以下 JSON 格式，不要输出任何其他内容：
```json
{{
  "date": "{date}",
  "repo": "{repo}",
  "generated_at": "<当前时间，UTC+8>",
  "daily_summary": "<当日整体摘要>",
  {test_summary_field}
  {ascend_summary_field}
  "commits": [
    {{
      "sha": "<commit sha>",
      "comment": "<分析评论>",
      "tags": ["<tag1>", "<tag2>"]{test_commit_field}{ascend_commit_field}
    }}
  ]
}}
```"""

ASCEND_IMPACT_VLLM = "（仅 vllm 仓库需要填写）：评估对 vllm-ascend 项目的影响"
ASCEND_IMPACT_ASCEND = "（vllm-ascend 仓库无需填写，填写 null 即可）"

VLLM_ASCEND_REQUIREMENT = """**ascend_impact**（仅 vllm 仓库需要填写）：评估对 vllm-ascend 项目的影响
   - functionality：功能层面的影响（参考架构上下文中的硬件适配层信息判断）
   - testing：测试层面的影响
   - needs_test_update：vllm-ascend 是否因此变更需要新增、删除或更新测试用例（布尔值）
   - suggested_test_areas：如果 needs_test_update 为 true，建议变更的文件或模块（列表）
   - 如果该 commit 不影响 vllm-ascend，则 functionality 和 testing 填写"无影响"，needs_test_update 填 false"""
ASCEND_ASCEND_REQUIREMENT = ""

VLLM_TEST_REQUIREMENT = ""
ASCEND_TEST_REQUIREMENT = """**test_impact**：测试影响评估（vllm-ascend 仓库）
   - needs_test_update：是否需要新增、删除或更新测试用例（布尔值）
   - reason：判断理由（基于对变更内容的理解，不要硬猜）
   - suggested_test_areas：建议变更的文件或模块（列表）"""

VLLM_TEST_SUMMARY_DESC = ""
ASCEND_TEST_SUMMARY_DESC = "**test_impact_summary**：一段话重点总结当日变更对测试看护的影响（是否引入需要看护的测试点、现有测试是否足以覆盖等），不要仅重复每个 commit 的 test_impact.reason，而是综合评估测试策略层面的影响"

VLLM_TEST_SUMMARY_FIELD = ""
ASCEND_TEST_SUMMARY_FIELD = '''"test_impact_summary": "<测试看护影响总结>",
'''

VLLM_TEST_COMMIT_FIELD = ""
ASCEND_TEST_COMMIT_FIELD = ''',
      "test_impact": {
        "needs_test_update": true,
        "reason": "<理由>",
        "suggested_test_areas": ["<area1>"]
      }'''

VLLM_ASCEND_SUMMARY_DESC = "（仅 vllm 项目）**ascend_impact_summary**：一段话总结当日 vllm 变更对 vllm-ascend 项目的影响（从 ascend_impact 字段中提炼）"
ASCEND_ASCEND_SUMMARY_DESC = ""

VLLM_ASCEND_SUMMARY_FIELD = '''"ascend_impact_summary": "<对 vllm-ascend 的影响总结>",
'''
ASCEND_ASCEND_SUMMARY_FIELD = ""

VLLM_ASCEND_COMMIT_FIELD = ''',
      "ascend_impact": {
        "functionality": "<功能影响>",
        "testing": "<测试影响>",
        "needs_test_update": true,
        "suggested_test_areas": ["<area>"]
      }'''
ASCEND_ASCEND_COMMIT_FIELD = ""


def load_json(filepath):
    if not os.path.exists(filepath):
        return None
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        print(f"Warning: Failed to load {filepath}: {e}")
        return None


def save_json_atomic(filepath, data):
    dirpath = os.path.dirname(filepath)
    os.makedirs(dirpath, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=dirpath, suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, filepath)
    except Exception as e:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise e


def get_repo_dir(data_dir, repo):
    return os.path.join(data_dir, repo_dir_name(repo))


def get_latest_date(data_dir, repo):
    repo_dir = get_repo_dir(data_dir, repo)
    commits_dir = os.path.join(repo_dir, "commits")
    if not os.path.isdir(commits_dir):
        return None
    files = sorted(
        [f for f in os.listdir(commits_dir) if f.endswith(".json") and f != "meta.json"],
        reverse=True,
    )
    if not files:
        return None
    return files[0].replace(".json", "")


def load_commits_data(data_dir, repo, date):
    repo_dir = get_repo_dir(data_dir, repo)
    filepath = os.path.join(repo_dir, "commits", f"{date}.json")
    data = load_json(filepath)
    if data is None:
        print(f"No commit data found for {repo} on {date}")
        return None
    return data


def load_context(data_dir, repo):
    repo_dir = get_repo_dir(data_dir, repo)
    context_path = os.path.join(repo_dir, "context", "architecture.json")
    context = load_json(context_path)
    if context is None:
        return None
    return context


def build_context_section(context):
    if context is None:
        return '（未找到架构上下文文件，请基于 commit 内容和 diff 进行分析，对不确定的内容标注"不确定"）'

    parts = []
    if context.get("overview"):
        parts.append(f"项目概述：{context['overview']}")

    if context.get("modules"):
        modules_text = "\n".join(
            f"  - {m.get('path', '')} ({m.get('name', '')}): {m.get('description', '')}"
            for m in context["modules"]
        )
        parts.append(f"核心模块：\n{modules_text}")

    if context.get("key_abstractions"):
        abs_text = "\n".join(
            f"  - {a.get('name', '')} ({a.get('location', '')}): {a.get('description', '')}"
            for a in context["key_abstractions"]
        )
        parts.append(f"关键抽象：\n{abs_text}")

    if context.get("module_dependencies"):
        parts.append(f"模块依赖：{context['module_dependencies']}")

    if context.get("hardware_abstraction"):
        ha = context["hardware_abstraction"]
        parts.append(f"硬件适配层：{ha.get('description', '')}")
        if ha.get("platform_independent"):
            parts.append(f"  平台无关接口：{', '.join(ha['platform_independent'])}")
        if ha.get("platform_specific"):
            parts.append(f"  平台特定实现：{', '.join(ha['platform_specific'])}")

    if context.get("test_structure"):
        ts = context["test_structure"]
        parts.append(f"测试结构：{ts.get('path', '')} - {ts.get('description', '')}")

    gen_time = context.get("generated_at", "unknown")
    parts.append(f"\n（上下文生成时间：{gen_time}，如需更详细信息请走读源码）")

    return "\n".join(parts)


def build_prompt(repo, date, commits_data, data_dir, local_repo=None):
    is_vllm = "vllm-ascend" not in repo

    if is_vllm:
        test_requirement = VLLM_TEST_REQUIREMENT
        test_summary_desc = VLLM_TEST_SUMMARY_DESC
        test_summary_field = VLLM_TEST_SUMMARY_FIELD
        test_commit_field = VLLM_TEST_COMMIT_FIELD
        ascend_requirement = VLLM_ASCEND_REQUIREMENT
        ascend_summary_desc = VLLM_ASCEND_SUMMARY_DESC
        ascend_summary_field = VLLM_ASCEND_SUMMARY_FIELD
        ascend_commit_field = VLLM_ASCEND_COMMIT_FIELD
    else:
        test_requirement = ASCEND_TEST_REQUIREMENT
        test_summary_desc = ASCEND_TEST_SUMMARY_DESC
        test_summary_field = ASCEND_TEST_SUMMARY_FIELD
        test_commit_field = ASCEND_TEST_COMMIT_FIELD
        ascend_requirement = ASCEND_ASCEND_REQUIREMENT
        ascend_summary_desc = ASCEND_ASCEND_SUMMARY_DESC
        ascend_summary_field = ASCEND_ASCEND_SUMMARY_FIELD
        ascend_commit_field = ASCEND_ASCEND_COMMIT_FIELD

    context = load_context(data_dir, repo)
    context_section = build_context_section(context)

    if local_repo:
        source_section = (
            f"该项目的源码位于本地路径：{local_repo}\n"
            f"如果你对某个 commit 的变更不确定，请走读该路径下的源码来理解上下文，不要硬猜。\n"
            f"可以使用文件读取工具查看 {local_repo} 下的任何文件。"
        )
    else:
        source_section = "（本地源码不可用，如对变更不确定请标注\"不确定\"）"

    commits_for_prompt = []
    for c in commits_data.get("commits", []):
        commit_info = {
            "sha": c["sha"],
            "message": c["message"],
            "author": c.get("author", {}),
            "stats": c.get("stats", {}),
            "files": [],
        }
        for f in c.get("files", []):
            commit_info["files"].append({
                "filename": f["filename"],
                "status": f["status"],
                "additions": f["additions"],
                "deletions": f["deletions"],
                "patch": f.get("patch", ""),
            })
        commits_for_prompt.append(commit_info)

    commits_json = json.dumps(commits_for_prompt, ensure_ascii=False, indent=2)

    prompt = PROMPT_TEMPLATE.format(
        repo=repo,
        date=date,
        commits_json=commits_json,
        context_section=context_section,
        source_section=source_section,
        test_requirement=test_requirement,
        test_summary_desc=test_summary_desc,
        test_summary_field=test_summary_field,
        test_commit_field=test_commit_field,
        ascend_requirement=ascend_requirement,
        ascend_summary_desc=ascend_summary_desc,
        ascend_summary_field=ascend_summary_field,
        ascend_commit_field=ascend_commit_field,
    )
    return prompt


def extract_text_from_json_events(output):
    if not output:
        return None
    texts = []
    for line in output.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
            if event.get("type") == "text":
                text = event.get("part", {}).get("text", "")
                if text:
                    texts.append(text)
        except json.JSONDecodeError:
            continue
    return "".join(texts) if texts else output


def call_opencode(prompt, workdir=None, model="deepseek/deepseek-v4-flash"):
    try:
        cmd = ["opencode", "run", "--format", "json", "--model", model]
        if workdir:
            cmd += ["--dir", workdir]
        cmd.append(prompt)
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,
        )
        if result.returncode != 0:
            print(f"opencode returned non-zero exit code: {result.returncode}")
            if result.stderr:
                print(f"stderr: {result.stderr[:500]}")
        return extract_text_from_json_events(result.stdout)
    except subprocess.TimeoutExpired:
        print("opencode call timed out (120s)")
        return None
    except FileNotFoundError:
        print("opencode CLI not found. Please install it first.")
        return None


def extract_json_from_output(output):
    if not output:
        return None

    text = output.strip()
    # Strip markdown code fences
    if text.startswith("```"):
        start = text.find("\n")
        if start != -1:
            text = text[start:].strip()
        if text.endswith("```"):
            text = text[:-3].strip()

    # Try to find the outermost JSON object, handling trailing content
    json_start = text.find("{")
    if json_start == -1:
        return None

    # Use raw_decode to find the complete JSON object
    i = json_start
    while i != -1:
        try:
            parsed, end = json.JSONDecoder().raw_decode(text, i)
            # Only accept if it looks like our expected structure
            if isinstance(parsed, dict) and "commits" in parsed:
                return parsed
            # Otherwise keep trying - might be embedded in text
            i = text.find("{", i + 1)
        except (json.JSONDecodeError, ValueError):
            i = text.find("{", i + 1)

    return None


def validate_analysis(analysis, commits_data, repo):
    errors = []

    if not isinstance(analysis, dict):
        return ["Analysis result is not a JSON object"]

    for field in ["date", "repo", "daily_summary", "commits"]:
        if field not in analysis:
            errors.append(f"Missing required field: {field}")

    if errors:
        return errors

    commit_shas = {c["sha"] for c in commits_data.get("commits", [])}
    is_vllm = "vllm-ascend" not in repo

    for ac in analysis.get("commits", []):
        if "sha" not in ac:
            errors.append("Commit analysis missing 'sha' field")
            continue
        if ac["sha"] not in commit_shas:
            errors.append(f"SHA {ac['sha'][:8]} not found in commit data")

        if is_vllm:
            if "ascend_impact" not in ac:
                errors.append(f"Commit {ac['sha'][:8]} missing ascend_impact (required for vllm repo)")
        else:
            if "test_impact" not in ac:
                errors.append(f"Commit {ac['sha'][:8]} missing test_impact (required for vllm-ascend repo)")

        if "comment" not in ac:
            errors.append(f"Commit {ac['sha'][:8]} missing field: comment")
        if "tags" not in ac:
            errors.append(f"Commit {ac['sha'][:8]} missing field: tags")

        ti = ac.get("test_impact")
        if ti:
            if "needs_test_update" not in ti:
                errors.append(f"Commit {ac['sha'][:8]} test_impact missing 'needs_test_update'")
            if "reason" not in ti:
                errors.append(f"Commit {ac['sha'][:8]} test_impact missing 'reason'")

    return errors


def display_analysis(analysis):
    is_vllm = "vllm-ascend" not in analysis.get("repo", "")
    print("\n" + "=" * 60)
    print(f"Date: {analysis.get('date', 'N/A')}")
    print(f"Repo: {analysis.get('repo', 'N/A')}")
    print(f"\n📋 当日总结\n{analysis.get('daily_summary', 'N/A')}")
    if is_vllm:
        ai_summary = analysis.get("ascend_impact_summary")
        if ai_summary:
            print(f"\n⬆ vllm-ascend 影响\n{ai_summary}")
    else:
        print(f"\n🧪 测试看护影响\n{analysis.get('test_impact_summary', 'N/A')}")
    print(f"\nCommits analyzed: {len(analysis.get('commits', []))}")
    print("-" * 60)

    for ac in analysis.get("commits", []):
        sha_short = ac.get("sha", "")[:8]
        print(f"\n  [{sha_short}] {ac.get('tags', [])}")
        comment = ac.get("comment", "")
        print(f"  {comment[:200]}{'...' if len(comment) > 200 else ''}")
        ti = ac.get("test_impact")
        if ti:
            if ti.get("needs_new_test"):
                print(f"  ⚠ 需新增测试: {ti.get('reason', '')[:120]}")
                print(f"     建议范围: {', '.join(ti.get('suggested_test_areas', []))}")
        ai = ac.get("ascend_impact")
        if ai:
            func = ai.get("functionality", "")
            if func and func != "无影响":
                print(f"  ↑ Ascend 功能影响: {func[:120]}")
                print(f"  ↑ Ascend 测试影响: {ai.get('testing', '')[:120]}")

    print("=" * 60 + "\n")


def analyze_commits(repo, date, data_dir, confirm, force, local_repo=None, model="deepseek/deepseek-v4-flash"):
    commits_data = load_commits_data(data_dir, repo, date)
    if commits_data is None:
        return False

    num_commits = len(commits_data.get("commits", []))
    if num_commits == 0:
        print(f"No commits found for {repo} on {date}")
        return False

    repo_dir = get_repo_dir(data_dir, repo)
    analysis_path = os.path.join(repo_dir, "analysis", f"{date}.json")

    if os.path.exists(analysis_path) and not force:
        if confirm:
            existing = load_json(analysis_path)
            if existing:
                print(f"Analysis already exists for {date}:")
                display_analysis(existing)
                answer = input("Overwrite? [y/N] ").strip().lower()
                if answer != "y":
                    print("Skipped.")
                    return True
        else:
            print(f"Analysis already exists for {date}, skipping (use --force to overwrite)")
            return True

    print(f"Analyzing {num_commits} commits for {repo} on {date}...")

    prompt = build_prompt(repo, date, commits_data, data_dir, local_repo=local_repo)

    print("Calling opencode CLI...")
    output = call_opencode(prompt, workdir=local_repo, model=model)
    if output is None:
        print("Failed to get response from opencode")
        return False

    analysis = extract_json_from_output(output)
    if analysis is None:
        print("Failed to parse JSON from opencode output")
        print(f"Raw output (first 500 chars): {output[:500]}")
        return False

    errors = validate_analysis(analysis, commits_data, repo)
    if errors:
        print(f"Validation errors:")
        for e in errors:
            print(f"  - {e}")
        if not confirm and not force:
            print("Analysis result invalid, not writing to file")
            return False
        if confirm:
            answer = input("Write anyway? [y/N] ").strip().lower()
            if answer != "y":
                print("Skipped.")
                return False

    analysis["date"] = date
    analysis["repo"] = repo
    analysis["generated_at"] = datetime.now(TZ_CN).isoformat()

    if confirm:
        display_analysis(analysis)
        answer = input("Write this analysis to file? [Y/n] ").strip().lower()
        if answer == "n":
            print("Skipped.")
            return True

    save_json_atomic(analysis_path, analysis)
    print(f"Analysis written to {analysis_path}")
    return True


def get_unanalyzed_dates(data_dir, repo):
    repo_dir = get_repo_dir(data_dir, repo)
    commits_dir = os.path.join(repo_dir, "commits")
    analysis_dir = os.path.join(repo_dir, "analysis")

    if not os.path.isdir(commits_dir):
        return []

    commit_files = {
        f.replace(".json", "")
        for f in os.listdir(commits_dir)
        if f.endswith(".json") and f != "meta.json"
    }

    analyzed_files = set()
    if os.path.isdir(analysis_dir):
        analyzed_files = {
            f.replace(".json", "")
            for f in os.listdir(analysis_dir)
            if f.endswith(".json")
        }

    unanalyzed = sorted(commit_files - analyzed_files, reverse=True)
    return unanalyzed


def main():
    parser = argparse.ArgumentParser(description="Analyze commits using opencode CLI")
    parser.add_argument(
        "--repo", action="append", required=True,
        help="GitHub repo (owner/repo), can specify multiple times"
    )
    parser.add_argument("--date", default=None, help="Date to analyze (YYYY-MM-DD, UTC+8)")
    parser.add_argument("--latest", action="store_true", help="Analyze the latest date with commit data")
    parser.add_argument("--catch-up", action="store_true", help="Analyze all dates that have commits but no analysis")
    parser.add_argument("--confirm", action="store_true", help="Confirm before writing results")
    parser.add_argument("--force", action="store_true", help="Force overwrite existing analysis")
    parser.add_argument("--data-dir", default="data", help="Data directory")
    parser.add_argument("--local-repo", default=None, help="Path to local repo source code (auto-detected if not specified)")
    parser.add_argument("--model", default="deepseek/deepseek-v4-flash", help="opencode model to use (default: deepseek/deepseek-v4-flash)")
    args = parser.parse_args()

    if not args.date and not args.latest and not args.catch_up:
        args.catch_up = True

    project_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    repo_local_map = {}
    for repo in args.repo:
        local = ensure_repo(repo, args.local_repo, project_dir)
        repo_local_map[repo] = local

    success = True
    for repo in args.repo:
        local_repo = repo_local_map.get(repo)
        dates_to_analyze = []

        if args.date:
            dates_to_analyze = [args.date]
        elif args.catch_up:
            dates_to_analyze = get_unanalyzed_dates(args.data_dir, repo)
            if not dates_to_analyze:
                print(f"All dates already analyzed for {repo}")
                continue
            print(f"Found {len(dates_to_analyze)} unanalyzed dates for {repo}: {dates_to_analyze[0]} ... {dates_to_analyze[-1]}")
        elif args.latest:
            latest = get_latest_date(args.data_dir, repo)
            if latest is None:
                print(f"No commit data found for {repo}")
                success = False
                continue
            dates_to_analyze = [latest]
            print(f"Latest date for {repo}: {latest}")

        for date in dates_to_analyze:
            print(f"\n--- Analyzing {repo} / {date} ---")
            result = analyze_commits(repo, date, args.data_dir, args.confirm, args.force, local_repo=local_repo, model=args.model)
            if not result:
                success = False

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
