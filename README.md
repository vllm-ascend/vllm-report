# vllm-report

Daily commit monitor and AI analysis for [vllm](https://github.com/vllm-project/vllm) and [vllm-ascend](https://github.com/vllm-project/vllm-ascend).

## Features

- **Daily Commit Fetching** — Automatically fetches new commits (with full diff) via GitHub Actions at 08:00 CST every day
- **AI Analysis** — Analyzes each commit for intent, risk, test impact, and cross-project (vllm → vllm-ascend) impact via LLM API (DeepSeek)
- **Architecture Context Cache** — Weekly auto-generated project architecture summaries injected into AI analysis prompts, improving accuracy and reducing token waste
- **Static Web Dashboard** — Dark-themed monitor page with commit list, diff viewer, AI analysis overlay, and tag-based filtering
- **Local Source Code Support** — Can read local vllm/vllm-ascend repos to fetch commits via `git log` instead of GitHub API

## Project Structure

```
vllm-report/
├── .github/workflows/
│   ├── daily-commit.yml       # Daily fetch + AI analysis
│   └── pages.yml              # GitHub Pages deployment
├── data/
│   ├── vllm/
│   │   ├── meta.json          # Anchor: latest SHA + last fetch time
│   │   ├── commits/           # Daily commit JSON files
│   │   ├── analysis/          # Daily AI analysis results
│   │   └── context/           # Architecture context cache
│   └── vllm-ascend/
│       └── (same structure)
├── scripts/
│   ├── source_repo.py         # Local repo discovery/pull/clone
│   ├── fetch_commits.py       # Fetch commit data
│   ├── analyze_commits.py     # AI analysis via LLM API
│   └── generate_context.py    # Generate architecture context via LLM API
├── site/
│   ├── index.html
│   ├── style.css
│   └── app.js
└── schemas/
    ├── commits-schema.json
    └── analysis-schema.json
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
```

Local repo discovery order:
1. `--local-repo` argument
2. Common paths: `~/code/vllm`, `~/projects/vllm`, etc.
3. Auto-clone to `repos/` directory

### 2. Set Up LLM API Key

```bash
export LLM_API_KEY="sk-你的DeepSeekAPIKey"
# Optional overrides:
# export LLM_API_BASE="https://api.deepseek.com/v1"
# export LLM_MODEL="deepseek-chat"
```

### 3. Generate Architecture Context

```bash
# Generate for the first time
python scripts/generate_context.py --repo vllm-project/vllm

# Force regenerate
python scripts/generate_context.py --repo vllm-project/vllm --force

# Both repos at once
python scripts/generate_context.py --repo vllm-project/vllm --repo vllm-project/vllm-ascend --force
```

This reads the project source code and produces `data/{repo}/context/architecture.json`, which is used by the analysis script to avoid blind guessing.

### 4. Run AI Analysis

```bash
# Analyze all unanalyzed dates (default)
python scripts/analyze_commits.py --repo vllm-project/vllm

# Analyze a specific date
python scripts/analyze_commits.py --repo vllm-project/vllm --date 2024-01-15

# Interactive confirmation before writing
python scripts/analyze_commits.py --repo vllm-project/vllm --confirm

# Force overwrite existing analysis
python scripts/analyze_commits.py --repo vllm-project/vllm --force
```

### 5. View Dashboard

Open `site/index.html` locally, or deploy via GitHub Pages (auto-deployed when `site/` or `data/` changes).

## GitHub Actions Setup

### Required Secrets

| Secret | Description |
|--------|-------------|
| `DEEPSEEK_API_KEY` | API key for DeepSeek (or your LLM provider) |
| `GITHUB_TOKEN` | Default token (auto-provided) |

Analysis runs unconditionally when commits exist — no feature flag needed.

### GitHub Pages

1. Go to repo Settings → Pages
2. Set Source to "GitHub Actions"
3. The `pages.yml` workflow will auto-deploy on pushes to `site/` or `data/`

## Data Format

### Commits (data/{repo}/commits/YYYY-MM-DD.json)

Each commit includes: SHA, author, date, message, parents, stats (additions/deletions/files), and full diff per file.

### Analysis (data/{repo}/analysis/YYYY-MM-DD.json)

Each commit analysis includes:
- `comment` — AI analysis of the change
- `tags` — Classification (feature/bugfix/refactor/performance, high-risk/medium-risk/low-risk, module name)
- `test_impact` — Whether new tests are needed, reason, suggested test areas
- `ascend_impact` — (vllm repo only) Impact on vllm-ascend (functionality + testing)

### Context (data/{repo}/context/architecture.json)

Project architecture summary: modules, key abstractions, hardware abstraction layer, test structure, vllm↔vllm-ascend interface points.

## External AI Systems

External systems can write analysis results to this repo via GitHub Contents API:

```
PUT /repos/{owner}/vllm-report/contents/data/{repo}/analysis/YYYY-MM-DD.json
```

The JSON must conform to `schemas/analysis-schema.json`. Commit SHA is the matching key between commits and analysis.
