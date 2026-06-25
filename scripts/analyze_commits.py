#!/usr/bin/env python3
import argparse
import json
import os
import sys
import tempfile
import urllib.request
import urllib.error
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
严格输出以下 JSON 格式，不要输出任何其他内容，不要使用文件写入工具，直接在回复中输出 JSON：
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

VLLM_ASCEND_REQUIREMENT = """**ascend_impact**（仅 vllm 仓库需要填写）：评估对 vllm-ascend 项目的影响。
   请基于上面架构上下文中的"接口面"、"跨项目影响判断规则"、"实现原理"来判断，不要凭感觉猜测。

   判断流程：
   1. 先看变更文件是否命中"必然影响"路径列表 → ascend_affected = true
   2. 再看变更文件是否命中"可能影响"路径列表 → 结合变更内容判断
   3. 纯平台特定代码（flashinfer/cuda/rocm 等）→ ascend_affected = false
   4. 纯 docs/tests/ci/build → ascend_affected = false

   输出字段：
   - ascend_affected：该 commit 是否影响 vllm-ascend（布尔值，影响则 true，不影响则 false）
   - functionality：功能层面的影响（具体说明影响哪个接口/类，如何影响，不要写"可能""也许"等模糊词）
   - testing：测试层面的影响
   - needs_test_update：vllm-ascend 是否因此变更需要新增、删除或更新测试用例（布尔值）
   - suggested_test_areas：如果 needs_test_update 为 true，建议变更的文件或模块（列表）
   - 如果该 commit 不影响 vllm-ascend，则 ascend_affected 填 false，functionality 和 testing 填写"无影响"，needs_test_update 填 false"""
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
        "ascend_affected": true,
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
        abs_lines = []
        for a in context["key_abstractions"]:
            line = f"  - {a.get('name', '')} ({a.get('location', '')})"
            if a.get("inherits_from"):
                line += f" extends {a['inherits_from']}"
            line += f": {a.get('description', '')}"
            if a.get("key_methods"):
                methods = "; ".join(a["key_methods"])
                line += f"\n    关键方法: {methods}"
            if a.get("ascend_implementations"):
                line += f"\n    Ascend 实现: {', '.join(a['ascend_implementations'])}"
            abs_lines.append(line)
        parts.append(f"关键抽象：\n" + "\n".join(abs_lines))

    if context.get("implementation_principles"):
        principles_lines = []
        for p in context["implementation_principles"]:
            lines = [
                f"  [{p.get('module', '')}]",
                f"    问题: {p.get('problem', '')}",
                f"    流程: {p.get('workflow', '')}",
                f"    交互: {p.get('interactions', '')}",
            ]
            if p.get("platform_differences"):
                lines.append(f"    平台差异: {p['platform_differences']}")
            principles_lines.append("\n".join(lines))
        parts.append(f"实现原理：\n" + "\n".join(principles_lines))

    if context.get("module_dependencies"):
        parts.append(f"模块依赖：{context['module_dependencies']}")

    if context.get("hardware_abstraction"):
        ha = context["hardware_abstraction"]
        parts.append(f"硬件适配层：{ha.get('description', '')}")
        if ha.get("platform_independent"):
            parts.append(f"  平台无关接口：{', '.join(ha['platform_independent'])}")
        if ha.get("platform_specific"):
            parts.append(f"  平台特定实现：{', '.join(ha['platform_specific'])}")

    if context.get("interface_surface"):
        iface = context["interface_surface"]
        if iface.get("description"):
            parts.append(f"接口面：{iface['description']}")
        if iface.get("inheritable_interfaces"):
            iface_lines = []
            for ii in iface["inheritable_interfaces"]:
                line = f"  - {ii.get('interface', '')} → Ascend: {ii.get('ascend_impl', '')}"
                if ii.get("impact_rule"):
                    line += f"\n    影响规则: {ii['impact_rule']}"
                if ii.get("key_methods"):
                    line += f"\n    关键方法: {'; '.join(ii['key_methods'])}"
                iface_lines.append(line)
            parts.append(f"可继承接口：\n" + "\n".join(iface_lines))
        if iface.get("not_used_by_ascend"):
            parts.append(f"不被 Ascend 使用的路径：{', '.join(iface['not_used_by_ascend'])}")

    if context.get("cross_project_relationship"):
        cpr = context["cross_project_relationship"]
        if cpr.get("impact_judgment_rules"):
            rules = cpr["impact_judgment_rules"]
            parts.append("跨项目影响判断规则：")
            if rules.get("definitely_affected_paths"):
                parts.append(f"  必然影响: {'; '.join(rules['definitely_affected_paths'])}")
            if rules.get("potentially_affected_paths"):
                parts.append(f"  可能影响: {'; '.join(rules['potentially_affected_paths'])}")
            if rules.get("never_affected_paths"):
                parts.append(f"  绝不影哬: {'; '.join(rules['never_affected_paths'])}")
        if cpr.get("patch_impact_map"):
            patch_lines = [f"  {k} → {v}" for k, v in cpr["patch_impact_map"].items()]
            parts.append(f"Patch 影响映射：\n" + "\n".join(patch_lines))

    if context.get("test_structure"):
        ts = context["test_structure"]
        parts.append(f"测试结构：{ts.get('path', '')} - {ts.get('description', '')}")

    gen_time = context.get("generated_at", "unknown")
    parts.append(f"\n（上下文生成时间：{gen_time}，如需更详细信息请走读源码）")

    return "\n".join(parts)


# ── Path-based triage for ascend impact ──────────────────────────────
# If ALL changed files in a commit match these patterns, it can be
# auto-determined as ascend_affected=false, skipping the LLM call.
#
# The base set is hardcoded. It can be extended at runtime by loading
# the not_used_by_ascend list from architecture.json (see triage_ascend).

AUTO_FALSE_DIRS = (
    "tests/",
    "docs/",
    ".github/",
    "benchmarks/",
    "csrc/",
    ".buildkite/",
    ".buildifier/",
    "rust/",
)

AUTO_FALSE_EXTS = (".md", ".rst", ".txt", ".cfg", ".ini", ".rs")

AUTO_FALSE_FILES = {
    "format.sh", "Dockerfile", "Makefile", "CMakeLists.txt",
    "pyproject.toml", "setup.py", "setup.cfg",
    "Cargo.toml", "Cargo.lock",
    "mkdocs.yaml", "mkdocs.yml",
    "README.md", "CONTRIBUTING.md", "CODE_OF_CONDUCT.md", "LICENSE",
    ".gitignore", ".gitattributes",
    ".pre-commit-config.yaml", ".codespellrc", ".flake8",
}

# Platform-specific files that are definitely NOT used by ascend.
# These are hardcoded as a fallback; architecture.json's not_used_by_ascend
# takes priority when available.
AUTO_FALSE_PLATFORM_SPECIFIC = (
    "vllm/platforms/cuda.py",
    "vllm/platforms/rocm.py",
    "vllm/platforms/xpu.py",
    "vllm/platforms/tpu.py",
    "vllm/platforms/cpu.py",
    "vllm/platforms/zen_cpu.py",
    "vllm/platforms/unspecified.py",
    "vllm/v1/worker/gpu_worker.py",
    "vllm/v1/worker/cpu_worker.py",
    "vllm/v1/worker/xpu_worker.py",
    "vllm/v1/attention/backends/flash_attn.py",
    "vllm/v1/attention/backends/flashinfer.py",
    "vllm/v1/attention/backends/rocm_attn.py",
    "vllm/v1/attention/backends/rocm_aiter.py",
    "vllm/v1/attention/backends/cpu_attn.py",
    "vllm/v1/attention/backends/triton_attn.py",
    "vllm/v1/attention/backends/flex_attention.py",
    "vllm/v1/attention/backends/turboquant_attn.py",
    "vllm/kernels/aiter_ops/",
    "vllm/kernels/vllm_c/",
    "vllm/kernels/xpu_ops/",
    "vllm/distributed/device_communicators/cuda_communicator.py",
    "vllm/distributed/device_communicators/cpu_communicator.py",
    "vllm/distributed/device_communicators/xpu_communicator.py",
    "vllm/distributed/device_communicators/ray_communicator.py",
)

# ── Runtime-extensible not_used_by_ascend set ────────────────────────
# Populated by load_not_used_by_ascend() from architecture.json.
_not_used_by_ascend_extra = set()


def load_not_used_by_ascend(data_dir):
    """Load the not_used_by_ascend list from vllm architecture.json.

    This extends the hardcoded AUTO_FALSE patterns with architecture-aware
    paths that the LLM identified as definitely not used by vllm-ascend.
    """
    global _not_used_by_ascend_extra
    if _not_used_by_ascend_extra:
        return
    vllm_dir = os.path.join(data_dir, "vllm")
    context_path = os.path.join(vllm_dir, "context", "architecture.json")
    context = load_json(context_path)
    if context is None:
        return
    iface = context.get("interface_surface", {})
    not_used = iface.get("not_used_by_ascend", [])
    for path in not_used:
        _not_used_by_ascend_extra.add(path)


def _is_auto_false_path(filename):
    """Check if a single file path is definitely NOT ascend-relevant."""
    if filename.startswith(AUTO_FALSE_DIRS):
        return True
    if filename.endswith(AUTO_FALSE_EXTS):
        return True
    if filename in AUTO_FALSE_FILES:
        return True
    for prefix in AUTO_FALSE_PLATFORM_SPECIFIC:
        if filename == prefix or filename.startswith(prefix.rstrip("/") + "/"):
            return True
    for prefix in _not_used_by_ascend_extra:
        if filename == prefix or filename.startswith(prefix.rstrip("/") + "/"):
            return True
    return False


def triage_ascend(commit):
    """Check if a commit definitely does NOT affect vllm-ascend.

    Returns True if every changed file is in a non-ascend-relevant path,
    meaning the commit can be auto-marked as ascend_affected=false.
    Returns False if any file needs LLM judgment.
    """
    files = commit.get("files", [])
    if not files:
        return False
    return all(_is_auto_false_path(f.get("filename", "")) for f in files)


def auto_analyze_commit(commit, repo):
    """Generate a minimal analysis for a triaged no-ascend-impact commit."""
    title = commit.get("message", "").split("\n")[0].lower()
    if any(w in title for w in ["fix", "bug", "hotfix"]):
        tags = ["bugfix", "low-risk"]
    elif any(w in title for w in ["feat", "add", "support", "implement"]):
        tags = ["feature", "low-risk"]
    elif any(w in title for w in ["refactor", "cleanup", "rename", "restruct"]):
        tags = ["refactor", "low-risk"]
    elif any(w in title for w in ["perf", "optimize", "speed"]):
        tags = ["performance", "low-risk"]
    elif any(w in title for w in ["test", "ci", "chore", "bump", "upgrade"]):
        tags = ["chore"]
    else:
        tags = ["chore"]

    is_vllm = "vllm-ascend" not in repo
    result = {
        "sha": commit["sha"],
        "comment": "（自动判定）仅涉及 tests / docs / CI / 平台特化代码变更，不影响 vllm-ascend。",
        "tags": tags,
    }
    if is_vllm:
        result["ascend_impact"] = {
            "ascend_affected": False,
            "functionality": "无影响",
            "testing": "无影响",
            "needs_test_update": False,
            "suggested_test_areas": [],
        }
    else:
        result["test_impact"] = {
            "needs_test_update": False,
            "reason": "（自动判定）该 commit 不涉及 vllm-ascend 核心逻辑变更。",
            "suggested_test_areas": [],
        }
    return result


def build_prompt(repo, date, commits_data, data_dir, local_repo=None, commit_subset=None):
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

    # When analyzing vllm commits, also load vllm-ascend architecture
    # as a reference so the AI can make precise ascend impact judgments.
    if is_vllm:
        ascend_context = load_context(data_dir, "vllm-project/vllm-ascend")
        if ascend_context:
            ascend_section = build_context_section(ascend_context)
            context_section += "\n\n## vllm-ascend 架构参考（用于评估 ascend_impact）\n" + ascend_section

    if local_repo:
        source_section = (
            f"该项目的源码位于本地路径：{local_repo}\n"
            f"如果你对某个 commit 的变更不确定，请参考上述 patch 内容进行分析，不要硬猜。"
        )
    else:
        source_section = "（本地源码不可用，如对变更不确定请标注\"不确定\"）"

    commits_src = commits_data.get("commits", [])
    if commit_subset is not None:
        commits_src = [c for c in commits_src if c["sha"] in commit_subset]
        if not commits_src:
            return ""

    # Build full commits JSON with complete patches.
    commits_for_prompt = []
    for c in commits_src:
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


DEFAULT_API_BASE = "https://api.deepseek.com/v1"


def call_llm(prompt):
    """Call the LLM API directly via environment variables.

    Required env var:
      LLM_API_KEY  — API key (e.g. DeepSeek sk-xxx)

    Optional env vars:
      LLM_API_BASE  — API base URL (default: {DEFAULT_API_BASE})
      LLM_MODEL     — model name sent to API (default: "deepseek-chat")
    """
    prompt_bytes = len(prompt.encode("utf-8"))
    print(f"  [call] prompt size: {prompt_bytes:,} bytes")

    api_key = os.environ.get("LLM_API_KEY")
    if not api_key:
        print("Error: LLM_API_KEY environment variable not set")
        return None

    api_base = os.environ.get("LLM_API_BASE", DEFAULT_API_BASE).rstrip("/")
    api_model = os.environ.get("LLM_MODEL", "deepseek-chat")

    endpoint = f"{api_base}/chat/completions"
    body = json.dumps({
        "model": api_model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,
        "max_tokens": 16384,
    }).encode("utf-8")

    req = urllib.request.Request(
        endpoint,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            result = json.loads(resp.read())
        content = result["choices"][0]["message"]["content"]
        if not content or not content.strip():
            print("LLM returned empty response")
            return None
        return content
    except urllib.error.HTTPError as e:
        print(f"API HTTP error: {e.code} {e.reason}")
        try:
            detail = e.read().decode("utf-8")
            print(f"  Response: {detail[:300]}")
        except Exception:
            pass
        return None
    except urllib.error.URLError as e:
        print(f"API connection error: {e.reason}")
        return None
    except json.JSONDecodeError as e:
        print(f"API returned invalid JSON: {e}")
        return None
    except KeyError as e:
        print(f"Unexpected API response format (missing {e})")
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

    # Strip trailing stats line (from the last "\n- " onwards)
    stats_marker = "\n— "
    stats_idx = text.rfind(stats_marker)
    if stats_idx != -1:
        text = text[:stats_idx].strip()

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
        if ti is not None and not is_vllm:
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
            if ti.get("needs_test_update"):
                print(f"  ⚠ 需新增测试: {ti.get('reason', '')[:120]}")
                print(f"     建议范围: {', '.join(ti.get('suggested_test_areas', []))}")
        ai = ac.get("ascend_impact")
        if ai:
            func = ai.get("functionality", "")
            if func and func != "无影响":
                print(f"  ↑ Ascend 功能影响: {func[:120]}")
                print(f"  ↑ Ascend 测试影响: {ai.get('testing', '')[:120]}")

    print("=" * 60 + "\n")


def analyze_commits(repo, date, data_dir, confirm, force, local_repo=None):
    commits_data = load_commits_data(data_dir, repo, date)
    if commits_data is None:
        print(f"No commit data for {repo} on {date}, skipping")
        return True

    all_commits = commits_data.get("commits", [])
    num_commits = len(all_commits)
    if num_commits == 0:
        print(f"No commits found for {repo} on {date}, skipping")
        return True

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

    # Load not_used_by_ascend from architecture.json for better triage
    is_vllm = "vllm-ascend" not in repo
    if is_vllm:
        load_not_used_by_ascend(data_dir)

    # Phase 1: triage — path-based pre-filter
    llm_shas = []
    auto_analysis = []
    for c in all_commits:
        if is_vllm and triage_ascend(c):
            auto_analysis.append(auto_analyze_commit(c, repo))
        else:
            llm_shas.append(c["sha"])

    if auto_analysis:
        print(f"  ├ {len(auto_analysis)} commits auto-determined (no ascend impact)")
    if llm_shas:
        print(f"  └ {len(llm_shas)} commits sent to LLM for analysis")

    # Phase 2: call LLM only for non-triaged commits
    llm_analysis = None  # Will hold the accumulated LLM results
    max_retries = 3
    retry_count = 0
    missing_shas = set()

    while llm_shas and retry_count < max_retries:
        llm_set = set(llm_shas)
        prompt = build_prompt(repo, date, commits_data, data_dir, local_repo=local_repo, commit_subset=llm_set)
        if not prompt:
            print("ERROR: empty prompt after subset filter")
            return False

        print(f"Calling LLM (attempt {retry_count + 1}, {len(llm_shas)} commits)...")
        output = call_llm(prompt)
        if output is None:
            print("Failed to get response from LLM")
            return False

        analysis = extract_json_from_output(output)
        if analysis is None:
            print("Failed to parse JSON from LLM output")
            print(f"Output length: {len(output)} chars")
            print(f"First 100 chars: {output[:100]!r}")
            print(f"Last 100 chars: {output[-100:]!r}")
            dump_path = os.path.join(data_dir, "llm_raw_output.txt")
            try:
                with open(dump_path, "w", encoding="utf-8") as f:
                    f.write(output)
                print(f"Full raw output saved to: {dump_path}")
            except OSError:
                print("(could not save raw output to file)")

            fallback_path = os.path.join(data_dir, "_llm_result.json")
            if os.path.exists(fallback_path):
                print(f"Trying fallback: reading {fallback_path}...")
                try:
                    with open(fallback_path, "r", encoding="utf-8") as f:
                        analysis = json.load(f)
                    os.unlink(fallback_path)
                except (json.JSONDecodeError, OSError) as e:
                    print(f"Fallback also failed: {e}")
                    analysis = None

        if analysis is None:
            return False

        # Accumulate LLM results across retries
        if llm_analysis is None:
            llm_analysis = analysis
        else:
            existing_shas = {ac["sha"] for ac in llm_analysis.get("commits", [])}
            for ac in analysis.get("commits", []):
                if ac["sha"] not in existing_shas:
                    llm_analysis["commits"].append(ac)

        # Check for missing shas
        analyzed_shas = {ac["sha"] for ac in llm_analysis.get("commits", [])}
        missing_shas = llm_set - analyzed_shas

        if not missing_shas:
            print(f"  ✓ All {len(llm_set)} commits analyzed")
            break

        print(f"  ⚠ {len(missing_shas)} commits missing from LLM response, retrying...")
        for sha in sorted(missing_shas):
            print(f"    - {sha[:8]}")

        llm_shas = list(missing_shas)
        retry_count += 1

    if missing_shas and retry_count >= max_retries:
        print(f"  ✗ Still {len(missing_shas)} commits missing after {max_retries} retries")

    analysis = llm_analysis

    # Validate the accumulated analysis
    if analysis and "commits" in analysis:
        # Only validate LLM-analyzed commits, not auto triaged ones
        all_llm_shas = {c["sha"] for c in all_commits if not is_vllm or not triage_ascend(c)}
        errors = validate_analysis(analysis, commits_data, repo)
        if errors:
            print("Validation errors:")
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

    # ── Phase 3: merge auto + LLM results ─────────────────────────
    merged_shas = {}
    for ac in auto_analysis:
        merged_shas[ac["sha"]] = ac
    if analysis and "commits" in analysis:
        for ac in analysis["commits"]:
            merged_shas[ac["sha"]] = ac

    merged_commits = []
    for c in all_commits:
        ac = merged_shas.get(c["sha"])
        if ac:
            merged_commits.append(ac)
        else:
            # Should not happen, but fallback
            merged_commits.append({
                "sha": c["sha"],
                "comment": "（分析缺失）",
                "tags": ["chore"],
            })

    # Build the final analysis object
    is_vllm = "vllm-ascend" not in repo
    if analysis is None:
        # All auto — build a minimal analysis structure
        analysis = {
            "date": date,
            "repo": repo,
            "daily_summary": f"当日 {len(auto_analysis)} 条 commit 均不涉及 vllm-ascend。",
        }
        if is_vllm:
            analysis["ascend_impact_summary"] = "当日所有变更均为 tests / docs / CI / 平台特化代码，对 vllm-ascend 无影响。"
    else:
        analysis["date"] = date
        analysis["repo"] = repo

    analysis["commits"] = merged_commits
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
    parser = argparse.ArgumentParser(description="Analyze commits using LLM")
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
            result = analyze_commits(repo, date, args.data_dir, args.confirm, args.force, local_repo=local_repo)
            if not result:
                success = False

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
