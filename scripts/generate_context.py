#!/usr/bin/env python3
import argparse
import json
import os
import subprocess
import tempfile
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from source_repo import ensure_repo, get_current_sha, repo_dir_name

TZ_CN = timezone(timedelta(hours=8))

CONTEXT_PROMPT_TEMPLATE = """你是一个资深代码架构分析师。请对以下开源项目进行架构分析，生成一份结构化的项目知识摘要。

## 仓库信息
- 仓库：{repo}
- 分支：main

## 本地源码路径
{source_section}

## 分析要求
请通过走读项目源码（使用 opencode 的文件读取能力），分析以下内容：

1. **项目概述**：项目是什么、解决什么问题
2. **核心模块**：列出主要模块/目录及其职责（如 vllm/worker, vllm/executor 等）
3. **关键抽象**：核心类/接口及其关系（如 LLMEngine, Scheduler, Worker 等）
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
  "commit_sha": "<分析基于的 main 分支最新 commit SHA>",
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

VLLM_EXTRA_CONTEXT = """7. **与 vllm-ascend 的关系**：重点分析 vllm 中哪些模块/接口是 vllm-ascend 需要适配或扩展的，特别是：
   - Platform 层如何注册新的硬件平台
   - Attention 后端的注册和选择机制
   - 哪些 Worker/Executor 抽象是 Ascend NPU 需要实现的
   - Model 加载和推理流程中的可扩展点"""

ASCEND_EXTRA_CONTEXT = """7. **作为 vllm 的 Ascend 适配层**：分析 vllm-ascend 如何扩展/适配 vllm 的核心接口，特别是：
   - 实现了哪些 vllm 的抽象接口
   - Ascend NPU 特有的模块和实现
   - 与 vllm 主项目的代码依赖和版本耦合方式"""


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
            timeout=3600,
        )
        if result.returncode != 0:
            print(f"opencode returned non-zero exit code: {result.returncode}")
            if result.stderr:
                print(f"stderr: {result.stderr[:500]}")
        return extract_text_from_json_events(result.stdout)
    except subprocess.TimeoutExpired:
        print("opencode call timed out (300s)")
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

    # Try to find the outermost JSON object
    json_start = text.find("{")
    if json_start == -1:
        return None

    # Try parsing from each { position, longest match first
    candidates = []
    i = json_start
    while i != -1:
        try:
            parsed = json.loads(text[i:])
            candidates.append((len(text[i:]), parsed))
        except json.JSONDecodeError:
            pass
        i = text.find("{", i + 1)

    if candidates:
        candidates.sort(key=lambda x: -x[0])
        return candidates[0][1]

    return None


def generate_context(repo, data_dir, force, local_repo=None, model="deepseek/deepseek-v4-flash"):
    repo_dir = os.path.join(data_dir, repo_dir_name(repo))
    context_path = os.path.join(repo_dir, "context", "architecture.json")

    if os.path.exists(context_path) and not force:
        existing = load_json(context_path)
        if existing:
            gen_time = existing.get("generated_at", "unknown")
            print(f"Context already exists (generated at {gen_time}), use --force to regenerate")
            return True

    is_vllm = "vllm-ascend" not in repo
    extra = VLLM_EXTRA_CONTEXT if is_vllm else ASCEND_EXTRA_CONTEXT

    if local_repo:
        source_section = (
            f"项目源码位于本地路径：{local_repo}\n"
            f"请使用文件读取工具走读该路径下的源码来分析项目架构。\n"
            f"当前代码基于 commit: {get_current_sha(local_repo) or 'unknown'}"
        )
    else:
        source_section = "（本地源码不可用，请基于你对项目的知识进行分析）"

    prompt = CONTEXT_PROMPT_TEMPLATE.format(
        repo=repo,
        extra_context=extra,
        source_section=source_section,
    )

    print(f"Generating architecture context for {repo}...")
    print("This may take a few minutes as opencode will read the source code...")

    output = call_opencode(prompt, workdir=local_repo, model=model)
    if output is None:
        print("Failed to get response from opencode")
        return False

    context = extract_json_from_output(output)
    if context is None:
        # Model may have written result to a file via tool calls
        candidates = ["architecture_analysis.json"]
        if local_repo:
            candidates.insert(0, os.path.join(local_repo, "architecture_analysis.json"))
        found = None
        for c in candidates:
            if os.path.exists(c):
                try:
                    context = load_json(c)
                    os.remove(c)
                    print(f"  Found result in {c}")
                    break
                except Exception:
                    pass
        if found is None:
            print("Failed to parse JSON from opencode output")
            print(f"Raw output (first 500 chars): {output[:500]}")
            return False

    context["repo"] = repo
    context["generated_at"] = datetime.now(TZ_CN).isoformat()

    save_json_atomic(context_path, context)
    print(f"Architecture context saved to {context_path}")
    return True


def main():
    parser = argparse.ArgumentParser(description="Generate project architecture context for AI analysis")
    parser.add_argument(
        "--repo", action="append", required=True,
        help="GitHub repo (owner/repo), can specify multiple times"
    )
    parser.add_argument("--force", action="store_true", help="Force regenerate even if context exists")
    parser.add_argument("--data-dir", default="data", help="Data directory")
    parser.add_argument("--local-repo", default=None, help="Path to local repo source code (auto-detected if not specified)")
    parser.add_argument("--model", default="deepseek/deepseek-v4-flash", help="opencode model to use (default: deepseek/deepseek-v4-flash)")
    args = parser.parse_args()

    project_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    success = True
    for repo in args.repo:
        local = ensure_repo(repo, args.local_repo, project_dir)
        result = generate_context(repo, args.data_dir, args.force, local_repo=local, model=args.model)
        if not result:
            success = False

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
