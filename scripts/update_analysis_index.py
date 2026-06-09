#!/usr/bin/env python3
"""
Generate analysis-dates.json for each repo (dates with completed analysis)
and update clean_stale_data to maintain both indices.
Call after analysis completes.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from source_repo import repo_dir_name


def update_analysis_index(data_dir, repo):
    repo_dir = os.path.join(data_dir, repo_dir_name(repo))
    analysis_dir = os.path.join(repo_dir, "analysis")

    if not os.path.isdir(analysis_dir):
        return

    dates = sorted(
        f.replace(".json", "")
        for f in os.listdir(analysis_dir)
        if f.endswith(".json") and f != ".gitkeep"
    )

    idx_path = os.path.join(repo_dir, "analysis-dates.json")
    with open(idx_path, "w") as fh:
        json.dump({"dates": dates}, fh, indent=2)


def main():
    repos = ["vllm-project/vllm", "vllm-project/vllm-ascend"]
    data_dir = sys.argv[1] if len(sys.argv) > 1 else "data"
    for repo in repos:
        update_analysis_index(data_dir, repo)
        print(f"Updated analysis-dates.json for {repo}")


if __name__ == "__main__":
    main()
