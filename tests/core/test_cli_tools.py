"""새 CLI 동사와 MCP 도구가 같은 인자와 결과를 사용하는지 확인한다."""
from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from api import cli
from api.cli_tools import _tool_request
from vinc_mcp import tools


def _failure_json(text):
    # 스토어 기동 진단 뒤의 JSON 봉투를 검사한다.
    for line in text.splitlines(keepends=True):
        if line.startswith("{"):
            return json.loads(text[text.index(line):])
    raise AssertionError("failure JSON is missing")


class ToolVerbTests(unittest.TestCase):
    def test_flag_mapping(self):
        cases = [
            ("whoami", None, {}, "vinc_whoami", {}),
            ("conventions", None, {"lang": "ko"}, "vinc_conventions", {"lang": "ko"}),
            ("node", "get", {"id": "concept:x"}, "vinc_brief", {"query": "concept:x"}),
            ("node", "neighbors", {"id": "concept:x", "limit": 7},
             "vinc_neighbors", {"node": "concept:x", "limit": 7}),
            ("node", "graph", {"id": "concept:x", "depth": 3},
             "vinc_graph", {"seed_id": "concept:x", "depth": 3}),
            ("path", None, {"a": "concept:a", "b": "concept:b", "max_depth": 4},
             "vinc_path", {"a": "concept:a", "b": "concept:b", "max_depth": 4}),
            ("ties", None, {"id": "concept:x", "limit": 2},
             "vinc_suggest_ties", {"node": "concept:x", "limit": 2}),
            ("diagnose", None, {"kind": "components", "node": None},
             "vinc_diagnose", {"kind": "components", "node": None}),
            ("delete", None, {"id": "concept:x"},
             "vinc_delete", {"node_id": "concept:x"}),
            ("edge", "delete", {"from_id": "concept:a", "to_id": "concept:b",
                                 "type": "relates_to"},
             "vinc_delete_edge", {"from_id": "concept:a", "to_id": "concept:b",
                                  "type": "relates_to"}),
            ("merge", "nodes", {"keep": "concept:a", "drop": "concept:b"},
             "vinc_merge_nodes", {"keep_id": "concept:a", "drop_id": "concept:b"}),
            ("merge", "candidates", {"limit": 5},
             "vinc_merge_candidates", {"limit": 5}),
            ("merge", "rejections", {}, "vinc_merge_rejections", {}),
            ("merge", "reject", {"a": "a", "b": "b", "undo": True, "note": "distinct"},
             "vinc_merge_reject", {"a": "a", "b": "b", "undo": True, "note": "distinct"}),
            ("doc", "extract", {"id": "d", "apply": False},
             "vinc_doc_extract", {"doc_id": "d", "apply": False}),
        ]
        for cmd, subcmd, flags, name, expected in cases:
            with self.subTest(cmd=cmd, subcmd=subcmd):
                args = {"cmd": cmd, "owner": None, **flags}
                if subcmd:
                    args["nodecmd" if cmd == "node" else
                         "mergecmd" if cmd == "merge" else
                         "edgecmd" if cmd == "edge" else "doccmd"] = subcmd
                got_name, got_args, destructive = _tool_request(SimpleNamespace(**args))
                want_guard = (flags.get("id") if cmd == "delete" else
                              "edge" if cmd == "edge" else
                              flags.get("drop") if cmd == "merge" and subcmd == "nodes"
                              else None)
                self.assertEqual((got_name, got_args, destructive),
                                 (name, expected, want_guard))

    def test_call_registry_covers_all_24_tools(self):
        names = {item["name"] for item in tools.TOOLS}
        self.assertEqual(len(names), 24)
        guarded = {"vinc_delete", "vinc_delete_edge", "vinc_merge_nodes",
                   "vinc_doc_delete"}
        for name in sorted(names):
            with self.subTest(name=name):
                got_name, data, destructive = _tool_request(SimpleNamespace(
                    cmd="call", tool=name, args="{}", args_file=None))
                self.assertEqual(got_name, name)
                self.assertEqual(data, {})
                self.assertEqual(bool(destructive), name in guarded)

    def test_confirmation_required_in_pipe(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err), patch("sys.stdin", io.StringIO()):
            code = cli.main(["--format", "json", "delete", "concept:x"])
        self.assertEqual(code, 2)
        self.assertEqual(out.getvalue(), "")
        self.assertIn("--yes", err.getvalue())

    def test_unknown_tool_is_usage_error(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(["call", "vinc_absent", "--args", "{}"])
        self.assertEqual(code, 2)
        self.assertEqual(out.getvalue(), "")
        self.assertIn("unknown tool", err.getvalue())

    def test_doc_rm_requires_confirmation(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err), patch("sys.stdin", io.StringIO()):
            code = cli.main(["doc", "rm", "--id", "doc:x"])
        self.assertEqual(code, 2)
        self.assertEqual(out.getvalue(), "")
        self.assertIn("--yes", err.getvalue())

    def test_call_accepts_json_and_rejects_bad_arguments(self):
        with tempfile.TemporaryDirectory() as home:
            data = str(Path(home) / "db")
            with patch.dict("os.environ", {"MEMORY_DATA_DIR": data}):
                out, err = io.StringIO(), io.StringIO()
                with redirect_stdout(out), redirect_stderr(err):
                    code = cli.main(["--format", "json", "call", "vinc_neighbors",
                                     "--args", '{"node":"concept:missing"}'])
                self.assertEqual(code, 1)
                self.assertEqual(out.getvalue(), "")
                self.assertEqual(_failure_json(err.getvalue())["kind"], "neighbors")
                out, err = io.StringIO(), io.StringIO()
                with redirect_stdout(out), redirect_stderr(err):
                    code = cli.main(["call", "vinc_neighbors", "--args", "{}"])
                self.assertEqual(code, 2)
                self.assertEqual(out.getvalue(), "")
                self.assertIn("node is required", err.getvalue())

    def test_inprocess_result_equals_tool_call(self):
        from api.cli_tools import _bridge
        from vinc_mcp import tools

        with tempfile.TemporaryDirectory() as home:
            data = str(Path(home) / "db")
            args = SimpleNamespace(cmd="node", data=data)
            with patch.dict("os.environ", {"MEMORY_DATA_DIR": data}):
                with _bridge(args) as bridge:
                    expected = tools.call("vinc_neighbors", {"node": "concept:missing"}, bridge)
                out, err = io.StringIO(), io.StringIO()
                with redirect_stdout(out), redirect_stderr(err):
                    status = cli.main(["--format", "json", "node", "neighbors",
                                       "concept:missing"])
            self.assertEqual(status, 1)
            self.assertEqual(out.getvalue(), "")
            self.assertEqual(_failure_json(err.getvalue()), expected)


if __name__ == "__main__":
    unittest.main()
