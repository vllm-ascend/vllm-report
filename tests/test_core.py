import json
import os
import sys
import tempfile
import unittest
import unittest.mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))


class TestBuildIndex(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.repo_dir = os.path.join(self.tmpdir, "vllm")
        os.makedirs(os.path.join(self.repo_dir, "analysis"))

    def _write_analysis(self, date, commits):
        path = os.path.join(self.repo_dir, "analysis", f"{date}.json")
        with open(path, "w") as f:
            json.dump({"date": date, "repo": "vllm-project/vllm", "commits": commits}, f)

    def test_build_index_architecture_impact_is_dict(self):
        from data.build_index import build_index

        self._write_analysis("2026-07-01", [
            {
                "sha": "a" * 40,
                "message": "test commit",
                "tags": ["feature", "attention"],
                "architecture_impact": {
                    "affects_architecture": True,
                    "affected_interfaces": ["AttentionBackend", "MLAAttentionSpec"],
                },
            }
        ])

        result = build_index(self.tmpdir, "vllm")
        self.assertTrue(result)

        index_path = os.path.join(self.repo_dir, "index.json")
        with open(index_path) as f:
            index = json.load(f)

        arch = index.get("architecture_impact_index", {})
        self.assertIn("a" * 40, arch)
        self.assertEqual(
            arch["a" * 40]["affected_interfaces"],
            ["AttentionBackend", "MLAAttentionSpec"],
        )

    def test_build_index_empty_commits(self):
        from data.build_index import build_index

        self._write_analysis("2026-07-01", [])
        result = build_index(self.tmpdir, "vllm")
        self.assertTrue(result)

        index_path = os.path.join(self.repo_dir, "index.json")
        with open(index_path) as f:
            index = json.load(f)

        self.assertEqual(index["architecture_impact_index"], {})

    def test_build_index_tags_index(self):
        from data.build_index import build_index

        self._write_analysis("2026-07-01", [
            {"sha": "a" * 40, "message": "feat: add attention", "tags": ["feature", "attention", "high-risk"]},
            {"sha": "b" * 40, "message": "fix: scheduler bug", "tags": ["bugfix", "scheduler"]},
        ])

        build_index(self.tmpdir, "vllm")

        index_path = os.path.join(self.repo_dir, "index.json")
        with open(index_path) as f:
            index = json.load(f)

        tags = index.get("tags_index", {})
        self.assertIn("feature", tags)
        self.assertIn("bugfix", tags)
        self.assertIn("attention", tags)
        self.assertIn("scheduler", tags)

        modules = index.get("modules_index", {})
        self.assertIn("attention", modules)
        self.assertIn("scheduler", modules)
        self.assertNotIn("feature", modules)
        self.assertNotIn("high-risk", modules)

    def test_commits_index(self):
        from data.build_index import build_index

        self._write_analysis("2026-07-01", [
            {"sha": "a" * 40, "message": "first commit", "tags": ["feature"]},
        ])

        build_index(self.tmpdir, "vllm")

        ci_path = os.path.join(self.repo_dir, "commits-index.json")
        with open(ci_path) as f:
            ci = json.load(f)

        self.assertIn("a" * 40, ci)
        self.assertEqual(ci["a" * 40]["date"], "2026-07-01")
        self.assertEqual(ci["a" * 40]["msg"], "first commit")


class TestExtractJson(unittest.TestCase):
    def test_extract_json_from_output_plain(self):
        from data.analyze_commits import extract_json_from_output

        result = extract_json_from_output('{"commits": [], "key": "value"}')
        self.assertEqual(result, {"commits": [], "key": "value"})

    def test_extract_json_from_output_with_code_fence(self):
        from data.analyze_commits import extract_json_from_output

        result = extract_json_from_output('```json\n{"commits": [], "key": "value"}\n```')
        self.assertEqual(result, {"commits": [], "key": "value"})

    def test_extract_json_from_output_with_trailing_text(self):
        from data.analyze_commits import extract_json_from_output

        text = '{"commits": [{"sha": "abc"}]}\n— some stats —'
        result = extract_json_from_output(text)
        self.assertEqual(result, {"commits": [{"sha": "abc"}]})

    def test_extract_json_from_output_none(self):
        from data.analyze_commits import extract_json_from_output

        self.assertIsNone(extract_json_from_output(None))
        self.assertIsNone(extract_json_from_output(""))
        self.assertIsNone(extract_json_from_output("no json here"))
        self.assertIsNone(extract_json_from_output('{"no_commits": true}'))


class TestSourceRepo(unittest.TestCase):
    def test_repo_dir_name(self):
        from data._source_repo import repo_dir_name

        self.assertEqual(repo_dir_name("vllm-project/vllm"), "vllm")
        self.assertEqual(repo_dir_name("vllm-project/vllm-ascend"), "vllm-ascend")
        self.assertEqual(repo_dir_name("custom/repo"), "repo")

    def test_known_repos_structure(self):
        from data._source_repo import KNOWN_REPOS

        for repo, config in KNOWN_REPOS.items():
            self.assertIn("dir_name", config)
            self.assertIn("url", config)
            self.assertIn("common_paths", config)
            self.assertTrue(config["url"].startswith("https://github.com/"))


class TestCleanStaleData(unittest.TestCase):
    def test_clean_stale_no_analysis_dir(self):
        # clean_stale_data lives in .github/scripts, not src/data
        import importlib.util
        script = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            ".github", "scripts", "clean_stale_data.py")
        spec = importlib.util.spec_from_file_location("clean_stale_data", script)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        clean_stale_data = mod.clean_stale_data

        tmpdir = tempfile.mkdtemp()
        repo_dir = os.path.join(tmpdir, "vllm")
        os.makedirs(os.path.join(repo_dir, "commits"))
        # No analysis dir

        result = clean_stale_data(tmpdir, "vllm-project/vllm")
        self.assertEqual(result, 0)


class TestRepoDirName(unittest.TestCase):
    def test_mcp_repo_dir_name(self):
        from mcp_server_app import repo_dir_name

        self.assertEqual(repo_dir_name("vllm"), "vllm")
        self.assertEqual(repo_dir_name("vllm-ascend"), "vllm-ascend")
        self.assertEqual(repo_dir_name("unknown"), "unknown")


if __name__ == "__main__":
    unittest.main()

class TestPersistLessonToRemote(unittest.TestCase):
    """Route rotation + rebase identity for lesson persistence.

    The a3-16-runner outage (09-12~09-15, zero lessons recorded) had three
    stacked causes this must prevent: the insteadOf-derived in-cluster route
    was missing (only gh-proxy/direct/anonymous-origin were tried), the
    rebase after a stranded commit died on "Committer identity unknown", and
    only the LAST route's error was reported.
    """

    def setUp(self):
        import mcp_server_app
        self.mcp = mcp_server_app
        self.tmpdir = tempfile.mkdtemp()
        self.data_dir = os.path.join(self.tmpdir, "data")
        os.makedirs(self.data_dir)
        self.mcp.data_dir = self.data_dir
        self.calls = []

        class _R:
            returncode, stdout, stderr = 0, "", ""

            def __init__(self, returncode=0, stdout="", stderr=""):
                self.returncode, self.stdout, self.stderr = (
                    returncode, stdout, stderr)

        self._R = _R

        def fake_run(cmd, cwd=None, env=None, capture_output=False, text=False):
            self.calls.append((list(cmd), env))
            if cmd[:2] == ["git", "status"]:
                return _R(stdout=" M vllm-ascend/lessons/x.json")
            if cmd[:2] == ["git", "config"]:
                return _R(stdout="url.http://git-cdn:8000/https://github.com/"
                                 ".insteadof https://github.com/\n")
            if cmd[:2] == ["git", "push"]:
                return _R(returncode=self.push_rc,
                          stderr=self.push_stderr)
            return _R()

        self._orig_run = self.mcp.subprocess.run
        self.mcp.subprocess.run = fake_run
        self.push_rc, self.push_stderr = 0, ""

    def tearDown(self):
        self.mcp.subprocess.run = self._orig_run

    def test_gitcdn_route_first_and_rebase_identity(self):
        with unittest.mock.patch.dict(os.environ, {"GH_TOKEN": "TESTTOKEN"}):
            result = self.mcp._persist_lesson_to_remote()
        self.assertEqual(result, "")
        push = self.calls[-1][0]
        self.assertEqual(push[:2], ["git", "push"])
        # insteadOf-derived route (in-cluster, no WAF) comes first
        self.assertEqual(
            push[2], "http://x-access-token:TESTTOKEN@git-cdn:8000/"
                     "https://github.com/vllm-ascend/vllm-report.git")
        rebases = [c for c in self.calls if c[0][:2] == ["git", "rebase"]
                   and "--abort" not in c[0]]
        self.assertTrue(rebases)
        env = rebases[0][1]
        self.assertEqual(env["GIT_AUTHOR_NAME"], "vllm-report-bot")
        self.assertEqual(env["GIT_COMMITTER_NAME"], "vllm-report-bot")

    def test_all_route_errors_reported_without_token(self):
        self.push_rc = 128
        self.push_stderr = ("fatal: could not read Username for "
                            "'http://git-cdn': No such device or address")
        with unittest.mock.patch.dict(os.environ, {"GH_TOKEN": "TESTTOKEN"}):
            result = self.mcp._persist_lesson_to_remote()
        self.assertIn("all routes", result)
        self.assertIn("gh-proxy.test.osinfra.cn", result)
        self.assertIn("origin", result)
        self.assertNotIn("TESTTOKEN", result)
        pushes = [c for c in self.calls if c[0][:2] == ["git", "push"]]
        self.assertEqual(len(pushes), 4)  # gitcdn, gh-proxy, direct, origin
