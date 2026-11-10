"""Producer-side schema gate for architecture context generation.

The weekly refresh-context job used to save whatever dict the LLM returned,
with no validation against _build_architecture_schema(). Drifted outputs got
committed (e.g. 2026-09-28 1cbf711, 2026-10-12 bfd6b4e) and killed the daily
pipeline for weeks. The generator must validate before saving and retry once
with the validation errors before giving up.
"""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from data import generate_context


# Minimal dict that satisfies _build_architecture_schema().
VALID_CONTEXT = {
    "overview": "vLLM is an inference engine",
    "modules": [
        {"path": "vllm/engine", "name": "engine", "description": "LLM engine"},
    ],
    "key_abstractions": [
        {"name": "Platform", "description": "platform abstraction", "location": "vllm/platforms/interface.py"},
    ],
    "implementation_principles": [
        {"module": "engine", "problem": "p", "workflow": "w", "interactions": "i"},
    ],
    "module_dependencies": "engine -> worker",
    "hardware_abstraction": {"description": "ha"},
    "interface_surface": {"description": "iface"},
}

# Same drift as the 2026-10-12 commit (bfd6b4e): renamed keys + list-shaped
# interface_surface. Must be rejected, never saved.
DRIFTED_CONTEXT = {
    "project_overview": {"what": "vLLM"},
    "core_modules": [{"path": "vllm/engine", "responsibility": "engine"}],
    "key_abstractions": [{"name": "Platform", "file": "vllm/platforms/interface.py"}],
    "implementation_principles": [{"topic": "KV cache", "detail": "paged"}],
    "module_dependencies": {"entrypoints": ["vllm/entrypoints/openai/api_server.py"]},
    "hardware_abstraction": {"platform_agnostic_interfaces": ["Platform"]},
    "interface_surface": [
        {"interface": "Platform", "base_location": "vllm/platforms/interface.py:137"},
    ],
}


class TestGenerateContextValidation(unittest.TestCase):
    def setUp(self):
        self.data_dir = tempfile.mkdtemp()
        self.local_repo = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.local_repo, "vllm"))
        with open(os.path.join(self.local_repo, "vllm", "engine.py"), "w") as f:
            f.write("# placeholder\n")
        self.context_path = os.path.join(self.data_dir, "vllm", "context", "architecture.json")
        patcher = mock.patch("data.generate_context.call_opencode")
        self.call_opencode = patcher.start()
        self.addCleanup(patcher.stop)

    def _generate(self):
        return generate_context.generate_context(
            "vllm-project/vllm", self.data_dir, force=True, local_repo=self.local_repo
        )

    def test_valid_output_is_saved(self):
        self.call_opencode.return_value = dict(VALID_CONTEXT)
        self.assertTrue(self._generate())
        with open(self.context_path) as f:
            saved = json.load(f)
        self.assertEqual(saved["repo"], "vllm-project/vllm")
        self.assertIn("generated_at", saved)
        self.assertIn("commit_sha", saved)

    def test_drifted_output_is_rejected_and_not_saved(self):
        self.call_opencode.return_value = dict(DRIFTED_CONTEXT)
        self.assertFalse(self._generate())
        self.assertFalse(os.path.exists(self.context_path))

    def test_non_dict_output_is_rejected_and_not_saved(self):
        self.call_opencode.return_value = ["not", "a", "dict"]
        self.assertFalse(self._generate())
        self.assertFalse(os.path.exists(self.context_path))

    def test_retry_succeeds_after_one_drifted_attempt(self):
        self.call_opencode.side_effect = [dict(DRIFTED_CONTEXT), dict(VALID_CONTEXT)]
        self.assertTrue(self._generate())
        self.assertEqual(self.call_opencode.call_count, 2)
        self.assertTrue(os.path.exists(self.context_path))
        # The retry prompt must carry the validation errors back to the model.
        retry_prompt = self.call_opencode.call_args_list[1].kwargs["prompt"]
        self.assertIn("Schema", retry_prompt)

    def test_retry_exhausted_rejects(self):
        self.call_opencode.side_effect = [dict(DRIFTED_CONTEXT), dict(DRIFTED_CONTEXT)]
        self.assertFalse(self._generate())
        self.assertEqual(self.call_opencode.call_count, 2)
        self.assertFalse(os.path.exists(self.context_path))

    def test_cross_reference_rejects_drifted_output(self):
        # Phase 2 writes cross_project_relationship into both architecture
        # files; a drifted response must not be written into either.
        for repo_dir in ("vllm", "vllm-ascend"):
            os.makedirs(os.path.join(self.data_dir, repo_dir, "context"))
            with open(os.path.join(self.data_dir, repo_dir, "context", "architecture.json"), "w") as f:
                json.dump(dict(VALID_CONTEXT, repo=f"vllm-project/{repo_dir}"), f)
        self.call_opencode.return_value = {"unexpected": ["shape"]}
        self.assertFalse(
            generate_context.generate_cross_reference(self.data_dir, force=True)
        )
        for repo_dir in ("vllm", "vllm-ascend"):
            with open(os.path.join(self.data_dir, repo_dir, "context", "architecture.json")) as f:
                saved = json.load(f)
            self.assertNotIn("cross_project_relationship", saved)


if __name__ == "__main__":
    unittest.main()
