#!/usr/bin/env python3
"""
Generate architecture context for a vLLM project by walking the local source
tree and reading key interface files, then asking Reasonix to synthesize
a structured JSON summary.

Execution frequency: weekly is recommended (architecture doesn't change
daily, but vLLM evolves fast enough that monthly would miss things).
"""
import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from source_repo import ensure_repo, get_current_sha, repo_dir_name

TZ_CN = timezone(timedelta(hours=8))

# ── Directory names to skip when walking the tree ───────────────────
IGNORE_DIRS = {
    ".git", "__pycache__", "node_modules", ".venv", "venv",
    "build", "dist", ".egg-info", ".mypy_cache", ".pytest_cache",
    ".hypothesis", ".tox", ".nox", ".direnv",
    ".github", ".buildkite", ".buildifier",
    "csrc",  # C++ source — not needed for Python-side architecture analysis
}

# Key interface files that define the project's abstraction boundaries.
# Reading these gives the AI the core architecture without walking every file.
# Per-repo definitions so vllm and vllm-ascend each get their key files.

REPO_SOURCE_DIRS = {
    "vllm-project/vllm": "vllm",
    "vllm-project/vllm-ascend": "vllm_ascend",
}

VLLM_KEY_FILES = [
    "vllm/platforms/__init__.py",
    "vllm/v1/attention/backend.py",
    "vllm/v1/attention/backends/registry.py",
    "vllm/v1/worker/worker_base.py",
    "vllm/v1/executor/abstract.py",
    "vllm/v1/engine/core.py",
    "vllm/config/vllm.py",
    "vllm/model_executor/models/registry.py",
    "vllm/plugins/__init__.py",
    "vllm/v1/sample/sampler.py",
]

ASCEND_KEY_FILES = [
    "vllm_ascend/platform.py",
    "vllm_ascend/worker/worker.py",
    "vllm_ascend/attention/attention_v1.py",
    "vllm_ascend/compilation/acl_graph.py",
    "vllm_ascend/distributed/device_communicators/npu_communicator.py",
    "vllm_ascend/sample/sampler.py",
    "vllm_ascend/ascend_config.py",
    "vllm_ascend/worker/model_runner_v1.py",
]

CONTEXT_PROMPT_TEMPLATE = """你是一个资深代码架构分析师。请根据以下项目源码结构目录树和关键接口文件内容，生成一份结构化的项目知识摘要。

## 仓库信息
- 仓库：{repo}
- 分支：main
- 分析的 commit：{commit_sha}

## 项目源码目录树
```
{tree}
```

## 关键接口文件内容
{key_files_content}

## 分析要求
请基于以上信息，分析以下内容：

1. **项目概述**：项目是什么、解决什么问题
2. **核心模块**：列出主要模块/目录及其职责
3. **关键抽象**：核心类/接口及其关系
4. **模块依赖关系**：模块间如何调用和依赖
5. **硬件适配层**：与硬件相关的抽象层（如 Attention 后端、Platform 层），哪些是平台无关的接口，哪些是平台特定的实现
6. **测试结构**：测试目录的组织方式和覆盖范围
{extra_context}

## 输出格式
输出 JSON 格式，不要输出其他内容：
```json
{{
  "repo": "{repo}",
  "generated_at": "<当前时间 UTC+8>",
  "commit_sha": "<commit SHA>",
  "overview": "<项目概述>",
  "modules": [
    {{
      "path": "<模块路径>",
      "name": "<模块名>",
      "description": "<职责描述>",
      "key_classes": ["<类名>"]
    }}
  ],
  "key_abstractions": [
    {{
      "name": "<抽象名>",
      "description": "<描述>",
      "location": "<所在文件>",
      "relationships": ["<关联的抽象>"]
    }}
  ],
  "module_dependencies": "<模块间依赖关系的文字描述>",
  "hardware_abstraction": {{
    "description": "<硬件适配层的整体描述>",
    "platform_independent": ["<平台无关接口>"],
    "platform_specific": ["<平台特定实现>"]
  }},
  "test_structure": {{
    "path": "<测试目录>",
    "description": "<测试组织方式>"
  }}
}}
```"""

VLLM_EXTRA_CONTEXT = """
7. **与 vllm-ascend 的关系**：特别关注 vLLM 中哪些模块/接口是专为特定硬件平台设计的，以及哪些是平台无关的抽象层。"""

ASCEND_EXTRA_CONTEXT = """
7. **作为 vLLM 的 Ascend 适配层**：分析 vllm-ascend 如何扩展 vllm 的抽象接口。"""


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
    import tempfile
    fd, tmp_path = tempfile.mkstemp(dir=dirpath, suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, filepath)
    except Exception as e:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise e


def build_tree(local_repo, source_dir, max_depth=4):
    """Walk the source directory and produce an indented tree string."""
    lines = []
    root = os.path.join(local_repo.rstrip("/"), source_dir)

    def walk(dir_path, depth):
        if depth > max_depth:
            return
        try:
            entries = sorted(os.listdir(dir_path))
        except PermissionError:
            return
        dirs = []
        files = []
        for e in entries:
            fp = os.path.join(dir_path, e)
            if os.path.isdir(fp):
                if e not in IGNORE_DIRS and not e.startswith("."):
                    dirs.append(e)
            elif e.endswith(".py"):
                files.append(e)
        indent = "  " * depth
        for d in dirs:
            lines.append(f"{indent}{d}/")
            walk(os.path.join(dir_path, d), depth + 1)
        for f in files:
            lines.append(f"{indent}{f}")

    if os.path.isdir(root):
        walk(root, 0)
    return "\n".join(lines)


def read_key_files(local_repo, key_files):
    """Read key interface files and return their content as a formatted string."""
    parts = []
    for rel_path in key_files:
        abs_path = os.path.join(local_repo, rel_path)
        if not os.path.exists(abs_path):
            continue
        try:
            with open(abs_path, "r", encoding="utf-8") as f:
                content = f.read()
            # Truncate very long files to first 200 lines
            lines = content.split("\n")
            if len(lines) > 200:
                content = "\n".join(lines[:200]) + "\n... (truncated)"
            parts.append(f"### {rel_path}\n```python\n{content}\n```")
        except (IOError, OSError):
            continue

    return "\n\n".join(parts)


def extract_json_from_output(output):
    """Parse the first valid JSON object that looks like our expected structure."""
    import json as _json
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

    # Strip Reasonix trailing stats line
    stats_marker = "\n— "
    stats_idx = text.rfind(stats_marker)
    if stats_idx != -1:
        text = text[:stats_idx].strip()

    # Find JSON object
    json_start = text.find("{")
    if json_start == -1:
        return None

    i = json_start
    while i != -1:
        try:
            parsed, end = _json.JSONDecoder().raw_decode(text, i)
            if isinstance(parsed, dict) and "overview" in parsed and "modules" in parsed:
                return parsed
            i = text.find("{", i + 1)
        except (_json.JSONDecodeError, ValueError):
            i = text.find("{", i + 1)

    return None


def call_reasonix(prompt, model="deepseek-v4-flash"):
    """Call Reasonix CLI to analyze architecture."""
    try:
        cmd = ["reasonix", "run", "--model", model, prompt]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,
        )
        if result.returncode != 0:
            print(f"reasonix returned non-zero exit code: {result.returncode}")
            if result.stderr:
                print(f"stderr: {result.stderr[:500]}")
        return result.stdout
    except subprocess.TimeoutExpired:
        print("reasonix call timed out (600s)")
        return None
    except FileNotFoundError:
        print("reasonix CLI not found. Please install it first.")
        return None


def generate_context(repo, data_dir, force, local_repo=None, model="deepseek-v4-flash"):
    repo_dir = os.path.join(data_dir, repo_dir_name(repo))
    context_path = os.path.join(repo_dir, "context", "architecture.json")

    if os.path.exists(context_path) and not force:
        existing = load_json(context_path)
        if existing:
            gen_time = existing.get("generated_at", "unknown")
            print(f"Context already exists (generated at {gen_time}), use --force to regenerate")
            return True

    if not local_repo:
        print("Error: local_repo is required for tree walking")
        return False

    source_dir = REPO_SOURCE_DIRS.get(repo, repo_dir_name(repo))
    is_vllm = "vllm-ascend" not in repo
    key_files = VLLM_KEY_FILES if is_vllm else ASCEND_KEY_FILES

    print(f"Building directory tree for {repo} (source: {source_dir})...")
    tree = build_tree(local_repo, source_dir)
    print(f"  → {len(tree.split(chr(10)))} entries")

    print(f"Reading key interface files...")
    key_files_content = read_key_files(local_repo, key_files)
    print(f"  → {len(key_files_content)} chars from {len(key_files)} files")

    extra = VLLM_EXTRA_CONTEXT if is_vllm else ASCEND_EXTRA_CONTEXT
    commit_sha = get_current_sha(local_repo) or "unknown"

    prompt = CONTEXT_PROMPT_TEMPLATE.format(
        repo=repo,
        commit_sha=commit_sha,
        tree=tree,
        key_files_content=key_files_content,
        extra_context=extra,
    )

    print("Calling Reasonix to synthesize architecture summary...")
    output = call_reasonix(prompt, model=model)
    if output is None:
        print("Failed to get response from Reasonix")
        return False

    context = extract_json_from_output(output)
    if context is None:
        print("Failed to parse JSON from Reasonix output")
        print(f"Raw output (first 500 chars): {output[:500]}")
        return False

    context["repo"] = repo
    context["generated_at"] = datetime.now(TZ_CN).isoformat()

    save_json_atomic(context_path, context)
    print(f"Architecture context saved to {context_path}")
    return True


def main():
    parser = argparse.ArgumentParser(
        description="Generate project architecture context for AI analysis"
    )
    parser.add_argument(
        "--repo", action="append", required=True,
        help="GitHub repo (owner/repo), can specify multiple times"
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Force regenerate even if context exists"
    )
    parser.add_argument(
        "--data-dir", default="data",
        help="Data directory (default: data)"
    )
    parser.add_argument(
        "--local-repo", default=None,
        help="Path to local repo source code (auto-detected)"
    )
    parser.add_argument(
        "--model", default="deepseek-v4-flash",
        help="Reasonix model to use (default: deepseek-v4-flash)"
    )
    args = parser.parse_args()

    project_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    success = True

    for repo in args.repo:
        local = ensure_repo(repo, args.local_repo, project_dir)
        if not local:
            print(f"Error: cannot locate local repo for {repo}")
            success = False
            continue
        result = generate_context(repo, args.data_dir, args.force,
                                  local_repo=local, model=args.model)
        if not result:
            success = False

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
