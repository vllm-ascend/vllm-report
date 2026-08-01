# vllm-report

Daily commit monitor and AI analysis for [vllm](https://github.com/vllm-project/vllm) and [vllm-ascend](https://github.com/vllm-project/vllm-ascend). Provides a knowledge base for AI agents (Claude Code, OpenCode) to support vllm-ascend code upgrades and main2main adaptation.

## Features

- **Daily Commit Fetching** — Automatically fetches new commits (with full diff) via GitHub Actions daily at 02:00 CST
- **Two-Phase AI Analysis** —
  - **Phase 1**: Bulk analysis via DeepSeek API (intent, risk, ascend impact, test impact) with path-based triage that auto-skips non-ascend-relevant commits (tests/docs/CI/platform-specific code), reducing LLM API costs
  - **Phase 2**: Deep analysis via Claude Code agent for ascend_affected commits — identifies specific affected interfaces, adaptation effort, and adaptation guide by reading actual source files
- **Diff-Aware Architecture Impact** — Commits modifying key interface files get `architecture_impact` markers (affected_interfaces, recommend_refresh), detected automatically from file paths against cross-project relationship rules
- **Architecture Context Cache** — Weekly auto-generated project architecture summaries via Claude Code agent, injected into AI analysis prompts for precise impact judgment
- **Knowledge Base** — `knowledge_base` field in architecture.json includes patch catalog, development workflows, and testing guide
- **Patch Catalog Extraction** — Deterministic regex-based parsing of vllm-ascend's `patch/__init__.py` via `_extract_patches.py`, producing structured JSON without LLM costs
- **Search Index** — Keyword/tag/module index for fast cross-date search
- **Adaptation Status Tracking** — Track which vllm upstream commits have been adapted to vllm-ascend, with CLI and MCP tool support
- **MCP Server** — Stdio-based MCP server with 22 tools for AI agents to query the knowledge base (query tools, adaptation tracking, architecture knowledge). Supports progressive loading — agents can start with a lightweight overview and drill into specific modules as needed
- **Email Report** — Daily markdown report sent via SMTP with categorized commit summaries
- **Static Web Dashboard** — Dark-themed monitor page with commit list, diff viewer, AI analysis overlay, and tag-based filtering
- **Data Lifecycle Management** — Stale data cleanup (`clean_stale_data.py`) removes commit data without corresponding analysis

## Project Structure

```
vllm-report/
├── .github/workflows/
│   ├── daily-commit.yml          # Daily fetch + AI analysis
│   ├── weekly-context.yml        # Weekly architecture context generation
│   ├── pages.yml                 # GitHub Pages deployment
│   └── send-report.yml           # Email report workflow
├── data/
│   ├── README.json               # Entry point for AI agents
│   ├── vllm/                     # vllm project data
│   │   ├── meta.json
│   │   ├── commits/              # Daily commit JSON files
│   │   ├── analysis/             # Daily AI analysis results
│   │   ├── context/
│   │   │   └── architecture.json
│   │   ├── index.json
│   │   ├── commits-index.json
│   └── vllm-ascend/              # vllm-ascend project data
│       ├── meta.json
│       ├── commits/
│       ├── analysis/
│       ├── context/
│       │   └── architecture.json
│       ├── index.json
│       ├── commits-index.json
│       └── adaptation-status.json
├── scripts/
│   ├── _source_repo.py           # Local repo discovery/pull/clone (internal)
│   ├── _claude_client.py         # Claude Code CLI wrapper (internal)
│   ├── _extract_patches.py       # Patch catalog extractor (deterministic)
│   ├── fetch_commits.py          # Fetch commit data
│   ├── analyze_commits.py        # Two-phase AI analysis
│   ├── generate_context.py       # Architecture context via Claude Code agent
│   ├── build_index.py            # Search index builder
│   ├── track_adaptation.py       # Adaptation status CLI
│   ├── clean_stale_data.py       # Data cleanup (commits without analysis)
│   ├── send_report.py            # Email report
│   └── serve.py                  # Dev server
├── mcp_server.py                 # MCP Server (22 tools, stdio-based)
├── site/
│   ├── index.html
│   ├── style.css
│   └── app.js
├── schemas/
│   ├── commits-schema.json
│   └── analysis-schema.json
├── docs/
│   ├── data-spec.md              # Data format specification
│   ├── usage-guide.md            # AI agent usage guide
│   └── frontend-enhancement-plan.md # Optional frontend enhancements
└── .gitignore
```

## Quick Start

### 1. Fetch Commits

```bash
# Auto-detect or clone local repos
python scripts/fetch_commits.py --repo vllm-project/vllm
python scripts/fetch_commits.py --repo vllm-project/vllm-ascend

# Specify local source code path
python scripts/fetch_commits.py --repo vllm-project/vllm --local-repo ~/code/vllm

# Use GitHub API only (no local repo)
GITHUB_TOKEN=xxx python scripts/fetch_commits.py --repo vllm-project/vllm

# Refresh a specific date (overwrite existing data)
python scripts/fetch_commits.py --repo vllm-project/vllm --refresh-date 2026-07-30
```

### 2. Set Up API Keys

```bash
# DeepSeek API for Phase 1 bulk analysis
export LLM_API_KEY="sk-your-deepseek-key"

# Anthropic API for Phase 2 deep analysis and architecture generation
export ANTHROPIC_API_KEY="sk-your-anthropic-key"
```

### 3. Generate Architecture Context

```bash
# Generate for vllm (uses Claude Code agent to read source code)
python scripts/generate_context.py --repo vllm-project/vllm --force

# Generate for vllm-ascend
python scripts/generate_context.py --repo vllm-project/vllm-ascend --force

# Cross-reference vllm vs vllm-ascend
python scripts/generate_context.py --cross-reference --force

# Both repos at once with cross-reference
python scripts/generate_context.py \
  --repo vllm-project/vllm \
  --repo vllm-project/vllm-ascend \
  --cross-reference --force
```

### 4. Run AI Analysis

```bash
# Analyze all unanalyzed dates (catch-up mode, default)
python scripts/analyze_commits.py --repo vllm-project/vllm

# Analyze a specific date
python scripts/analyze_commits.py --repo vllm-project/vllm --date 2024-01-15 --force

# Analyze only the latest date
python scripts/analyze_commits.py --repo vllm-project/vllm --latest

# Explicit catch-up mode (same as default)
python scripts/analyze_commits.py --repo vllm-project/vllm --catch-up
```

The analysis runs in two phases:
1. **Phase 1**: DeepSeek batch analysis for all commits. Includes path-based triage — commits modifying only tests/docs/CI/platform-specific code are auto-skipped (no LLM cost), using the `not_used_by_ascend` path list from architecture.json (regenerated weekly).
2. **Phase 2**: Claude Code agent deep analysis for ascend_affected commits (identifies specific interfaces, adaptation effort, and guide).

### 5. Build Search Index

```bash
python scripts/build_index.py --ascend-repo-path ~/code/vllm-ascend
```

### 6. Track Adaptation Status

```bash
# Initialize tracking (scans analysis for ascend_affected commits after baseline)
python scripts/track_adaptation.py init \
  --ascend-repo-path ~/code/vllm-ascend

# Force re-initialize with a specific start date
python scripts/track_adaptation.py init \
  --ascend-repo-path ~/code/vllm-ascend \
  --since 2026-07-15 --force

# Update a commit's status
python scripts/track_adaptation.py update --sha abc123 --status adapted

# Mark a commit as pending (confirmed not yet adapted)
python scripts/track_adaptation.py review --sha abc123 --status pending

# Show progress
python scripts/track_adaptation.py status

# List commits by status
python scripts/track_adaptation.py list --status pending
```

### 7. Start MCP Server (for AI Agents)

```bash
python mcp_server.py \
  --data-dir /path/to/vllm-report/data \
  --ascend-repo-path /path/to/vllm-ascend
```

Claude Code config (`~/.claude/settings.local.json`):
```json
{
  "mcpServers": {
    "vllm-report": {
      "command": "python",
      "args": [
        "/path/to/vllm-report/mcp_server.py",
        "--data-dir", "/path/to/vllm-report/data",
        "--ascend-repo-path", "/path/to/vllm-ascend"
      ]
    }
  }
}
```

### 8. View Dashboard

```bash
python scripts/serve.py
# or open site/index.html directly
```

## AI Agent Integration

See [docs/usage-guide.md](docs/usage-guide.md) for detailed usage scenarios:

- **Claude Code** (native MCP): Configure the MCP server, then ask questions like "What commits affect ascend today?"
- **OpenCode / Codex CLI**: Read JSON files directly from `data/` directory
- **Custom Agent**: Wrap MCP Server with `socat` for HTTP access

## GitHub Actions Setup

### Required Secrets

| Secret | Description |
|--------|-------------|
| `DEEPSEEK_API_KEY` | API key for DeepSeek (Phase 1 bulk analysis) |
| `ANTHROPIC_API_KEY` | API key for Claude Code (Phase 2 deep analysis + architecture generation) |
| `GITHUB_TOKEN` | Default token (auto-provided) |

### Optional Secrets (for Email Report)

| Secret | Description |
|--------|-------------|
| `SMTP_USER` | SMTP login username |
| `SMTP_PASS` | SMTP login password |
| `NOTIFY_EMAIL` | Recipient email address(es) |

### Workflows

- **`daily-commit.yml`** — Runs daily at 02:00 CST: fetch → Phase 1 DeepSeek analysis → Phase 2 Claude Code analysis → build index → clean stale data → send email report → deploy
- **`weekly-context.yml`** — Runs weekly on Monday 08:00 CST: checkout vllm/vllm-ascend source → generate architecture via Claude Code → cross-reference → rebuild index
- **`send-report.yml`** — Standalone workflow for on-demand email report generation
- **`pages.yml`** — Deploys GitHub Pages on push to `site/` or `data/`

## Data Format

See [docs/data-spec.md](docs/data-spec.md) for complete data format specification.

### Key Data Files

| File | Description |
|------|-------------|
| `data/README.json` | Entry point for AI agents |
| `data/{repo}/commits/{date}.json` | Raw commit data |
| `data/{repo}/analysis/{date}.json` | AI analysis results (with `architecture_based_on_sha`, `architecture_impact`, optional `deep_analysis`) |
| `data/{repo}/context/architecture.json` | Architecture knowledge (with `knowledge_base` and `cross_project_relationship`) |
| `data/{repo}/index.json` | Lightweight search index (tags/modules/keywords → SHA list) |
| `data/{repo}/commits-index.json` | SHA → {date, message} lookup table |
| `data/vllm-ascend/adaptation-status.json` | Adaptation progress tracking |

## Architecture Knowledge

The architecture knowledge is stored in `data/{repo}/context/architecture.json`, generated weekly by Claude Code agent reading actual source code. It covers **11 dimensions** across two files (~70KB for vllm, ~90KB for vllm-ascend).

### Dimensions

| Dimension | Size | Description | Who Maintains |
|-----------|------|-------------|---------------|
| `overview` | ~0.2KB | Project overview — what it is, what problem it solves | Claude Code (weekly) |
| `modules` | ~6.5KB | Core module list (name, path, key_classes, description) — 23 modules for vllm, 25 for vllm-ascend | Claude Code (weekly) |
| `key_abstractions` | ~12KB | Key classes/interfaces with inheritance (`inherits_from`), key method signatures, ascend implementations | Claude Code (weekly) |
| `implementation_principles` | ~5KB | Implementation principles — how core workflows work (scheduler loop, attention backend selection, platform plugin loading, etc.) | Claude Code (weekly) |
| `module_dependencies` | ~0.3KB | Text description of inter-module dependency graph | Claude Code (weekly) |
| `hardware_abstraction` | ~1.3KB | Hardware abstraction layer — platform-independent interfaces vs platform-specific implementations | Claude Code (weekly) |
| `interface_surface` | ~7.7KB | Interface surface — `inheritable_interfaces` (8 interfaces that ascend inherits/overrides) + `not_used_by_ascend` (63 paths that are platform-specific and can be skipped) | Claude Code (weekly) |
| `test_structure` | ~0.1KB | Test structure overview | Claude Code (weekly) |
| `cross_project_relationship` | ~13KB | Cross-project mapping — `patch_impact_map` (24 vllm patched modules → ascend patch files), `impact_judgment_rules` (definitely/potentially/never_affected_paths), `vllm_to_ascend_map` | Claude Code (weekly, cross-reference) |
| `knowledge_base` | ~3KB (vllm) / ~21KB (vllm-ascend) | Knowledge base — see below for sub-fields | Mixed |
| `repo` / `generated_at` / `commit_sha` | — | Metadata: which repo, when generated, which commit it's based on | Auto-generated |

### knowledge_base sub-fields

The `knowledge_base` field contains three sub-fields:

| Sub-field | Size | Description | Update Method |
|-----------|------|-------------|---------------|
| `patch_catalog` | ~17.6KB (ascend) | All vllm-ascend patches organized by category: `platform_patches` (21 patches), `worker_patches` (24 patches), `v2_worker_patches` (9 patches). Each patch entry includes `targets` (modified classes/functions), `why`, `how`, `related_pr`, `future_plan`. | `_extract_patches.py` — deterministic regex parsing of `patch/__init__.py`, no LLM cost |
| `development_workflows` | ~2.1KB | Fixed workflow templates for common development tasks: adding a new model, adding a config item, adding a platform backend (vllm); adding platform patch, worker patch, new model, env variable, attention backend (vllm-ascend). | Hardcoded in `generate_context.py` |
| `testing_guide` | ~0.6KB | Test commands and environment setup for both vllm and vllm-ascend: unit test commands, e2e test commands, lint commands, environment setup instructions. | Hardcoded in `generate_context.py` |

### cross_project_relationship sub-fields

| Sub-field | Description |
|-----------|-------------|
| `patch_impact_map` | Maps vllm patched modules (e.g., `vllm/v1/engine/core.py`) to corresponding vllm-ascend patch files (e.g., `patch_balance_schedule.py`). 24 entries. |
| `impact_judgment_rules` | Three categories of path matching rules: `definitely_affected_paths` (changes that always affect ascend), `never_affected_paths` (pure CUDA/ROCm/FlashInfer code), `potentially_affected_paths` (changes that need human review). |
| `vllm_to_ascend_map` | Maps vllm modules/classes to their ascend counterparts. |
| `ascend_only_components` | Components that exist only in vllm-ascend (no upstream equivalent). |

### Dynamic Maintenance

- **`interface_surface.not_used_by_ascend`** — Regenerated weekly by Claude Code during architecture generation. Reflects current codebase state: paths that are pure CUDA/ROCm/FlashInfer platform-specific code that ascend never touches.
- **`patch_catalog`** — Updated by `_extract_patches.py` every time `patch/__init__.py` changes. No LLM call needed.
- **`development_workflows` and `testing_guide`** — Hardcoded templates that rarely change.

### Progressive Loading via MCP

The MCP server provides granular tools so AI agents can load only what they need:

```
Agent's first call:    get_architecture_overview("vllm-ascend")       → ~1KB overview
Agent's second call:   get_module_info("vllm-ascend", "attention")    → ~1KB module detail
Agent's third call:    get_interface_surface("vllm")                  → ~8KB interface surface
If needed:             get_development_workflows("vllm-ascend")       → ~2KB workflows
                       get_implementation_principles("vllm")          → ~5KB principles
                       get_patch_catalog("platform")                  → patches only
```

Instead of loading the full 90KB `architecture.json` to find a single piece of information.

## MCP Server Tools

The MCP server (`mcp_server.py`) provides **22 tools** across three categories:

**Query Tools (13):**
- `get_architecture_overview` — **[Progressive]** Return overview + modules list (minimal), use as first call
- `get_module_info` — **[Progressive]** Return detailed info for a single module (fuzzy match supported)
- `get_interface_surface` — **[Progressive]** Return interface surface (inheritable interfaces + not_used_by_ascend paths)
- `get_key_abstractions` — **[Progressive]** Return key abstractions/classes with inheritance info
- `get_implementation_principles` — **[Progressive]** Return implementation principles
- `get_hardware_abstraction` — **[Progressive]** Return hardware abstraction layer info
- `get_development_workflows` — **[Progressive]** Return development workflow templates
- `get_testing_guide` — **[Progressive]** Return testing guide
- `get_architecture_context` — **[Full]** Return complete architecture.json (all of the above), use on-demand
- `get_daily_analysis` — Return analysis data for a specific date
- `search_analysis` — Cross-date search with keyword/tag/date filters
- `get_module_history` — Get change history for a specific module
- `get_commit_diff` — Get full diff for a commit (local data first, GitHub API fallback)

**Adaptation Tracking Tools:**
- `get_ascend_impact_summary` — Summary of commits that affect vllm-ascend
- `get_adaptation_baseline` — Return current vllm baseline verified by vllm-ascend
- `get_pending_adaptations` — Get list of pending/unknown adaptation commits
- `update_adaptation_status` — Update adaptation status for a commit
- `advance_baseline` — Advance the vllm baseline (update vllm-main-verified.commit)
- `get_adaptation_guide` — Get adaptation guide for a commit (markdown format)

**Architecture Knowledge Tools:**
- `get_cross_project_mapping` — Return vllm ↔ vllm-ascend cross-project mapping
- `get_patch_catalog` — Return vllm-ascend patch catalog
- `get_architecture_freshness` — Check if architecture.json is up-to-date

## License

Apache 2.0