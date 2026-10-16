"""파일 내보내기의 바이트 계약을 검증한다."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from api import cli, export


def test_live_sidecar_and_inprocess_export_match(tmp_path, monkeypatch):
    from api.auth import SessionAuth
    from api.server import VincApp, make_server
    from store.config import load_config
    from vinc_mcp import bridge as bridge_module

    data = str(tmp_path / "db")
    token = "export-test-token"
    app = VincApp.open(load_config({"data_dir": data}), auth=SessionAuth(token))
    httpd = make_server(app, port=0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    session = tmp_path / "session.json"
    session.write_text(json.dumps({"port": httpd.server_address[1],
                                   "token": token, "pid": os.getpid()}), encoding="utf-8")
    monkeypatch.setattr(bridge_module, "_session_path", lambda: str(session))
    monkeypatch.setenv("MEMORY_DATA_DIR", data)
    source = tmp_path / "guide.md"
    source.write_bytes(b"First line\nSecond line\n")
    try:
        assert app.store is not None
        with patch("sys.stdout", io.StringIO()):
            assert cli.main(["doc", "put", "--id", "doc:guide", "--owner", "user:local",
                             "--file", str(source), "--data", data, "--format", "json"]) == 0
        live = tmp_path / "live"
        with patch("sys.stdout", io.StringIO()):
            assert cli.main(["--format", "json", "export", "--format", "md", "--out",
                             str(live), "--owner", "user:local"]) == 0
    finally:
        httpd.shutdown()
        httpd.server_close()
        app.close()
    local = tmp_path / "local"
    with patch("sys.stdout", io.StringIO()):
        assert cli.main(["--format", "json", "export", "--format", "md", "--out",
                         str(local), "--owner", "user:local"]) == 0
    live_files = {p.relative_to(live).as_posix(): p.read_bytes()
                  for p in live.rglob("*") if p.is_file()}
    local_files = {p.relative_to(local).as_posix(): p.read_bytes()
                   for p in local.rglob("*") if p.is_file()}
    assert live_files == local_files
    assert b"First line\nSecond line\n" in live_files["documents/guide.md"]


def _fixture():
    nodes = {"executed": True, "nodes": [
        {"id": "concept:Alpha", "type": "concept", "title": "A, \"one\"\nnext",
         "domain": "vinc/design", "tags": ["x"], "props": {"z": 1}},
        {"id": "concept:alpha", "type": "concept", "title": "B",
         "domain": "vinc/design/details", "tags": [], "props": {}},
        {"id": "record:other", "type": "record", "title": "C",
         "domain": "other", "tags": [], "props": {}},
    ], "edges": [{"from": "concept:Alpha", "to": "concept:alpha",
                  "type": "relates_to", "evidence": "a source"}]}
    docs = {"docs": [{"doc_id": "doc:guide", "title": "Guide", "origin": "local",
                      "updated_at": "2026-09-27", "content_hash": "h", "text": "Line 1\nLine 2\n"}]}
    listed = {"executed": True, "docs": [{"doc_id": "doc:guide", "topic": "vinc/design"}]}
    return nodes, docs, listed


class ExportTests(unittest.TestCase):
    def test_graph_detail_fills_one_liner_and_evidence(self):
        # 옛 사이드카 번들처럼 one_liner·근거가 없으면 graph 읽기가 채운다.
        nodes, _, _ = _fixture()
        nodes["edges"][0].pop("evidence")
        export._enrich(nodes, {"executed": True,
                               "nodes": [{"id": "concept:Alpha", "one_liner": "summary"}],
                               "edges": [{"source": "concept:Alpha", "target": "concept:alpha",
                                          "type": "RELATED_TO", "evidence": "original quote"}]})
        self.assertEqual(nodes["nodes"][0]["one_liner"], "summary")
        self.assertEqual(nodes["edges"][0]["evidence"], "original quote")

    def test_bundle_values_win_over_graph_detail(self):
        # 번들이 나른 값은 정본이다. graph 읽기는 기록 대체처럼 그것이 못 보는 관계가 있다.
        nodes, _, _ = _fixture()
        nodes["nodes"][0]["one_liner"] = "from the bundle"
        export._enrich(nodes, {"executed": True,
                               "nodes": [{"id": "concept:Alpha", "one_liner": "summary"}],
                               "edges": [{"source": "concept:Alpha", "target": "concept:alpha",
                                          "type": "RELATED_TO", "evidence": "original quote"}]})
        self.assertEqual(nodes["nodes"][0]["one_liner"], "from the bundle")
        self.assertEqual(nodes["edges"][0]["evidence"], "a source")

    def test_markdown_order_case_collision_and_text(self):
        files = export.build_files("user:local", "md", *_fixture(), domain="vinc")
        self.assertIn("nodes/concept/Alpha.md", files)
        self.assertTrue(any(name.startswith("nodes/concept/alpha-") for name in files))
        self.assertNotIn("nodes/record/other.md", files)
        doc = files["documents/guide.md"].decode("utf-8")
        keys = [line.split(":", 1)[0] for line in doc.split("---\n", 2)[1].splitlines()]
        # 기존 키와 순서는 그대로고, 시간 필드는 뒤에만 붙는다.
        self.assertEqual(keys, ["doc_id", "title", "topic", "origin",
                                "updated_at", "content_hash", "content_time", "created_at",
                                "valid_from", "valid_until", "superseded_by"])
        self.assertTrue(doc.endswith("---\nLine 1\nLine 2\n"))

    def test_csv_quoting_and_excel_bom(self):
        files = export.build_files("user:local", "csv", *_fixture())
        self.assertFalse(files["nodes.csv"].startswith(b"\xef\xbb\xbf"))
        rows = list(csv.DictReader(io.StringIO(files["nodes.csv"].decode("utf-8"))))
        self.assertEqual(rows[0]["title"], 'A, "one"\nnext')
        excel = export.build_files("user:local", "csv", *_fixture(), excel=True)
        self.assertTrue(all(b.startswith(b"\xef\xbb\xbf") for b in excel.values()))

    def test_repeat_export_has_same_tree_hash(self):
        with tempfile.TemporaryDirectory() as temp:
            args = SimpleNamespace(owner="user:local", file_format="md", out=temp,
                                   domain=None, excel=False)
            with patch.object(export, "_payload", return_value=_fixture()):
                first = export.export_store(args)
                before = {p.relative_to(temp).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                          for p in Path(temp).rglob("*") if p.is_file()}
                second = export.export_store(args)
                after = {p.relative_to(temp).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                         for p in Path(temp).rglob("*") if p.is_file()}
            self.assertTrue(first["executed"])
            self.assertEqual(first["sha256"], second["sha256"])
            self.assertEqual(before, after)

    def test_format_option_levels(self):
        with patch.object(export, "export_store", return_value={"kind": "export",
                                                                   "executed": True}) as run:
            out = io.StringIO()
            with patch("sys.stdout", out):
                code = cli.main(["--format", "json", "export", "--format", "csv",
                                 "--owner", "user:local", "--out", "here"])
            self.assertEqual(code, 0)
            self.assertEqual(run.call_args.args[0].file_format, "csv")
            self.assertEqual(json.loads(out.getvalue())["kind"], "export")


if __name__ == "__main__":
    unittest.main()
