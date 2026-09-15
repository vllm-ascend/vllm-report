#!/usr/bin/env python3
"""
Clean stale data from the data directory.

Two mechanisms:

1. Orphan cleanup (always on): drop commits/<date>.json files that have no
   corresponding analysis/<date>.json (repair for a fetch-bug window; empty
   commit files are kept).

2. Retention window (--retention-days, default 14): date-keyed data lives for
   two weeks. Files whose name starts with a date (YYYY-MM-DD.json as written
   by the trackers, or date-prefixed names like YYYY-MM-DD-failure-analysis.json)
   anywhere under a repo directory (commits/, analysis/, lessons/,
   pr_ci_results/, ...) are dropped once older than the window, in every
   configured repo. The source-context cache
   (<repo>/_deep_analysis_cache/source_context/*.json) is dropped when its
   embedded cached_at leaves the window — file mtimes are unusable because CI
   checkout resets them; entries with a missing/corrupt cached_at are dropped
   too (the cache is a pure accelerator: a miss re-extracts locally, no LLM
   cost).

Protected dates are never dropped, even outside the window: baseline_date and
tracking_start_date from adaptation-status.json, the upstream_date of every
tracked commit, and the analysis/commits file dates that resolve the baseline
main/release SHAs (find_sha_date and track_adaptation init resolve them by
scanning those files; dropping one would silently degrade get_adaptation_baseline
and the next init --force rebuild). context/ is never touched — the
architecture snapshot is not date-keyed and outlives the window.
"""
import json
import os
import re
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "src"))
from data._source_repo import repo_dir_name

# Date-keyed files: exact "<date>.json" (trackers) and date-prefixed names
# like "<date>-failure-analysis.json" (per-day snapshot files that must not
# collide with a tracker's plain <date>.json).  Both sunset by the leading
# date; look-alikes like "2026-09-158.json", "<date>.json.bak" or undated
# "failure-analysis.json" stay out.
DATE_FILE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}(?:\.json$|-\S+\.json$)")


def load_json_file(path):
    try:
        with open(path, "r") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, IOError):
        return None


def retention_cutoff(retention_days, today=None):
    """Oldest date kept: files strictly older than today - retention_days are dropped."""
    today = today or date.today()
    return (today - timedelta(days=retention_days)).isoformat()


def _sha_dates(data_dir, shas):
    """Resolve each sha (12-char prefix) to its analysis/commits file date.

    Scans newest first, mirroring find_sha_date / find_baseline_date.
    """
    found = {}
    for sub in ("analysis", "commits"):
        d = os.path.join(data_dir, "vllm", sub)
        if not os.path.isdir(d):
            continue
        for fname in sorted(os.listdir(d), reverse=True):
            if not fname.endswith(".json") or fname == ".gitkeep":
                continue
            if len(found) == len(shas):
                return found
            data = load_json_file(os.path.join(d, fname))
            if not data:
                continue
            for commit in data.get("commits", []):
                sha = commit.get("sha", "")
                if sha[:12] in shas:
                    found[sha[:12]] = fname[:10]
    return found


def protected_dates(data_dir):
    """Dates the retention window must never drop."""
    dates = set()
    status = load_json_file(os.path.join(data_dir, "vllm-ascend", "adaptation-status.json"))
    if not status:
        return dates
    baseline = status.get("baseline", {})
    for key in ("baseline_date", "tracking_start_date"):
        if baseline.get(key):
            dates.add(baseline[key])
    for commit in status.get("commits", []):
        if commit.get("upstream_date"):
            dates.add(commit["upstream_date"])
    # The baseline SHAs themselves are resolved from data files by
    # find_sha_date (MCP) / find_baseline_date (init) — keep their files.
    shas = {s[:12] for s in (baseline.get("main_sha", ""), baseline.get("release_tag", "")) if s}
    if shas:
        dates.update(_sha_dates(data_dir, shas).values())
    return dates


def clean_stale_data(data_dir, repo):
    """Orphan cleanup: commits/<date>.json without a matching analysis file."""
    repo_dir = os.path.join(data_dir, repo_dir_name(repo))
    commits_dir = os.path.join(repo_dir, "commits")
    analysis_dir = os.path.join(repo_dir, "analysis")

    if not os.path.isdir(commits_dir):
        print(f"No commits directory for {repo}")
        return

    # Get all dates that have analysis files
    analyzed_dates = set()
    if os.path.isdir(analysis_dir):
        for f in os.listdir(analysis_dir):
            if f.endswith(".json") and f != ".gitkeep":
                d = f.replace(".json", "")
                analyzed_dates.add(d)

    # Check each commit file
    removed = 0
    for f in sorted(os.listdir(commits_dir)):
        if not f.endswith(".json") or f == ".gitkeep":
            continue
        d = f.replace(".json", "")
        if d not in analyzed_dates:
            path = os.path.join(commits_dir, f)
            # Keep empty commit files (no commits on that day) — they don't need analysis
            data = load_json_file(path)
            if data is not None and len(data.get("commits", [])) == 0:
                continue
            os.remove(path)
            print(f"  Removed {f} (no analysis for {d})")
            removed += 1

    return removed


def apply_retention(data_dir, cutoff, protected, repo):
    """Drop date-keyed files older than cutoff (inclusive boundary kept)."""
    repo_dir = os.path.join(data_dir, repo_dir_name(repo))
    if not os.path.isdir(repo_dir):
        return 0
    removed = 0
    for entry in sorted(os.listdir(repo_dir)):
        sub = os.path.join(repo_dir, entry)
        if not os.path.isdir(sub) or entry in ("context", "_deep_analysis_cache"):
            continue
        for f in sorted(os.listdir(sub)):
            if not DATE_FILE_RE.match(f):
                continue
            d = f[:10]
            if d >= cutoff or d in protected:
                continue
            os.remove(os.path.join(sub, f))
            print(f"  Retention: removed {repo_dir_name(repo)}/{entry}/{f} (older than {cutoff})")
            removed += 1
    return removed


def clean_source_context_cache(data_dir, cutoff):
    """Drop source-context cache entries outside the retention window.

    cached_at is the only reliable timestamp (CI checkout resets mtimes).
    Missing or unparseable cached_at means the entry predates the field —
    drop it; ensure_cached rebuilds such entries on demand.
    """
    removed = 0
    if not os.path.isdir(data_dir):
        return 0
    for repo in sorted(os.listdir(data_dir)):
        cache_dir = os.path.join(data_dir, repo, "_deep_analysis_cache", "source_context")
        if not os.path.isdir(cache_dir):
            continue
        for f in sorted(os.listdir(cache_dir)):
            if not f.endswith(".json"):
                continue
            path = os.path.join(cache_dir, f)
            entry = load_json_file(path)
            drop = True
            if isinstance(entry, dict) and entry.get("cached_at"):
                drop = str(entry["cached_at"])[:10] < cutoff
            if drop:
                os.remove(path)
                print(f"  Retention: removed {repo}/_deep_analysis_cache/source_context/{f}")
                removed += 1
    return removed


def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="Clean stale data: orphan commits without analysis, "
        "plus a retention window on date-keyed files and the source-context cache")
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--repo", nargs="*", default=["vllm-project/vllm", "vllm-project/vllm-ascend"])
    parser.add_argument("--retention-days", type=int, default=14,
                        help="Date-keyed data older than this many days is dropped (0 disables retention)")
    args = parser.parse_args()

    total = 0
    cutoff = retention_cutoff(args.retention_days) if args.retention_days > 0 else None
    if cutoff:
        protected = protected_dates(args.data_dir)
        print(f"Retention window: keeping {cutoff} onward ({len(protected)} protected dates)")
    for repo in args.repo:
        total += clean_stale_data(args.data_dir, repo) or 0
        if cutoff:
            total += apply_retention(args.data_dir, cutoff, protected, repo) or 0
    if cutoff:
        total += clean_source_context_cache(args.data_dir, cutoff) or 0
    print(f"Total: {total} files removed")


if __name__ == "__main__":
    main()
