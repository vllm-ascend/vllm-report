"""Consumer-side robustness against architecture.json schema drift.

The weekly refresh-context job regenerates architecture.json via an LLM; some
weeks the output drifted from the schema (run 42733625373 crash:
`AttributeError: 'list' object has no attribute 'get'` at
analyze_commits.py:_load_not_used_by_ascend, and a 2026-10-06 crash
`TypeError: sequence item 0: expected str instance, dict found` in
build_context_section). Consumers must degrade gracefully instead of dying.
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from data import analyze_commits


GOOD_CONTEXT = {
    "overview": "vLLM is an inference engine",
    "modules": [
        {"path": "vllm/engine", "name": "engine", "description": "LLM engine", "key_classes": ["LLMEngine"]},
    ],
    "key_abstractions": [
        {
            "name": "Platform",
            "description": "platform abstraction",
            "location": "vllm/platforms/interface.py:137",
            "inherits_from": None,
            "key_methods": ["get_attn_backend_cls(...)"],
            "ascend_implementations": ["NPUPlatform"],
        },
    ],
    "implementation_principles": [
        {"module": "engine", "problem": "p", "workflow": "w", "interactions": "i"},
    ],
    "module_dependencies": "engine -> worker",
    "hardware_abstraction": {
        "description": "ha",
        "platform_independent": ["a"],
        "platform_specific": ["b"],
    },
    "interface_surface": {
        "description": "iface",
        "inheritable_interfaces": [
            {
                "interface": "Platform",
                "location": "vllm/platforms/interface.py",
                "ascend_impl": "NPUPlatform",
                "key_methods": ["m1"],
                "impact_rule": "r",
            },
        ],
        "not_used_by_ascend": ["docs/", "csrc/cuda/"],
    },
    "test_structure": {"path": "tests", "description": "t"},
    "repo": "vllm-project/vllm",
    "commit_sha": "a" * 40,
    "generated_at": "2026-08-01T20:25:03+08:00",
}

# What the 2026-10-12 weekly run committed (data on main today, commit bfd6b4e):
# renamed top-level keys, interface_surface as a list, module_dependencies as a
# dict of lists.
DRIFT_2026_10_12 = {
    "meta": {"repository": "vllm-project/vllm", "branch": "main", "analyzed_commit": "f26d9f4"},
    "project_overview": {
        "what": "vLLM is a high-throughput LLM inference engine",
        "problems_solved": ["PagedAttention"],
        "architecture_style": "layered",
        "key_design_principle": "pluggable",
    },
    "core_modules": [
        {"path": "vllm/engine", "responsibility": "LLM engine orchestration", "files": ["llm_engine.py"]},
    ],
    "key_abstractions": [
        {
            "name": "Platform",
            "file": "vllm/platforms/interface.py",
            "inherits_from": None,
            "ascend_implementations": ["NPUPlatform"],
            "key_methods": ["get_attn_backend_cls(...)"],
            "impact_rule": "impact",
        },
    ],
    "implementation_principles": [
        {"topic": "KV cache", "detail": "paged"},
    ],
    "module_dependencies": {
        "entrypoints": ["vllm/entrypoints/openai/api_server.py"],
        "engine.v1.LLMEngine": ["vllm/v1/engine/llm_engine.py"],
    },
    "hardware_abstraction": {
        "platform_agnostic_interfaces": ["Platform"],
        "platform_specific_implementations": ["NPUPlatform"],
        "oot_injection": "entrypoints",
    },
    "interface_surface": [
        {
            "interface": "Platform",
            "base_location": "vllm/platforms/interface.py:137",
            "ascend_impl": "vllm_ascend.platform.NPUPlatform",
            "critical_methods": ["get_attn_backend_cls(...)"],
            "change_consequences": ["backend selection"],
        },
    ],
    "cross_project_relationship": {
        "vllm_to_ascend_map": {},
        "ascend_only_components": [],
        "impact_judgment_rules": {},
        "patch_impact_map": {},
    },
}

# What the 2026-10-05 weekly run committed (commit d67d23d): key_methods
# elements are {"sig": ..., "desc": ...} dicts, which crashed `"; ".join`.
DRIFT_2026_10_05 = dict(GOOD_CONTEXT)
DRIFT_2026_10_05["key_abstractions"] = [
    {
        "name": "Platform",
        "description": "d",
        "location": "vllm/platforms/interface.py:135",
        "key_methods": [{"sig": "check_and_update_config(cls, vllm_config) -> None", "desc": "hook"}],
    },
]

# Whatever next week's drift looks like — no section may crash the pipeline.
HOSTILE_CONTEXT = dict(GOOD_CONTEXT)
HOSTILE_CONTEXT.update({
    "overview": {"what": ["unexpected", "dict"]},
    "modules": {"unexpected": "dict"},
    "key_abstractions": None,
    "implementation_principles": [None, 42],
    "module_dependencies": ["list", "not", "str"],
    "hardware_abstraction": ["not", "dict"],
    "interface_surface": [{"interface": None}, "str-entry", 3],
    "cross_project_relationship": {"impact_judgment_rules": None, "patch_impact_map": ["a"]},
    "test_structure": "not-a-dict",
})


class TestLoadNotUsedByAscend(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        analyze_commits._not_used_by_ascend_cache = None

    def tearDown(self):
        analyze_commits._not_used_by_ascend_cache = None

    def _write_context(self, context):
        ctx_dir = os.path.join(self.tmpdir, "vllm", "context")
        os.makedirs(ctx_dir)
        with open(os.path.join(ctx_dir, "architecture.json"), "w") as f:
            json.dump(context, f)

    def test_dict_shape_returns_set(self):
        self._write_context(GOOD_CONTEXT)
        result = analyze_commits._load_not_used_by_ascend(self.tmpdir)
        self.assertEqual(result, {"docs/", "csrc/cuda/"})

    def test_list_shape_returns_empty_set(self):
        # Exact crash from run 42733625373: interface_surface is a list.
        self._write_context(DRIFT_2026_10_12)
        result = analyze_commits._load_not_used_by_ascend(self.tmpdir)
        self.assertEqual(result, set())

    def test_missing_file_returns_empty_set(self):
        self.assertEqual(analyze_commits._load_not_used_by_ascend(self.tmpdir), set())

    def test_non_string_entries_filtered(self):
        ctx = dict(GOOD_CONTEXT)
        ctx["interface_surface"] = {
            "description": "iface",
            "not_used_by_ascend": ["docs/", 42, None, "", {"path": "x"}],
        }
        self._write_context(ctx)
        result = analyze_commits._load_not_used_by_ascend(self.tmpdir)
        self.assertEqual(result, {"docs/"})


class TestBuildContextSection(unittest.TestCase):
    def test_good_shape_renders_key_sections(self):
        text = analyze_commits.build_context_section(GOOD_CONTEXT)
        self.assertIn("项目概述", text)
        self.assertIn("核心模块", text)
        self.assertIn("可继承接口", text)
        self.assertIn("不被 Ascend 使用的路径", text)

    def test_drift_2026_10_12_does_not_crash_and_renders_interfaces(self):
        # Run 42733625373 crashed before this could render at all.
        text = analyze_commits.build_context_section(DRIFT_2026_10_12)
        self.assertIsInstance(text, str)
        self.assertTrue(len(text) > 200)
        self.assertIn("Platform", text)
        self.assertIn("NPUPlatform", text)
        self.assertIn("vLLM is a high-throughput LLM inference engine", text)
        self.assertIn("vllm/engine", text)
        self.assertIn("KV cache", text)

    def test_drift_2026_10_05_key_methods_dicts_do_not_crash(self):
        # 2026-10-06 run crashed: TypeError on "; ".join(dict elements).
        text = analyze_commits.build_context_section(DRIFT_2026_10_05)
        self.assertIn("check_and_update_config(cls, vllm_config) -> None", text)

    def test_hostile_shape_never_crashes(self):
        text = analyze_commits.build_context_section(HOSTILE_CONTEXT)
        self.assertIsInstance(text, str)
        self.assertTrue(text)

    def test_drifted_triage_survives_end_to_end(self):
        # The exact code path of the failing job: analyze_commits calls
        # _load_not_used_by_ascend then build_prompt -> build_context_section.
        self.assertIsInstance(
            analyze_commits.build_context_section(DRIFT_2026_10_12, data_dir=None), str
        )


if __name__ == "__main__":
    unittest.main()
