import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

SCRIPT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    ".github", "scripts", "clean_stale_data.py",
)


def _load_script():
    # clean_stale_data lives in .github/scripts, not src/data — load by path.
    # Registered under a distinct name so tests can `from clean_stale_data_mod import ...`.
    spec = importlib.util.spec_from_file_location("clean_stale_data_mod", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["clean_stale_data_mod"] = mod
    spec.loader.exec_module(mod)
    return mod


_load_script()


def _d(days_ago):
    return (date.today() - timedelta(days=days_ago)).isoformat()


class TestRetention(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        for repo in ("vllm", "vllm-ascend"):
            for sub in ("commits", "analysis"):
                os.makedirs(os.path.join(self.tmpdir, repo, sub))

    def _write(self, relpath, content):
        path = os.path.join(self.tmpdir, relpath)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(content if isinstance(content, str) else json.dumps(content))
        return path

    def _commit_file(self, date, shas=()):
        return {
            "date": date,
            "commits": [{"sha": s, "message": "m"} for s in shas],
        }

    def _analysis_file(self, date, shas=()):
        return {
            "date": date,
            "repo": "vllm-project/vllm",
            "commits": [{"sha": s, "message": "m", "tags": []} for s in shas],
        }

    def _status(self, commits, baseline_sha=""):
        return {
            "baseline": {
                "source": "vllm-ascend/.github/vllm-main-verified.commit",
                "main_sha": baseline_sha,
                "release_tag": "",
                "tracking_start_date": _d(20),
                "baseline_date": _d(20),
            },
            "commits": commits,
            "stats": {"total": len(commits), "pending": 0, "adapted": len(commits)},
        }

    def test_retention_cutoff(self):
        from clean_stale_data_mod import retention_cutoff
        self.assertEqual(retention_cutoff(14, today=date(2026, 9, 15)), "2026-09-01")
        self.assertEqual(retention_cutoff(0, today=date(2026, 9, 15)), "2026-09-15")

    def test_orphan_cleanup_behavior_unchanged(self):
        from clean_stale_data_mod import clean_stale_data
        # orphan (commits without analysis, non-empty) -> removed
        self._write("vllm/commits/2020-01-01.json", self._commit_file("2020-01-01", ["a" * 40]))
        # analyzed -> kept
        self._write("vllm/commits/2020-01-02.json", self._commit_file("2020-01-02", ["b" * 40]))
        self._write("vllm/analysis/2020-01-02.json", self._analysis_file("2020-01-02", ["b" * 40]))
        # empty -> kept
        self._write("vllm/commits/2020-01-03.json", {"date": "2020-01-03", "commits": []})
        removed = clean_stale_data(self.tmpdir, "vllm-project/vllm")
        self.assertEqual(removed, 1)
        self.assertFalse(os.path.exists(os.path.join(self.tmpdir, "vllm/commits/2020-01-01.json")))
        self.assertTrue(os.path.exists(os.path.join(self.tmpdir, "vllm/commits/2020-01-02.json")))
        self.assertTrue(os.path.exists(os.path.join(self.tmpdir, "vllm/commits/2020-01-03.json")))

    def test_retention_drops_old_date_files_in_both_repos(self):
        from clean_stale_data_mod import apply_retention, retention_cutoff
        cutoff = retention_cutoff(14)
        old, fresh = _d(30), _d(1)
        for repo in ("vllm", "vllm-ascend"):
            self._write(f"{repo}/commits/{old}.json", self._commit_file(old, ["a" * 40]))
            self._write(f"{repo}/commits/{fresh}.json", self._commit_file(fresh))
            self._write(f"{repo}/analysis/{old}.json", self._analysis_file(old, ["a" * 40]))
            self._write(f"{repo}/analysis/{fresh}.json", self._analysis_file(fresh))
        self._write(f"vllm-ascend/lessons/{old}.json", {"lessons": []})
        self._write(f"vllm-ascend/lessons/{fresh}.json", {"lessons": []})
        self._write(f"vllm-ascend/pr_ci_results/{old}.json", {"results": []})
        self._write(f"vllm-ascend/pr_ci_results/{fresh}.json", {"results": []})

        removed = apply_retention(self.tmpdir, cutoff, set(), "vllm-project/vllm")
        removed += apply_retention(self.tmpdir, cutoff, set(), "vllm-project/vllm-ascend")
        self.assertEqual(removed, 6)
        for repo in ("vllm", "vllm-ascend"):
            self.assertFalse(os.path.exists(os.path.join(self.tmpdir, repo, "commits", f"{old}.json")))
            self.assertFalse(os.path.exists(os.path.join(self.tmpdir, repo, "analysis", f"{old}.json")))
            self.assertTrue(os.path.exists(os.path.join(self.tmpdir, repo, "commits", f"{fresh}.json")))
            self.assertTrue(os.path.exists(os.path.join(self.tmpdir, repo, "analysis", f"{fresh}.json")))
        self.assertFalse(os.path.exists(os.path.join(self.tmpdir, "vllm-ascend/lessons", f"{old}.json")))
        self.assertTrue(os.path.exists(os.path.join(self.tmpdir, "vllm-ascend/lessons", f"{fresh}.json")))
        self.assertFalse(os.path.exists(os.path.join(self.tmpdir, "vllm-ascend/pr_ci_results", f"{old}.json")))
        self.assertTrue(os.path.exists(os.path.join(self.tmpdir, "vllm-ascend/pr_ci_results", f"{fresh}.json")))

    def test_retention_covers_date_prefixed_names(self):
        from clean_stale_data_mod import apply_retention, retention_cutoff
        cutoff = retention_cutoff(14)
        old, fresh = _d(30), _d(1)
        base = "vllm-ascend/pr_ci_results"
        # Date-prefixed snapshot names (e.g. per-day failure-analysis files
        # that must not collide with a tracker's plain <date>.json) sunset
        # by their leading date, exactly like plain <date>.json files.
        self._write(f"{base}/{old}-failure-analysis.json", {"kind": "x"})
        self._write(f"{base}/{fresh}-failure-analysis.json", {"kind": "x"})
        # Look-alikes without a date-prefix boundary survive: an undated
        # file and a date-prefixed backup are not date-keyed data.
        self._write(f"{base}/failure-analysis.json", {"kind": "x"})
        self._write(f"{base}/{old}.json.bak", {"kind": "x"})
        removed = apply_retention(self.tmpdir, cutoff, set(), "vllm-project/vllm-ascend")
        self.assertEqual(removed, 1)
        self.assertFalse(os.path.exists(os.path.join(self.tmpdir, base, f"{old}-failure-analysis.json")))
        self.assertTrue(os.path.exists(os.path.join(self.tmpdir, base, f"{fresh}-failure-analysis.json")))
        self.assertTrue(os.path.exists(os.path.join(self.tmpdir, base, "failure-analysis.json")))
        self.assertTrue(os.path.exists(os.path.join(self.tmpdir, base, f"{old}.json.bak")))

    def test_retention_boundary_date_kept(self):
        from clean_stale_data_mod import apply_retention, retention_cutoff
        cutoff = retention_cutoff(14)
        # exactly at cutoff -> kept; one day older -> dropped
        self._write(f"vllm/commits/{cutoff}.json", self._commit_file(cutoff))
        older = (date.fromisoformat(cutoff) - timedelta(days=1)).isoformat()
        self._write(f"vllm/commits/{older}.json", self._commit_file(older))
        removed = apply_retention(self.tmpdir, cutoff, set(), "vllm-project/vllm")
        self.assertEqual(removed, 1)
        self.assertTrue(os.path.exists(os.path.join(self.tmpdir, "vllm/commits", f"{cutoff}.json")))
        self.assertFalse(os.path.exists(os.path.join(self.tmpdir, "vllm/commits", f"{older}.json")))

    def test_protected_dates_never_dropped(self):
        from clean_stale_data_mod import apply_retention, protected_dates, retention_cutoff
        old = _d(30)
        tracked = {
            "sha": "c" * 40, "upstream_date": old, "message": "tracked",
            "status": "pending", "adapted_at": None, "adapted_by": None,
            "ascend_impact_summary": "", "adaptation_notes": "", "tags": [],
        }
        self._write("vllm-ascend/adaptation-status.json", self._status([tracked]))
        self._write(f"vllm/analysis/{old}.json", self._analysis_file(old, ["c" * 40]))
        self._write(f"vllm/commits/{old}.json", self._commit_file(old, ["c" * 40]))
        protected = protected_dates(self.tmpdir)
        self.assertIn(old, protected)
        self.assertIn(_d(20), protected)  # baseline_date / tracking_start_date
        cutoff = retention_cutoff(14)
        removed = apply_retention(self.tmpdir, cutoff, protected, "vllm-project/vllm")
        self.assertEqual(removed, 0)
        self.assertTrue(os.path.exists(os.path.join(self.tmpdir, "vllm/analysis", f"{old}.json")))
        self.assertTrue(os.path.exists(os.path.join(self.tmpdir, "vllm/commits", f"{old}.json")))

    def test_baseline_sha_analysis_file_protected(self):
        from clean_stale_data_mod import apply_retention, protected_dates, retention_cutoff
        old = _d(40)
        baseline_sha = "d" * 40
        self._write("vllm-ascend/adaptation-status.json", self._status([], baseline_sha=baseline_sha))
        self._write(f"vllm/analysis/{old}.json", self._analysis_file(old, [baseline_sha]))
        protected = protected_dates(self.tmpdir)
        self.assertIn(old, protected)
        removed = apply_retention(self.tmpdir, retention_cutoff(14), protected, "vllm-project/vllm")
        self.assertEqual(removed, 0)

    def test_non_date_files_and_context_untouched(self):
        from clean_stale_data_mod import apply_retention, retention_cutoff
        old = _d(30)
        self._write("vllm/adaptation-status.json", self._status([]))
        self._write("vllm/index.json", {"entries": []})
        self._write("vllm/commits/.gitkeep", "")
        self._write("vllm/context/architecture.json", {"modules": []})
        self._write(f"vllm/context/{old}.json", {"sneaky": True})
        removed = apply_retention(self.tmpdir, retention_cutoff(14), set(), "vllm-project/vllm")
        self.assertEqual(removed, 0)
        for p in ("vllm/adaptation-status.json", "vllm/index.json",
                  "vllm/context/architecture.json", f"vllm/context/{old}.json",
                  "vllm/commits/.gitkeep"):
            self.assertTrue(os.path.exists(os.path.join(self.tmpdir, p)), p)

    def test_retention_disabled_keeps_everything(self):
        from clean_stale_data_mod import apply_retention
        old = _d(30)
        self._write(f"vllm/commits/{old}.json", self._commit_file(old, ["a" * 40]))
        # a far-future cutoff drops the file (guard helper behaves)
        future = (date.today() + timedelta(days=365)).isoformat()
        self.assertEqual(apply_retention(self.tmpdir, future, set(), "vllm-project/vllm"), 1)
        # but the CLI with --retention-days 0 removes nothing (analyzed file:
        # orphan cleanup keeps it too, so only retention could remove it)
        self._write(f"vllm/commits/{old}.json", self._commit_file(old, ["a" * 40]))
        self._write(f"vllm/analysis/{old}.json", self._analysis_file(old, ["a" * 40]))
        out = subprocess.run(
            [sys.executable, SCRIPT, "--data-dir", self.tmpdir, "--retention-days", "0"],
            capture_output=True, text=True,
        )
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertTrue(os.path.exists(os.path.join(self.tmpdir, "vllm/commits", f"{old}.json")))
        self.assertTrue(os.path.exists(os.path.join(self.tmpdir, "vllm/analysis", f"{old}.json")))

    def test_source_context_cache_by_cached_at(self):
        from clean_stale_data_mod import clean_source_context_cache, retention_cutoff
        cutoff = retention_cutoff(14)
        base = "vllm/_deep_analysis_cache/source_context"
        self._write(f"{base}/fresh.json", {"content_hash": "h1", "analysis": "a",
                                           "cached_at": _d(1) + "T10:00:00+08:00"})
        self._write(f"{base}/stale.json", {"content_hash": "h2", "analysis": "a",
                                           "cached_at": _d(30) + "T10:00:00+08:00"})
        self._write(f"{base}/legacy.json", {"content_hash": "h3", "analysis": "a"})
        self._write(f"{base}/corrupt.json", "{not json")
        removed = clean_source_context_cache(self.tmpdir, cutoff)
        self.assertEqual(removed, 3)
        self.assertTrue(os.path.exists(os.path.join(self.tmpdir, base, "fresh.json")))
        for name in ("stale.json", "legacy.json", "corrupt.json"):
            self.assertFalse(os.path.exists(os.path.join(self.tmpdir, base, name)), name)

    def test_main_cli_end_to_end(self):
        old = _d(30)
        self._write(f"vllm/commits/{old}.json", self._commit_file(old, ["a" * 40]))
        self._write(f"vllm/analysis/{old}.json", self._analysis_file(old, ["a" * 40]))
        out = subprocess.run(
            [sys.executable, SCRIPT, "--data-dir", self.tmpdir, "--retention-days", "14"],
            capture_output=True, text=True,
        )
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("Total: 2 files removed", out.stdout)
        self.assertFalse(os.path.exists(os.path.join(self.tmpdir, "vllm/commits", f"{old}.json")))
        self.assertFalse(os.path.exists(os.path.join(self.tmpdir, "vllm/analysis", f"{old}.json")))


if __name__ == "__main__":
    unittest.main()
