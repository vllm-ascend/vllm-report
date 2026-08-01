#!/usr/bin/env python3
"""
Adaptation status tracking CLI for vllm-ascend main2main.

Tracks which vllm upstream commits have been adapted to vllm-ascend.

Usage:
  # Initialize: scan vllm analysis for ascend_affected commits after baseline
  python scripts/track_adaptation.py init \\
    --ascend-repo-path ~/code/vllm-ascend \\
    --data-dir data

  # Initialize with start date
  python scripts/track_adaptation.py init --since 2026-07-15 \\
    --ascend-repo-path ~/code/vllm-ascend

  # Update status
  python scripts/track_adaptation.py update --sha abc123 --status adapted

  # Review a commit (mark as pending after confirming not adapted)
  python scripts/track_adaptation.py review --sha abc123 --status pending

  # Show status summary
  python scripts/track_adaptation.py status

  # List commits by status
  python scripts/track_adaptation.py list --status pending
"""
import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timezone, timedelta

TZ_CN = timezone(timedelta(hours=8))

# Paths relative to vllm-ascend repo
BASELINE_MAIN = ".github/vllm-main-verified.commit"
BASELINE_RELEASE = ".github/vllm-release-tag.commit"


def load_json(filepath):
    if not os.path.exists(filepath):
        return None
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
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


def read_baseline(ascend_repo_path, filename):
    """Read a baseline file from vllm-ascend repo."""
    filepath = os.path.join(ascend_repo_path, filename)
    if not os.path.exists(filepath):
        print(f"Warning: {filepath} not found")
        return None
    with open(filepath, "r") as f:
        return f.read().strip()


def find_baseline_date(data_dir, sha):
    """Find the analysis date for a given SHA by scanning analysis files."""
    analysis_dir = os.path.join(data_dir, "vllm", "analysis")
    if not os.path.isdir(analysis_dir):
        return None

    for fname in sorted(os.listdir(analysis_dir), reverse=True):
        if not fname.endswith(".json"):
            continue
        date = fname.replace(".json", "")
        analysis = load_json(os.path.join(analysis_dir, fname))
        if not analysis:
            continue
        for commit in analysis.get("commits", []):
            if commit.get("sha", "")[:12] == sha[:12]:
                return date
    return None


def cmd_init(args):
    """Initialize adaptation-status.json by scanning vllm analysis data."""
    if not args.ascend_repo_path:
        print("Error: --ascend-repo-path is required for init")
        sys.exit(1)

    data_dir = os.path.abspath(args.data_dir)
    ascend_repo_path = os.path.abspath(args.ascend_repo_path)
    adaptation_path = os.path.join(data_dir, "vllm-ascend", "adaptation-status.json")

    if os.path.exists(adaptation_path) and not args.force:
        print(f"adaptation-status.json already exists at {adaptation_path}")
        print("Use --force to overwrite")
        return

    # Read baselines
    main_sha = read_baseline(ascend_repo_path, BASELINE_MAIN)
    release_tag = read_baseline(ascend_repo_path, BASELINE_RELEASE)

    if not main_sha:
        print("Error: cannot read vllm-main-verified.commit")
        sys.exit(1)

    print(f"Main baseline SHA: {main_sha[:12]}")
    print(f"Release tag: {release_tag or 'N/A'}")

    # Find baseline date
    baseline_date = find_baseline_date(data_dir, main_sha)
    if not baseline_date:
        print(f"Warning: Could not find baseline date for {main_sha[:12]}")
        print("Using --since argument or all available data")
    else:
        print(f"Baseline date: {baseline_date}")

    # Determine start date
    since_date = args.since or baseline_date
    if not since_date:
        print("Error: could not determine baseline date. Use --since to specify a start date.")
        sys.exit(1)

    print(f"Scanning vllm analysis from {since_date} onwards...")

    # Scan vllm analysis for ascend_affected commits
    analysis_dir = os.path.join(data_dir, "vllm", "analysis")
    if not os.path.isdir(analysis_dir):
        print(f"Error: analysis directory not found: {analysis_dir}")
        sys.exit(1)

    commits = []
    for fname in sorted(os.listdir(analysis_dir)):
        if not fname.endswith(".json"):
            continue
        date = fname.replace(".json", "")
        if date < since_date:
            continue

        analysis = load_json(os.path.join(analysis_dir, fname))
        if not analysis:
            continue

        for ac in analysis.get("commits", []):
            ascend_impact = ac.get("ascend_impact", {})
            if ascend_impact.get("ascend_affected"):
                commits.append({
                    "sha": ac["sha"],
                    "upstream_date": date,
                    "upstream_sha": ac["sha"],
                    "message": (ac.get("message", "") or "").split("\n")[0][:120],
                    "status": "unknown",
                    "tags": [t for t in ac.get("tags", [])],
                    "ascend_impact_summary": ascend_impact.get("functionality", ""),
                    "adaptation_notes": "",
                    "adapted_at": None,
                    "adapted_by": None,
                })

    if not commits:
        print("No ascend_affected commits found after the baseline date.")
        print("(This may be normal if there are no new upstream changes.)")
        # Create empty file anyway
        adaptation = {
            "baseline": {
                "source": f"vllm-ascend/{BASELINE_MAIN}",
                "release_tag_source": f"vllm-ascend/{BASELINE_RELEASE}",
                "tracking_start_date": since_date,
            },
            "commits": [],
            "stats": {"total": 0, "unknown": 0, "pending": 0, "in_progress": 0, "adapted": 0, "skipped": 0},
        }
        save_json_atomic(adaptation_path, adaptation)
        print(f"Created empty adaptation-status.json at {adaptation_path}")
        return

    stats = {
        "total": len(commits),
        "unknown": len(commits),
        "pending": 0,
        "in_progress": 0,
        "adapted": 0,
        "skipped": 0,
    }

    adaptation = {
        "baseline": {
            "source": f"vllm-ascend/{BASELINE_MAIN}",
            "release_tag_source": f"vllm-ascend/{BASELINE_RELEASE}",
            "tracking_start_date": since_date,
        },
        "commits": commits,
        "stats": stats,
    }

    save_json_atomic(adaptation_path, adaptation)
    print(f"Created adaptation-status.json with {len(commits)} commits (all marked as 'unknown')")
    print(f"Stats: total={stats['total']}, unknown={stats['unknown']}")
    print(f"Note: Run 'track_adaptation.py review --sha <sha> --status pending' to confirm unadapted commits.")


def cmd_update(args):
    """Update a single commit's adaptation status."""
    data_dir = os.path.abspath(args.data_dir)
    adaptation_path = os.path.join(data_dir, "vllm-ascend", "adaptation-status.json")

    adaptation = load_json(adaptation_path)
    if not adaptation:
        print("Error: adaptation-status.json not found. Run 'init' first.")
        sys.exit(1)

    valid_statuses = ["unknown", "pending", "in_progress", "adapted", "skipped"]
    if args.status not in valid_statuses:
        print(f"Error: invalid status '{args.status}'. Valid: {', '.join(valid_statuses)}")
        sys.exit(1)

    found = False
    for commit in adaptation.get("commits", []):
        if commit.get("sha") == args.sha:
            commit["status"] = args.status
            if getattr(args, 'notes', None):
                commit["adaptation_notes"] = args.notes
            if args.status == "adapted":
                commit["adapted_at"] = datetime.now(TZ_CN).isoformat()
                if getattr(args, 'adapted_by', None):
                    commit["adapted_by"] = args.adapted_by
            found = True
            print(f"Updated {args.sha[:12]}: {args.status}")
            break

    if not found:
        print(f"Error: commit {args.sha[:12]} not found in adaptation tracking")
        sys.exit(1)

    # Update stats
    stats = {"total": 0, "unknown": 0, "pending": 0, "in_progress": 0, "adapted": 0, "skipped": 0}
    for commit in adaptation.get("commits", []):
        stats["total"] += 1
        s = commit.get("status", "unknown")
        if s in stats:
            stats[s] += 1
    adaptation["stats"] = stats

    save_json_atomic(adaptation_path, adaptation)
    print(f"Stats: {json.dumps(stats)}")


def cmd_status(args):
    """Show adaptation status summary."""
    data_dir = os.path.abspath(args.data_dir)
    adaptation_path = os.path.join(data_dir, "vllm-ascend", "adaptation-status.json")

    adaptation = load_json(adaptation_path)
    if not adaptation:
        print("Error: adaptation-status.json not found. Run 'init' first.")
        sys.exit(1)

    stats = adaptation.get("stats", {})
    print(f"\n{'=' * 50}")
    print(f"vllm-ascend 适配进度")
    print(f"{'=' * 50}")
    print(f"  总计:    {stats.get('total', 0)}")
    print(f"  ✅ 已适配: {stats.get('adapted', 0)}")
    print(f"  🔄 适配中: {stats.get('in_progress', 0)}")
    print(f"  ⏳ 待适配: {stats.get('pending', 0)}")
    print(f"  ❓ 待确认: {stats.get('unknown', 0)}")
    print(f"  ⏭️ 已跳过: {stats.get('skipped', 0)}")
    print(f"{'=' * 50}")

    baseline = adaptation.get("baseline", {})
    print(f"基线: {baseline.get('tracking_start_date', 'N/A')} 起")
    print(f"源文件: {baseline.get('source', 'N/A')}")


def cmd_list(args):
    """List commits by status."""
    data_dir = os.path.abspath(args.data_dir)
    adaptation_path = os.path.join(data_dir, "vllm-ascend", "adaptation-status.json")

    adaptation = load_json(adaptation_path)
    if not adaptation:
        print("Error: adaptation-status.json not found. Run 'init' first.")
        sys.exit(1)

    commits = adaptation.get("commits", [])
    if args.status:
        commits = [c for c in commits if c.get("status") == args.status]

    if not commits:
        print(f"No commits with status '{args.status or 'any'}'")
        return

    print(f"\nFound {len(commits)} commits:")
    print("-" * 80)
    for c in commits:
        sha_short = c["sha"][:12]
        date = c.get("upstream_date", "????-??-??")
        message = (c.get("message", "") or "")[:60]
        status_icon = {
            "unknown": "❓",
            "pending": "⏳",
            "in_progress": "🔄",
            "adapted": "✅",
            "skipped": "⏭️",
        }.get(c.get("status", ""), "?")
        print(f"  {status_icon} [{sha_short}] {date} {message}")
        if c.get("ascend_impact_summary"):
            print(f"    影响: {c['ascend_impact_summary'][:80]}")
    print("-" * 80)


def main():
    parser = argparse.ArgumentParser(
        description="Track vllm-ascend adaptation status for main2main"
    )
    # Global options (available to all subcommands)
    global_opts = argparse.ArgumentParser(add_help=False)
    global_opts.add_argument("--data-dir", default="data", help="Data directory")
    global_opts.add_argument("--ascend-repo-path", default=None, help="Path to vllm-ascend repository")

    subparsers = parser.add_subparsers(dest="command", help="Sub-command")

    # init
    init_parser = subparsers.add_parser("init", parents=[global_opts], help="Initialize adaptation tracking")
    init_parser.add_argument("--since", default=None, help="Start tracking from this date (YYYY-MM-DD)")
    init_parser.add_argument("--force", action="store_true", help="Overwrite existing file")

    # update
    update_parser = subparsers.add_parser("update", parents=[global_opts], help="Update a commit's adaptation status")
    update_parser.add_argument("--sha", required=True, help="Commit SHA")
    update_parser.add_argument("--status", required=True, choices=["unknown", "pending", "in_progress", "adapted", "skipped"], help="New status")
    update_parser.add_argument("--notes", default=None, help="Adaptation notes")
    update_parser.add_argument("--adapted-by", default=None, help="Who adapted this commit")

    # review (alias for update --status pending)
    review_parser = subparsers.add_parser("review", parents=[global_opts], help="Mark a commit as pending (confirmed not adapted)")
    review_parser.add_argument("--sha", required=True, help="Commit SHA")
    review_parser.add_argument("--status", default="pending", choices=["pending", "unknown"], help="Status (default: pending)")

    # status
    status_parser = subparsers.add_parser("status", parents=[global_opts], help="Show adaptation status summary")

    # list
    list_parser = subparsers.add_parser("list", parents=[global_opts], help="List commits by status")
    list_parser.add_argument("--status", default=None, choices=["unknown", "pending", "in_progress", "adapted", "skipped"], help="Filter by status")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    # Dispatch commands
    if args.command == "init":
        cmd_init(args)
    elif args.command == "update":
        cmd_update(args)
    elif args.command == "review":
        cmd_update(args)  # Same logic as update
    elif args.command == "status":
        cmd_status(args)
    elif args.command == "list":
        cmd_list(args)


if __name__ == "__main__":
    main()