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
            ("node", "get", {"id": "concept:x"}, "vinc_node", {"node_id": "concept:x"}),
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

    def test_call_registry_covers_all_registered_tools(self):
        names = {item["name"] for item in tools.TOOLS}
        self.assertEqual(len(names), 37)
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
                    code = cli.main(["--target", "local", "--format", "json", "call", "vinc_neighbors",
                                     "--args", '{"node":"concept:missing"}'])
                self.assertEqual(code, 1)
                self.assertEqual(out.getvalue(), "")
                self.assertEqual(_failure_json(err.getvalue())["kind"], "neighbors")
                out, err = io.StringIO(), io.StringIO()
                with redirect_stdout(out), redirect_stderr(err):
                    code = cli.main(["--target", "local", "call", "vinc_neighbors", "--args", "{}"])
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
                    status = cli.main(["--target", "local", "--format", "json", "node", "neighbors",
                                       "concept:missing"])
            self.assertEqual(status, 1)
            self.assertEqual(out.getvalue(), "")
            self.assertEqual(_failure_json(err.getvalue()), expected)


if __name__ == "__main__":
    unittest.main()


class TemporalVerbTests(unittest.TestCase):
    def test_search_and_brief_forward_only_supplied_temporal_options(self):
        for cmd in ("search", "brief"):
            flags = {"cmd": cmd, "owner": None, "query": "synthetic", "top_k": 5,
                     "cursor": None, "detail": "summary", "max_excerpts": 3}
            _, defaults, _ = _tool_request(SimpleNamespace(**flags))
            self.assertFalse(set(defaults) & {"mode", "historical", "as_of", "known_at"})
            options = {"mode": "historical", "historical": True, "as_of": "2020", "known_at": "2021"}
            _, explicit, _ = _tool_request(SimpleNamespace(**flags, **options))
            self.assertTrue(all(explicit[key] == value for key, value in options.items()))


class DocPutFileTests(unittest.TestCase):
    """`doc put --file` 의 파일 이름 기본값은 만들 때만 쓴다. 생략은 보존, 빈 문자열은 지우기다."""

    def _put(self, **flags):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "draft-notes.md"
            path.write_text("# Draft\n\nbody\n", encoding="utf-8")
            base = {"cmd": "doc", "doccmd": "put", "owner": None, "id": None, "title": None,
                    "origin": None, "file": str(path), "create_only": None}
            name, data, _ = _tool_request(SimpleNamespace(**{**base, **flags}))
        self.assertEqual(name, "vinc_doc_put")
        return data

    def test_만들_때는_파일_이름이_기본값이다(self):
        for flags in ({}, {"id": "doc.new", "create_only": True}):
            with self.subTest(flags=flags):
                data = self._put(**flags)
                self.assertEqual((data["title"], data["origin"]), ("draft-notes", "draft-notes.md"))

    def test_고칠_때는_제목과_origin_을_보내지_않는다(self):
        data = self._put(id="doc.existing")
        self.assertIsNone(data["title"])
        self.assertIsNone(data["origin"])
        self.assertIn("# Draft", data["text"])

    def test_빈_문자열은_파일_이름으로_덮이지_않는다(self):
        for flags in ({}, {"id": "doc.existing"}):
            with self.subTest(flags=flags):
                data = self._put(title="", origin="", **flags)
                self.assertEqual((data["title"], data["origin"]), ("", ""))

    def test_표준_입력으로_고칠_때_origin_을_지우지_않는다(self):
        # 실제 인자 파서의 기본값을 거친다. 기본값이 빈 문자열이면 고칠 때마다 origin 이 지워진다.
        seen = []
        with patch("api.cli_tools.run", lambda args: (seen.append(args) or ({}, 0))),                 redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            cli.main(["--target", "local", "--format", "json", "doc", "put", "--id", "doc.existing"])
        with patch("sys.stdin", io.StringIO("body")):
            _, data, _ = _tool_request(seen[0])
        self.assertIsNone(data["origin"])
        self.assertIsNone(data["title"])
