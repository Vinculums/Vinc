"""Each public read verb has a pinned human view without changing JSON success bytes."""
import json

import pytest

from api import cli_output


NODE = {"id": "concept:a", "kind": "concept", "title": "Alpha", "domain": "vinc/test"}
ROW = {"id": "concept:a", "title": "Alpha", "domain": "vinc/test"}
CASES = [
    ("whoami", {"target": "account", "owner": "user:u", "scope": "personal", "key_scope": "read",
                 "spaces_source": "account", "spaces": [{"id": "personal", "name": "Personal", "role": "admin"}]},
     "target: account\nowner: user:u\nscope: personal\nkey_scope: read\nspaces_source: account\nspace: personal\tPersonal\tadmin\n"),
    ("conventions", {"text": "# Conventions\nRead before writing.\n"}, "# Conventions\nRead before writing.\n"),
    ("node get", {"node": {**NODE, "props": {"note": "Stored"}}},
     "concept:a\tconcept\tAlpha\tvinc/test\nnote: Stored\n"),
    ("node graph", {"seed": "concept:a", "depth": 1, "nodes": [ROW],
                    "edges": [{"source": "concept:a", "target": "concept:b", "type": "relates_to"}]},
     "seed: concept:a; depth: 1; nodes: 1; edges: 1\nconcept:a\tAlpha\tvinc/test\nconcept:a -> concept:b\trelates_to\n"),
    ("path", {"a": "concept:a", "b": "concept:b", "found": True, "length": 1,
              "paths": [{"ids": ["concept:a", "concept:b"], "length": 1}]},
     "concept:a -> concept:b; found: True; length: 1\nconcept:a -> concept:b\n"),
    ("ties", {"suggestions": [{**ROW, "reason": "same_domain", "score": 1.0}]},
     "concept:a\tAlpha\tvinc/test\tsame_domain\t1.0\n"),
    ("domains", {"rows": [{"domain": "vinc/test"}]}, "vinc/test\n"),
    ("search", {"rows": [ROW]}, "concept:a\tAlpha\tvinc/test\n"),
    ("recall", {"rows": [ROW]}, "concept:a\tAlpha\tvinc/test\n"),
    ("node neighbors", {"neighbors": [ROW]}, "concept:a\tAlpha\tvinc/test\n"),
    ("decide", {"topic": "Alpha", "depth": 1, "nodes": [ROW], "edges": []},
     "seed: Alpha; depth: 1; nodes: 1; edges: 0\nconcept:a\tAlpha\tvinc/test\n"),
    ("diagnose", {"components": [{"size": 2, "members": ["concept:a", "concept:b"]}]},
     "size: 2; members: ['concept:a', 'concept:b']\n"),
    ("merge candidates", {"candidates": [ROW]}, "concept:a\tAlpha\tvinc/test\n"),
    ("merge rejections", {"rejections": [ROW]}, "concept:a\tAlpha\tvinc/test\n"),
    ("doc ls", {"docs": [{"id": "canon", "title": "Canon"}]}, "canon\tCanon\n"),
    ("doc get", {"text": "# Canon\nExact text\n"}, "# Canon\nExact text\n"),
    ("brief", {"status": "resolved", "one_liner": "Evidence", "excerpts": [{"excerpt": "Quoted"}]},
     "Evidence\nQuoted\n"),
    ("brief", {"status": "choose", "candidates": [NODE]}, "choose\nconcept:a\tconcept\tAlpha\tvinc/test\n"),
]


@pytest.mark.parametrize("command,result,expected", CASES, ids=[c[0] for c in CASES])
def test_read_verb_human_golden_and_json_bytes(capsys, command, result, expected):
    cli_output.configure("human", command)
    assert cli_output.emit(result) == 0
    human = capsys.readouterr()
    assert human.out == expected and human.err == ""
    cli_output.configure("json", command)
    assert cli_output.emit(result) == 0
    machine = capsys.readouterr()
    assert machine.out == json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    assert machine.err == ""


def test_brief_none_remains_failure_and_carries_diagnostics(capsys):
    cli_output.configure("human", "brief")
    assert cli_output.emit({"status": "none", "query": "missing", "note": "No matches",
                            "unreadable": ["concept:private"], "candidates": []}) == 1
    got = capsys.readouterr()
    assert got.out == ""
    assert got.err == "vinc: none\nquery: missing\nnote: No matches\nunreadable: concept:private\n"


def test_human_graph_is_bounded_and_keeps_counts(capsys):
    cli_output.configure("human", "node graph")
    assert cli_output.emit({"seed": "concept:a", "depth": 2,
                            "nodes": [{**ROW, "title": "x" * 10000}] * 1000,
                            "edges": [{"source": "concept:a", "target": "concept:b", "type": "relates_to"}] * 1000}) == 0
    got = capsys.readouterr()
    assert "nodes: 1000; edges: 1000" in got.out
    assert "... 980 more" in got.out and "... 980 more edges" in got.out
    assert len(got.out) < 7000
