# -*- coding: utf-8 -*-
"""호출 수 하네스(`scripts/qa/graph_build_calls.py`)가 세는 방식과 고정 문서의 모양."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.qa import graph_build_calls as h  # noqa: E402


def test_ten_fixed_documents_with_headings_and_topics():
    docs = h.fixtures()
    assert len(docs) == 10
    assert len({d["doc_id"] for d in docs}) == 10
    for d in docs:
        assert d["title"] and d["topic"].startswith("harbor/"), d["file"]
        headings = [l for l in d["text"].splitlines() if l.startswith("## ")]
        assert len(headings) >= 4, d["file"]
        assert "—" not in d["text"] and "–" not in d["text"]


def _event(*blocks, role="assistant"):
    return json.dumps({"type": role, "message": {"role": role, "content": list(blocks)}})


def test_count_dedupes_attributes_and_flags_errors(tmp_path):
    conv = {"type": "tool_use", "id": "t1", "name": "mcp__vinceval__vinc_conventions", "input": {}}
    get = {"type": "tool_use", "id": "t2", "name": "mcp__vinceval__vinc_doc_get",
           "input": {"doc_id": "gbc-01"}}
    ingest = {"type": "tool_use", "id": "t3", "name": "mcp__vinceval__vinc_ingest",
              "input": {"nodes": [], "edges": [
                  {"from": "record:r", "to": "concept:doc.gbc-02", "type": "derives_from"}]}}
    bash = {"type": "tool_use", "id": "t4", "name": "Bash", "input": {"command": "ls"}}
    lines = [_event(conv, get), _event(conv, get),   # 스트림이 같은 블록을 되풀이한다
             _event({"type": "tool_result", "tool_use_id": "t2", "is_error": True}, role="user"),
             _event(ingest, bash)]
    path = tmp_path / "s.jsonl"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    got = h.count([path])
    f = got["files"][0]
    assert f["vinc_calls"] == 3 and f["vinc_errors"] == 1 and f["other_calls"] == 1
    assert f["by_tool"] == {"vinc_conventions": 1, "vinc_doc_get": 1, "vinc_ingest": 1}
    assert f["by_document"] == {"(session)": 1, "gbc-01": 1, "gbc-02": 1}
    assert got["per_file"]["median"] == 3


def test_prompts_and_config():
    assert "`gbc-03`" in h.prompt("builtin", "gbc-03")
    plain = h.prompt("plain", "gbc-03")
    assert "gbc-03" in plain and "vinc_doc_extract" not in plain
    cfg = h.mcp_config("http://127.0.0.1:8799", "-tok", ".", "python")["mcpServers"][h.SERVER]
    assert "--token=-tok" in cfg["args"] and "--no-spawn" in cfg["args"]
    assert cfg["env"]["PYTHONPATH"]
