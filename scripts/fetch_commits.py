#!/usr/bin/env python3
import re
import argparse
import json
import os
import sys
import time
import tempfile
import subprocess
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from source_repo import ensure_repo, repo_dir_name

import requests

TZ_CN = timezone(timedelta(hours=8))
GITHUB_API = "https://api.github.com"
MAX_PAGES = 10
PER_PAGE = 100
RETRY_COUNT = 3
RETRY_DELAYS = [5, 10, 20]
REQUEST_TIMEOUT = 30


def github_request(url, token, params=None):
    headers = {
        "Accept": "application/vnd.github.v3+json",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    for attempt in range(RETRY_COUNT):
        try:
            resp = requests.get(
                url, headers=headers, params=params, timeout=REQUEST_TIMEOUT
            )

            remaining_str = resp.headers.get("X-RateLimit-Remaining")
            if remaining_str:
                remaining = int(remaining_str)
                if remaining < 100:
                    reset_str = resp.headers.get("X-RateLimit-Reset", "0")
                    reset_time = int(reset_str) if reset_str else 0
                    wait_seconds = max(reset_time - int(time.time()), 0) + 5
                    print(f"Rate limit low ({remaining} remaining), waiting {wait_seconds}s")
                    time.sleep(wait_seconds)
                    continue

            if resp.status_code == 200:
                return resp.json()
            elif resp.status_code in (403, 429):
                retry_after_str = resp.headers.get("Retry-After")
                retry_after = int(retry_after_str) if retry_after_str else RETRY_DELAYS[attempt]
                print(f"Rate limited (HTTP {resp.status_code}), retrying after {retry_after}s")
                time.sleep(retry_after)
                continue
            elif resp.status_code == 404:
                print(f"Resource not found: {url}")
                return None
            else:
                print(f"API error {resp.status_code}: {resp.text[:200]}")
                if attempt < RETRY_COUNT - 1:
                    time.sleep(RETRY_DELAYS[attempt])
                    continue
                return None
        except requests.exceptions.RequestException as e:
            print(f"Request failed (attempt {attempt + 1}): {e}")
            if attempt < RETRY_COUNT - 1:
                time.sleep(RETRY_DELAYS[attempt])
                continue
            return None
    return None


def get_commits_list(repo, branch, token, since_sha=None):
    all_commits = []
    page = 1

    while page <= MAX_PAGES:
        params = {"sha": branch, "per_page": PER_PAGE, "page": page}
        commits = github_request(
            f"{GITHUB_API}/repos/{repo}/commits", token, params
        )
        if commits is None:
            break

        if not commits:
            break

        for commit in commits:
            sha = commit["sha"]
            if since_sha and sha == since_sha:
                print(f"Reached anchor commit: {sha[:8]}")
                return all_commits
            all_commits.append(commit)

        if since_sha and len(commits) < PER_PAGE:
            print(f"Warning: anchor commit not found within {MAX_PAGES} pages, resetting anchor")
            return None

        page += 1

    print("Warning: anchor not found, resetting")
    return None

def get_commit_detail(repo, sha, token):
    return github_request(f"{GITHUB_API}/repos/{repo}/commits/{sha}", token)


def parse_commit_brief(commit_item):
    commit_data = commit_item.get("commit", {})
    commit_author = commit_data.get("author", {})

    return {
        "sha": commit_item["sha"],
        "author": {
            "name": commit_author.get("name", ""),
            "email": commit_author.get("email", ""),
        },
        "date": commit_author.get("date", ""),
        "message": commit_data.get("message", ""),
        "parents": [p["sha"] for p in commit_item.get("parents", [])],
        "stats": {},
        "files": [],
    }


def parse_commit_detail(detail):
    if detail is None:
        return None

    commit_data = detail.get("commit", {})
    commit_author = commit_data.get("author", {})

    stats = detail.get("stats", {})
    files = []
    for f in detail.get("files", []):
        files.append({
            "filename": f.get("filename", ""),
            "status": f.get("status", ""),
            "additions": f.get("additions", 0),
            "deletions": f.get("deletions", 0),
            "patch": f.get("patch", ""),
        })

    return {
        "sha": detail["sha"],
        "author": {
            "name": commit_author.get("name", ""),
            "email": commit_author.get("email", ""),
        },
        "date": commit_author.get("date", ""),
        "message": commit_data.get("message", ""),
        "parents": [p["sha"] for p in detail.get("parents", [])],
        "stats": {
            "total_additions": stats.get("additions", 0),
            "total_deletions": stats.get("deletions", 0),
            "files_changed": stats.get("total", 0),
        },
        "files": files,
    }


def convert_to_cn_time(iso_str):
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        dt_cn = dt.astimezone(TZ_CN)
        return dt_cn.isoformat()
    except (ValueError, AttributeError):
        return iso_str


def group_commits_by_date(commits_detail):
    groups = {}
    for c in commits_detail:
        date_str = convert_to_cn_time(c["date"])
        try:
            day = date_str[:10]
        except (ValueError, IndexError):
            day = "unknown"
        if day not in groups:
            groups[day] = []
        c["date"] = date_str
        groups[day].append(c)
    return groups


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


def merge_commits(existing_commits, new_commits):
    existing_shas = {c["sha"] for c in existing_commits}
    merged = list(existing_commits)
    for c in new_commits:
        if c["sha"] not in existing_shas:
            merged.append(c)
            existing_shas.add(c["sha"])
    merged.sort(key=lambda x: x.get("date", ""), reverse=True)
    return merged


def update_dates_index(data_dir, repo):
    repo_dir = os.path.join(data_dir, repo_dir_name(repo))
    commits_dir = os.path.join(repo_dir, "commits")
    dates_path = os.path.join(repo_dir, "dates.json")

    if not os.path.isdir(commits_dir):
        return

    dates = sorted(
        f.replace(".json", "")
        for f in os.listdir(commits_dir)
        if f.endswith(".json") and f != "meta.json" and re.match(r"^\d{4}-\d{2}-\d{2}$", f.replace(".json", ""))
    )

    save_json_atomic(dates_path, {"dates": dates})


def write_daily_commits(data_dir, repo, day, commits, branch="main", overwrite=False):
    repo_dir = os.path.join(data_dir, repo_dir_name(repo), "commits")
    filepath = os.path.join(repo_dir, f"{day}.json")

    if overwrite:
        merged = commits
    else:
        existing = load_json(filepath)
        if existing and "commits" in existing:
            merged = merge_commits(existing["commits"], commits)
        else:
            merged = commits

    data = {
        "date": day,
        "repo": repo,
        "branch": branch,
        "commits": merged,
    }
    save_json_atomic(filepath, data)
    print(f"Wrote {len(merged)} commits to {filepath}")
    update_dates_index(data_dir, repo)


def update_meta(data_dir, repo, latest_sha, branch="main"):
    repo_dir = os.path.join(data_dir, repo_dir_name(repo))
    meta_path = os.path.join(repo_dir, "meta.json")

    meta = {
        "repo": repo,
        "branch": branch,
        "last_commit_sha": latest_sha,
        "last_fetch_time": datetime.now(TZ_CN).isoformat(),
    }
    save_json_atomic(meta_path, meta)
    print(f"Updated meta: latest_sha={latest_sha[:8]}")


def get_current_sha_local(local_repo):
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=local_repo,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None


def get_commit_detail_local(local_repo, sha):
    try:
        result = subprocess.run(
            ["git", "show", "--format=%H%n%an%n%ae%n%aI%n%B", "--stat", "--patch", sha],
            cwd=local_repo,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return None
        output = result.stdout
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None

    lines = output.split("\n")
    if len(lines) < 5:
        return None

    sha_out = lines[0]
    author_name = lines[1]
    author_email = lines[2]
    author_date = lines[3]

    body_start = 4
    body_end = len(lines)
    for i in range(body_start, len(lines)):
        if lines[i].startswith("diff --git"):
            body_end = i
            break
    message_lines = lines[body_start:body_end]
    message = "\n".join(message_lines).strip()

    diff_start = None
    for i in range(body_end, len(lines)):
        if lines[i].startswith("diff --git"):
            diff_start = i
            break

    stats = {"total_additions": 0, "total_deletions": 0, "files_changed": 0}
    files = []

    if diff_start is not None:
        current_file = None
        file_additions = 0
        file_deletions = 0
        file_patch_lines = []

        i = diff_start
        while i < len(lines):
            line = lines[i]
            if line.startswith("diff --git"):
                if current_file is not None:
                    current_file["additions"] = file_additions
                    current_file["deletions"] = file_deletions
                    current_file["patch"] = "\n".join(file_patch_lines)
                    files.append(current_file)
                    stats["total_additions"] += file_additions
                    stats["total_deletions"] += file_deletions
                    stats["files_changed"] += 1

                parts = line.split(" b/", 1)
                if len(parts) == 2:
                    fname = parts[1]
                else:
                    fname = line.split()[-1] if line.split() else "unknown"
                current_file = {"filename": fname, "status": "modified", "additions": 0, "deletions": 0, "patch": ""}
                file_additions = 0
                file_deletions = 0
                file_patch_lines = []
                i += 1
                continue

            if current_file is not None:
                if line.startswith("new file"):
                    current_file["status"] = "added"
                elif line.startswith("deleted file"):
                    current_file["status"] = "removed"
                elif line.startswith("rename from"):
                    current_file["status"] = "renamed"

                if line.startswith("+") and not line.startswith("+++"):
                    file_additions += 1
                    file_patch_lines.append(line)
                elif line.startswith("-") and not line.startswith("---"):
                    file_deletions += 1
                    file_patch_lines.append(line)
                elif line.startswith("@@"):
                    file_patch_lines.append(line)
                elif not line.startswith("index ") and not line.startswith("---") and not line.startswith("+++") and not line.startswith("Binary"):
                    file_patch_lines.append(line)

            i += 1

        if current_file is not None:
            current_file["additions"] = file_additions
            current_file["deletions"] = file_deletions
            current_file["patch"] = "\n".join(file_patch_lines)
            files.append(current_file)
            stats["total_additions"] += file_additions
            stats["total_deletions"] += file_deletions
            stats["files_changed"] += 1

    parent_shas = []
    try:
        p_result = subprocess.run(
            ["git", "log", "--format=%P", "-1", sha],
            cwd=local_repo,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if p_result.returncode == 0 and p_result.stdout.strip():
            parent_shas = p_result.stdout.strip().split()
    except Exception:
        pass

    return {
        "sha": sha_out,
        "author": {"name": author_name, "email": author_email},
        "date": author_date,
        "message": message,
        "parents": parent_shas,
        "stats": stats,
        "files": files,
    }


def fetch_commits_from_local(local_repo, branch, data_dir, repo, since_sha=None):
    if since_sha:
        range_spec = f"{since_sha}..HEAD"
    else:
        range_spec = "--root"

    try:
        result = subprocess.run(
            ["git", "log", "--format=%H", range_spec],
            cwd=local_repo,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            print(f"git log failed: {result.stderr[:200]}")
            return None
        shas = result.stdout.strip().split("\n")
        shas = [s for s in shas if s]
    except subprocess.TimeoutExpired as e:
        print(f"git log error: {e}")
        return None

    if not shas:
        print("No new commits found via local git")
        return []

    print(f"Found {len(shas)} new commits via local git repo")

    commits_detail = []
    for i, sha in enumerate(shas):
        detail = get_commit_detail_local(local_repo, sha)
        if detail:
            commits_detail.append(detail)
        else:
            print(f"  Warning: failed to get detail for {sha[:8]}, skipping")
        if (i + 1) % 10 == 0:
            print(f"  Processed {i + 1}/{len(shas)} commits")

    return commits_detail


def refresh_date_commits(local_repo, repo, date, data_dir, branch, token):
    print(f"Refreshing commits for {repo} on {date}...")

    commits_detail = None

    if local_repo:
        try:
            after = f"{date}T00:00:00+08:00"
            before = f"{date}T23:59:59+08:00"
            result = subprocess.run(
                ["git", "log", "--format=%H", "--after", after, "--before", before, branch],
                cwd=local_repo,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0:
                shas = [s for s in result.stdout.strip().split("\n") if s]
                print(f"Found {len(shas)} commits on {date} via local git")
                commits_detail = []
                for sha in shas:
                    detail = get_commit_detail_local(local_repo, sha)
                    if detail:
                        commits_detail.append(detail)
            else:
                print(f"git log failed: {result.stderr[:200]}")
        except subprocess.TimeoutExpired:
            print("git log timed out")

    if commits_detail is None:
        if not token:
            print("GitHub token required for API-based refresh")
            return False

        since_iso = f"{date}T00:00:00+08:00"
        until_iso = f"{date}T23:59:59+08:00"
        since_utc = datetime.fromisoformat(since_iso).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        until_utc = datetime.fromisoformat(until_iso).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        params = {"sha": branch, "since": since_utc, "until": until_utc, "per_page": PER_PAGE}
        commits_list = github_request(f"{GITHUB_API}/repos/{repo}/commits", token, params)
        if not commits_list:
            print(f"No commits found on {date} via GitHub API")
            commits_detail = []
        else:
            print(f"Found {len(commits_list)} commits on {date} via GitHub API")
            commits_detail = []
            for c in commits_list:
                detail = get_commit_detail(repo, c["sha"], token)
                parsed = parse_commit_detail(detail)
                if parsed:
                    commits_detail.append(parsed)
                else:
                    commits_detail.append(parse_commit_brief(c))

    for c in commits_detail:
        c["date"] = convert_to_cn_time(c["date"])

    write_daily_commits(data_dir, repo, date, commits_detail, branch=branch, overwrite=True)
    print(f"Refreshed {len(commits_detail)} commits for {date}")
    return True


def fetch_commits(repo, branch, data_dir, token, local_repo=None, date=None):
    repo_dir = os.path.join(data_dir, repo_dir_name(repo))
    meta_path = os.path.join(repo_dir, "meta.json")
    meta = load_json(meta_path)

    since_sha = meta.get("last_commit_sha") if meta else None
    # Empty string anchor is equivalent to no anchor
    if since_sha == "":
        since_sha = None

    if not since_sha:
        os.makedirs(repo_dir, exist_ok=True)
        if local_repo:
            current_sha = get_current_sha_local(local_repo)
            if not current_sha:
                save_json_atomic(meta_path, {
                    "repo": repo,
                    "branch": branch,
                    "last_commit_sha": "",
                    "last_fetch_time": datetime.now(TZ_CN).isoformat(),
                })
                print("No commits found in local repo, initialized empty meta.json")
                return
            save_json_atomic(meta_path, {
                "repo": repo,
                "branch": branch,
                "last_commit_sha": current_sha,
                "last_fetch_time": datetime.now(TZ_CN).isoformat(),
            })
            print(f"Initialized meta.json with anchor: {current_sha[:8]} (no history fetched)")
            return
        else:
            commits_list = get_commits_list(repo, branch, token)
            os.makedirs(repo_dir, exist_ok=True)
            if not commits_list:
                save_json_atomic(meta_path, {
                    "repo": repo,
                    "branch": branch,
                    "last_commit_sha": "",
                    "last_fetch_time": datetime.now(TZ_CN).isoformat(),
                })
                print("No commits found, initialized empty meta.json")
                return

            latest_sha = commits_list[0]["sha"]
            save_json_atomic(meta_path, {
                "repo": repo,
                "branch": branch,
                "last_commit_sha": latest_sha,
                "last_fetch_time": datetime.now(TZ_CN).isoformat(),
            })
            print(f"Initialized meta.json with anchor: {latest_sha[:8]} (no history fetched)")
            return

    print(f"Fetching new commits for {repo} since {since_sha[:8]}...")

    commits_detail = None
    if local_repo:
        commits_detail = fetch_commits_from_local(local_repo, branch, data_dir, repo, since_sha=since_sha)

    if commits_detail is None:
        commits_list = get_commits_list(repo, branch, token, since_sha=since_sha)
        if commits_list is None:
            # Anchor not found — reset and start fresh
            print("Anchor commit not found in history, resetting meta.json anchor")
            since_sha = None
            save_json_atomic(meta_path, {
                "repo": repo,
                "branch": branch,
                "last_commit_sha": "",
                "last_fetch_time": datetime.now(TZ_CN).isoformat(),
            })
            return
        if not commits_list:
            if date:
                print(f"No new commits found for date {date}, writing empty file")
                write_daily_commits(data_dir, repo, date, [], branch=branch)
            else:
                print("No new commits found")
            return

        print(f"Found {len(commits_list)} new commits via GitHub API, fetching details...")

        commits_detail = []
        for i, c in enumerate(commits_list):
            detail = get_commit_detail(repo, c["sha"], token)
            parsed = parse_commit_detail(detail)
            if parsed:
                commits_detail.append(parsed)
            else:
                brief = parse_commit_brief(c)
                commits_detail.append(brief)
            if (i + 1) % 10 == 0:
                print(f"  Fetched {i + 1}/{len(commits_list)} commit details")

    if not commits_detail:
        print("No new commits found")
        return

    groups = group_commits_by_date(commits_detail)

    if date:
        groups = {day: commits for day, commits in groups.items() if day == date}
        if not groups:
            print(f"No commits found for date {date}, writing empty file")
            write_daily_commits(data_dir, repo, date, [], branch=branch)
            return

    total_new = 0
    for day, day_commits in sorted(groups.items()):
        write_daily_commits(data_dir, repo, day, day_commits, branch=branch)
        total_new += len(day_commits)

    if commits_detail:
        latest_sha = commits_detail[0]["sha"]
        update_meta(data_dir, repo, latest_sha, branch=branch)

    print(f"Done: fetched {total_new} new commits across {len(groups)} days")


def main():
    parser = argparse.ArgumentParser(description="Fetch commit data from GitHub")
    parser.add_argument("--repo", required=True, help="GitHub repo (owner/repo)")
    parser.add_argument("--branch", default="main", help="Branch to track")
    parser.add_argument("--data-dir", default="data", help="Data directory")
    parser.add_argument("--token", default=None, help="GitHub token (or set GITHUB_TOKEN env)")
    parser.add_argument("--local-repo", default=None, help="Path to local repo source code (auto-detected if not specified)")
    parser.add_argument("--refresh-date", default=None, help="Force re-fetch all commits on a specific date (YYYY-MM-DD) and overwrite existing data")
    parser.add_argument("--api-only", action="store_true", help="Skip local repo discovery, use GitHub API only")
    parser.add_argument("--date", default=None, help="Only keep commits on this date (YYYY-MM-DD, UTC+8)")
    args = parser.parse_args()

    token = args.token or os.environ.get("GITHUB_TOKEN")
    if not token:
        print("Warning: No GitHub token provided. API rate limit will be lower.")

    project_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if args.api_only:
        local_repo = None
    else:
        local_repo = ensure_repo(args.repo, args.local_repo, project_dir, branch=args.branch)

    if args.refresh_date:
        refresh_date_commits(local_repo, args.repo, args.refresh_date, args.data_dir, args.branch, token)
    else:
        fetch_commits(args.repo, args.branch, args.data_dir, token, local_repo=local_repo, date=args.date)


if __name__ == "__main__":
    main()
