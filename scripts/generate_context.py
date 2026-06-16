#!/usr/bin/env python3
"""
Generate architecture context for a vLLM project by walking the local source
tree and reading key interface files, then using an LLM to synthesize
a structured JSON summary.

Execution frequency: weekly is recommended (architecture doesn't change
daily, but vLLM evolves fast enough that monthly would miss things).

Two-phase generation:
  Phase 1: Generate architecture.json for vllm and vllm-ascend independently.
  Phase 2: Load both and cross-reference to fill cross_project_relationship.
"""
import argparse
import json
import os
import sys
import urllib.request
import urllib.error
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
    "csrc",
}

# Key interface files that define the project's abstraction boundaries.
# Reading these gives the AI the core architecture without walking every file.

REPO_SOURCE_DIRS = {
    "vllm-project/vllm": "vllm",
    "vllm-project/vllm-ascend": "vllm_ascend",
}

VLLM_KEY_FILES = [
    # Platform & plugin
    "vllm/platforms/__init__.py",
    "vllm/platforms/interface.py",
    "vllm/plugins/__init__.py",
    # Engine core
    "vllm/v1/engine/core.py",
    "vllm/v1/engine/llm_engine.py",
    "vllm/v1/engine/core_client.py",
    # Executor & worker (GPUModelRunner is parent of NPUModelRunner)
    "vllm/v1/executor/abstract.py",
    "vllm/v1/worker/worker_base.py",
    "vllm/v1/worker/gpu_model_runner.py",
    # Attention
    "vllm/v1/attention/backend.py",
    "vllm/v1/attention/backends/registry.py",
    # Scheduler & KV cache
    "vllm/v1/core/scheduler.py",
    "vllm/v1/kv_cache_interface.py",
    # Config
    "vllm/config/vllm.py",
    # Model
    "vllm/model_executor/models/registry.py",
    "vllm/model_executor/models/interfaces_base.py",
    # Compilation
    "vllm/compilation/compiler_interface.py",
    # Sampling
    "vllm/v1/sample/sampler.py",
    # Distributed
    "vllm/distributed/device_communicators/base_device_communicator.py",
    # Entrypoints (OpenAI/Anthropic API surface)
    "vllm/entrypoints/openai/serving_chat.py",
]

ASCEND_KEY_FILES = [
    # Platform
    "vllm_ascend/platform.py",
    # Worker & model runner
    "vllm_ascend/worker/worker.py",
    "vllm_ascend/worker/model_runner_v1.py",
    # Attention
    "vllm_ascend/attention/attention_v1.py",
    # Compilation
    "vllm_ascend/compilation/acl_graph.py",
    "vllm_ascend/compilation/compiler_interface.py",
    # Distributed
    "vllm_ascend/distributed/device_communicators/npu_communicator.py",
    "vllm_ascend/distributed/parallel_state.py",
    # Sampling
    "vllm_ascend/sample/sampler.py",
    # Config
    "vllm_ascend/ascend_config.py",
    # Patch
    "vllm_ascend/patch/__init__.py",
    # Quantization
    "vllm_ascend/quantization/__init__.py",
    # Ops (entry point for custom ops / triton kernels)
    "vllm_ascend/ops/__init__.py",
    # Models
    "vllm_ascend/models/__init__.py",
    # Device / memory
    "vllm_ascend/device_allocator/camem.py",
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
2. **核心模块**：列出主要模块/目录及其职责，对于有技术深度的模块（如 Attention、Worker、Compilation、Distributed），请在描述中包含 **实现原理**（这个模块怎么工作、为什么这样设计）
3. **关键抽象**：核心类/接口，要求包含：
   - inherits_from：该类/接口继承自哪个基类（如果是扩展 vllm 的抽象，标注出来）
   - key_methods：列出关键方法及其签名，简要说明作用
   - ascend_implementations：如果 vllm-ascend 实现了此接口，列出对应的 ascend 类名（vllm 仓库时填写）
4. **实现原理**：针对核心模块/技术，描述其实现原理和技术细节，包括：
   - 它解决了什么问题
   - 核心工作流程（用文字描述即可，不要写代码）
   - 与其他模块的交互方式
   - 不同硬件平台的差异处理方式
   - 示例主题（根据仓库选择）：
     * vllm 仓库：Platform 插件加载机制、AttentionBackend 选择与缓存机制、EngineCore 调度循环、KV Cache 管理、GPUModelRunner 前向传播流程、torch.compile 集成方式、OOT 平台注册机制
     * vllm-ascend 仓库：NPUPlatform 注册与加载流程、NPUModelRunner 与 GPUModelRunner 的差异、ACL Graph 与 CUDA Graph 的差异、AscendAttentionBackend 的 NZ 格式处理、patch 机制（platform 级/worker 级）、EPLB 负载均衡原理
5. **模块依赖关系**：模块间如何调用和依赖
6. **硬件适配层**：与硬件相关的抽象层，哪些是平台无关的接口，哪些是平台特定的实现
7. **接口面**（interface_surface）——非常重要：列出所有被外部平台插件（如 vllm-ascend）继承/复写的核心接口：
   - 对每个接口，说明：基类位置、ascend 实现类名、关键方法签名、影响规则（签名/行为变更的后果）
   - 同时列出 **不被 vllm-ascend 使用** 的模块/路径（如 flashinfer、cuda.py、rocm.py 等纯平台特定代码）
{extra_context}

## 输出格式
输出 JSON 格式，不要输出其他内容，不要使用文件写入工具，直接在回复中输出 JSON：
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
      "description": "<职责描述（含实现原理，如适用）>",
      "key_classes": ["<类名>"]
    }}
  ],
  "key_abstractions": [
    {{
      "name": "<抽象名>",
      "description": "<描述>",
      "location": "<所在文件>",
      "inherits_from": "<继承的基类，没有则填 null>",
      "key_methods": ["<方法签名>: <作用简述>"],
      "ascend_implementations": ["<ascend 实现类名，仅 vllm 仓库填写>"],
      "relationships": ["<关联的抽象>"]
    }}
  ],
  "implementation_principles": [
    {{
      "module": "<模块名>",
      "problem": "<该模块解决的问题>",
      "workflow": "<核心工作流程的文字描述，不用写代码>",
      "interactions": "<与其他模块的交互方式>",
      "platform_differences": "<不同硬件平台的差异处理方式（如适用）>"
    }}
  ],
  "module_dependencies": "<模块间依赖关系的文字描述>",
  "hardware_abstraction": {{
    "description": "<硬件适配层的整体描述>",
    "platform_independent": ["<平台无关接口>"],
    "platform_specific": ["<平台特定实现>"]
  }},
  "interface_surface": {{
    "description": "<哪些接口被外部平台插件继承/复写>",
    "inheritable_interfaces": [
      {{
        "interface": "<vLLM 基类全限定名>",
        "location": "<文件路径>",
        "ascend_impl": "<vllm-ascend 中的实现类名>",
        "key_methods": ["<方法签名>: <作用>"],
        "impact_rule": "<修改该接口的签名或行为后对 vllm-ascend 的影响>"
      }}
    ],
    "not_used_by_ascend": ["<vllm 中不被 vllm-ascend 使用的路径/模块>"]
  }},
  "test_structure": {{
    "path": "<测试目录>",
    "description": "<测试组织方式>"
  }}
}}
```"""

VLLM_EXTRA_CONTEXT = """
8. **与 vllm-ascend 的关系**：
   - 特别关注哪些模块/接口是 vllm-ascend 必须继承或复写的
   - interface_surface 字段需要非常详尽，这是后续 commit 分析判断 ascend_impact 的核心依据
   - not_used_by_ascend 需要包含所有绝对不影响 vllm-ascend 的路径（如纯 CUDA kernel、纯 ROCm 代码、纯 FlashInfer 后端等）
   - 实现原理示例主题：
     * EngineCore 调度循环：如何从 Scheduler 取 batch → Executor 分发到 Worker → 收集结果 → 输出处理
     * GPUModelRunner 前向传播：execute_model() 的完整流程，哪些步骤是可以用子类 override 的
     * Platform 插件加载机制：__init__.py 中的 auto-detect 流程，OOT 平台如何通过 entry_points 注入
     * AttentionBackend 注册与选择：get_attn_backend_cls() 的缓存和 fallback 逻辑
     * torch.compile 集成：CompilerInterface → InductorAdaptor → CUDAGraph 的编译流水线
     * KV Cache 管理：block_pool → scheduler → attention backend 的数据流"""

ASCEND_EXTRA_CONTEXT = """
8. **作为 vLLM 的 Ascend 适配层**：
   - 分析 vllm-ascend 如何扩展 vllm 的每个抽象接口
   - interface_surface 中的 inheritable_interfaces 需要说明基类来自 vLLM 的哪个文件
   - 实现原理示例主题：
     * NPUPlatform 注册流程：从 vllm_ascend/__init__.py register() → vLLM 插件系统 → NPUPlatform 实例化
     * NPUModelRunner 与 GPUModelRunner 的差异：哪些方法被 override、哪些是新增的
     * ACL Graph 机制：与 CUDA Graph 的差异（API 不同、NZ 格式、capture 流程差异）
     * AscendAttentionBackend 的 NZ 格式处理：KV cache shape 差异、get_kv_cache_shape 返回格式
     * Patch 机制：adapt_patch() 的执行时机、platform 级 vs worker 级的区别
     * EPLB 负载均衡：expert 路由权重分配的工作流程
     * CaMem 分配器：与 PyTorch 默认分配器的差异"""

CROSS_REFERENCE_PROMPT = """你是一个资深代码架构分析师。以下是将两个项目的架构摘要合并，请你分析两者之间的继承/复写/依赖关系。

## vllm 架构摘要
```json
{vllm_context_json}
```

## vllm-ascend 架构摘要
```json
{ascend_context_json}
```

## 分析要求
请基于以上两份架构摘要，输出跨项目关系分析。重点关注：

1. **类/接口映射**：vLLM 中的每个 interface_surface.inheritable_interfaces 在 vllm-ascend 中对应的实现类
2. **Ascend 独有组件**：vllm-ascend 中哪些组件没有对应的 vLLM 基类（如 ACLGraphWrapper、CaMemAllocator 等）
3. **影响判断规则**：基于接口面分析，给出一套具体的 ascend_impact 判断规则：
   - 哪些 vLLM 文件/路径的变更 **必然** 影响 vllm-ascend（如 platform/__init__.py、worker_base.py 签名变更）
   - 哪些 vLLM 文件/路径的变更 **可能** 影响 vllm-ascend（如 engine/core.py、config/ 的行为变更）
   - 哪些 vLLM 文件/路径的变更 **绝不** 影响 vllm-ascend（如 flashinfer、cuda.py、rocm.py）
4. **Patch 影响面**：vllm-ascend 通过 patch 机制修改了 vLLM 的哪些模块，这些模块的变更如何影响 ascend

## 输出格式
输出 JSON 格式，不要输出其他内容：
```json
{{
  "vllm_to_ascend_map": {{
    "<vLLM 类全限定名或文件路径>": "<对应 vllm-ascend 类名或文件路径，无实现则标注 null>"
  }},
  "ascend_only_components": [
    "<没有 vLLM 基类的 vllm-ascend 组件>"
  ],
  "impact_judgment_rules": {{
    "definitely_affected_paths": [
      "<vLLM 文件/路径模式">,
      "<说明：为什么必然影响>"
    ],
    "potentially_affected_paths": [
      "<vLLM 文件/路径模式">,
      "<说明：什么条件下会影响>"
    ],
    "never_affected_paths": [
      "<vLLM 文件/路径模式">,
      "<说明：为什么不影响>"
    ]
  }},
  "patch_impact_map": {{
    "<vLLM 被 patch 的模块路径>": "<对应的 vllm-ascend patch 文件>"
  }}
}}
```"""


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
    parts = []
    for rel_path in key_files:
        abs_path = os.path.join(local_repo, rel_path)
        if not os.path.exists(abs_path):
            continue
        try:
            with open(abs_path, "r", encoding="utf-8") as f:
                content = f.read()
            lines = content.split("\n")
            if len(lines) > 200:
                content = "\n".join(lines[:200]) + "\n... (truncated)"
            parts.append(f"### {rel_path}\n```python\n{content}\n```")
        except (IOError, OSError):
            continue

    return "\n\n".join(parts)


def extract_json_from_output(output, required_key="overview"):
    import json as _json
    if not output:
        return None

    text = output.strip()
    if text.startswith("```"):
        start = text.find("\n")
        if start != -1:
            text = text[start:].strip()
        if text.endswith("```"):
            text = text[:-3].strip()

    stats_marker = "\n— "
    stats_idx = text.rfind(stats_marker)
    if stats_idx != -1:
        text = text[:stats_idx].strip()

    json_start = text.find("{")
    if json_start == -1:
        return None

    i = json_start
    while i != -1:
        try:
            parsed, end = _json.JSONDecoder().raw_decode(text, i)
            if isinstance(parsed, dict) and any(k in parsed for k in (required_key, "modules", "commits")):
                return parsed
            i = text.find("{", i + 1)
        except (_json.JSONDecodeError, ValueError):
            i = text.find("{", i + 1)

    return None


DEFAULT_API_BASE = "https://api.deepseek.com/v1"


def call_llm(prompt, max_tokens=16384):
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
        "max_tokens": max_tokens,
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


def generate_context(repo, data_dir, force, local_repo=None):
    """Phase 1: Generate architecture.json for a single repo."""
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
    print(f"  -> {len(tree.split(chr(10)))} entries")

    print(f"Reading key interface files...")
    key_files_content = read_key_files(local_repo, key_files)
    print(f"  -> {len(key_files_content)} chars from {len(key_files)} files")

    extra = VLLM_EXTRA_CONTEXT if is_vllm else ASCEND_EXTRA_CONTEXT
    commit_sha = get_current_sha(local_repo) or "unknown"

    prompt = CONTEXT_PROMPT_TEMPLATE.format(
        repo=repo,
        commit_sha=commit_sha,
        tree=tree,
        key_files_content=key_files_content,
        extra_context=extra,
    )

    print("Calling LLM to synthesize architecture summary...")
    output = call_llm(prompt)
    if output is None:
        print("Failed to get response from LLM")
        return False

    context = extract_json_from_output(output)
    if context is None:
        print("Failed to parse JSON from LLM output")
        print(f"Output length: {len(output)} chars")
        print(f"First 100 chars: {output[:100]!r}")
        print(f"Last 100 chars: {output[-100:]!r}")

        fallback_path = os.path.join(repo_dir, "_llm_result.json")
        if os.path.exists(fallback_path):
            print(f"Trying fallback: reading {fallback_path}...")
            try:
                with open(fallback_path, "r", encoding="utf-8") as f:
                    context = json.load(f)
                os.unlink(fallback_path)
            except (json.JSONDecodeError, OSError) as e:
                print(f"Fallback also failed: {e}")
                context = None

    if context is None:
        return False

    context["repo"] = repo
    context["generated_at"] = datetime.now(TZ_CN).isoformat()

    save_json_atomic(context_path, context)
    print(f"Architecture context saved to {context_path}")
    return True


def generate_cross_reference(data_dir, force, vllm_local=None, ascend_local=None):
    """Phase 2: Cross-reference vllm and vllm-ascend architectures.

    Reads both architecture.json files, sends them to the LLM to produce
    cross_project_relationship, and writes the result back into both files.
    """
    vllm_dir = os.path.join(data_dir, repo_dir_name("vllm-project/vllm"))
    ascend_dir = os.path.join(data_dir, repo_dir_name("vllm-project/vllm-ascend"))
    vllm_path = os.path.join(vllm_dir, "context", "architecture.json")
    ascend_path = os.path.join(ascend_dir, "context", "architecture.json")

    vllm_ctx = load_json(vllm_path)
    ascend_ctx = load_json(ascend_path)

    if not vllm_ctx:
        print("Error: vllm architecture.json not found. Run phase 1 first.")
        return False
    if not ascend_ctx:
        print("Error: vllm-ascend architecture.json not found. Run phase 1 first.")
        return False

    # Check if cross reference already exists
    if (vllm_ctx.get("cross_project_relationship") and
            ascend_ctx.get("cross_project_relationship") and
            not force):
        print("Cross reference already exists, use --force to regenerate")
        return True

    print("Phase 2: Generating cross-project relationship...")

    vllm_json = json.dumps(vllm_ctx, ensure_ascii=False, indent=2)
    ascend_json = json.dumps(ascend_ctx, ensure_ascii=False, indent=2)

    prompt = CROSS_REFERENCE_PROMPT.format(
        vllm_context_json=vllm_json,
        ascend_context_json=ascend_json,
    )

    output = call_llm(prompt, max_tokens=16384)
    if output is None:
        print("Failed to get cross-reference from LLM")
        return False

    cross_ref = extract_json_from_output(output, required_key="vllm_to_ascend_map")
    if cross_ref is None:
        print("Failed to parse cross-reference JSON from LLM output")
        print(f"Output length: {len(output)} chars")
        print(f"First 200 chars: {output[:200]!r}")
        return False

    # Write cross_project_relationship into both architecture files
    for ctx, path in [(vllm_ctx, vllm_path), (ascend_ctx, ascend_path)]:
        ctx["cross_project_relationship"] = cross_ref
        ctx["generated_at"] = datetime.now(TZ_CN).isoformat()
        save_json_atomic(path, ctx)
        print(f"Updated cross_project_relationship in {path}")

    return True


def main():
    parser = argparse.ArgumentParser(
        description="Generate project architecture context for AI analysis"
    )
    parser.add_argument(
        "--repo", action="append", default=[],
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
        "--cross-reference", action="store_true",
        help="Run phase 2: generate cross_project_relationship from existing architecture.json files"
    )
    args = parser.parse_args()

    project_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    if args.cross_reference:
        vllm_local = None
        ascend_local = None
        for repo in args.repo:
            local = ensure_repo(repo, args.local_repo, project_dir)
            if "vllm-ascend" in repo:
                ascend_local = local
            else:
                vllm_local = local
        result = generate_cross_reference(args.data_dir, args.force,
                                          vllm_local=vllm_local,
                                          ascend_local=ascend_local)
        sys.exit(0 if result else 1)

    if not args.repo:
        print("Error: at least one --repo is required (or use --cross-reference)")
        sys.exit(1)

    success = True
    for repo in args.repo:
        print(f"\n{'='*60}")
        print(f"Processing: {repo}")
        print(f"{'='*60}")
        local = ensure_repo(repo, args.local_repo, project_dir)
        if not local:
            print(f"Error: cannot locate local repo for {repo}")
            success = False
            continue
        result = generate_context(repo, args.data_dir, args.force,
                                  local_repo=local)
        if not result:
            print(f"FAILED: {repo}")
            success = False
        else:
            print(f"DONE: {repo}")

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
