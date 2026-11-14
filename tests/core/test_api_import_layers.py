# -*- coding: utf-8 -*-
"""쓰는 모듈과 읽는 모듈이 서로를 import 하지 않는다.

잠그는 것:
- 쓰기 모듈(`api.fragment`·`api.docstore`·`api.docevidence`)은 읽기 파사드 `api.query` 를 import 하지
  않는다. 최상위든 함수 안이든 같다. 필요한 이름은 아래 층에서 바로 읽는다:
  `_props_to_obj`·`domain_names` 는 `store.querybase`, `_query_fingerprint`·`cursor_*` 는
  `vinc_rag.retrieval`.
- 읽기 모듈(`decide_read`·`diagnose`·`cli_gate`·`docextract`)은 쓰기 모듈의 사설 도우미
  (`_split_subject`·`_norm_subject_path`·`_norm_content_time`)를 가져오지 않는다. 그 셋의 정본은
  `api.node_schema`(`split_subject`·`normalize_subject_path`·`normalize_time`) 다.
- 옛 사설 이름은 정본과 같은 객체로 남는다(`tests/core/test_node_schema.py` 가 표로 대조한다).
- 위 모듈과 서버·CLI 는 새 인터프리터에서 맨 처음 import 해도 순환 없이 뜬다.
"""
from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from api import decide_read, diagnose, docevidence, docstore, fragment, node_schema, query  # noqa: E402
from store import querybase  # noqa: E402
from vinc_rag import retrieval  # noqa: E402

WRITE_MODULES = ("fragment", "docstore", "docevidence")


def _imports(module: str) -> list[tuple[str, tuple[str, ...]]]:
    """`api/<module>.py` 의 모든 import(함수 안 포함)를 (절대 모듈 이름, 가져온 이름들) 로 편다."""
    tree = ast.parse((ROOT / "api" / f"{module}.py").read_text(encoding="utf-8"))
    found: list[tuple[str, tuple[str, ...]]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [(alias.name, ()) for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = ("api." + node.module if node.module else "api") if node.level else (node.module or "")
            found.append((base, tuple(alias.name for alias in node.names)))
    return found


def _reaches(module: str, target: str) -> bool:
    """`module` 이 `target`(절대 이름, 예: `api.query`)을 어떤 모양으로든 import 하는가."""
    leaf = target.rsplit(".", 1)[-1]
    for base, names in _imports(module):
        if base == target or base.startswith(target + "."):
            return True
        if base == target.rsplit(".", 1)[0] and leaf in names:
            return True
    return False


@pytest.mark.parametrize("module", WRITE_MODULES)
def test_write_modules_do_not_import_the_read_facade(module):
    assert not _reaches(module, "api.query"), f"api.{module} 가 api.query 를 import 한다"


#: (쓰기 모듈, 읽기 쪽이 그 모듈에서 가져오면 안 되는 사설 이름들)
_PRIVATE_HELPERS = (
    ("api.fragment", {"_split_subject", "_norm_subject_path"}),
    ("api.docstore", {"_norm_content_time"}),
)


@pytest.mark.parametrize("module", ("decide_read", "diagnose", "cli_gate", "docextract"))
def test_read_modules_do_not_take_write_module_private_helpers(module):
    bad = [(base, sorted(set(names) & private))
           for base, names in _imports(module)
           for home, private in _PRIVATE_HELPERS
           if base == home and set(names) & private]
    assert not bad, f"api.{module} 가 쓰기 모듈의 사설 도우미를 가져온다: {bad}"


@pytest.mark.parametrize("module", ("decide_read", "diagnose", "cli_gate"))
def test_read_modules_do_not_import_fragment_at_all(module):
    assert not _reaches(module, "api.fragment"), f"api.{module} 가 api.fragment 를 import 한다"


def test_the_helpers_live_in_the_leaf_and_the_old_names_alias_them():
    assert fragment._split_subject is node_schema.split_subject
    assert fragment._norm_subject_path is node_schema.normalize_subject_path
    assert docstore._norm_content_time is node_schema.normalize_time
    assert decide_read.split_subject is node_schema.split_subject
    assert decide_read.normalize_subject_path is node_schema.normalize_subject_path
    assert diagnose.normalize_subject_path is node_schema.normalize_subject_path
    assert diagnose.normalize_time is node_schema.normalize_time


def test_the_names_the_write_side_reads_are_the_lower_layers_objects():
    assert fragment._props_to_obj is querybase._props_to_obj is query._props_to_obj
    assert fragment.domain_names is querybase.domain_names is query.domain_names
    assert query.domain_names_plan is querybase.domain_names_plan
    for name in ("_query_fingerprint", "cursor_decode", "cursor_encode"):
        assert getattr(docstore, name) is getattr(retrieval, name) is getattr(query, name), name
        assert getattr(docevidence, name) is getattr(retrieval, name), name


@pytest.mark.parametrize(("raw", "want"), [
    ("D:/tmp/x.py", ("", "D:/tmp/x.py")),
    ("VINC:api/sync.py", ("vinc", "api/sync.py")),
    ("api/sync.py", ("", "api/sync.py")),
])
def test_split_subject_keeps_its_contract(raw, want):
    assert node_schema.split_subject(raw) == want


def test_normalize_subject_path_keeps_its_contract():
    norm = node_schema.normalize_subject_path
    assert norm("VINC:./api\\sync.py") == "vinc:api/sync.py"
    assert norm("/api/sync.py") == "api/sync.py"
    assert norm("D:/tmp/x.py") == "D:/tmp/x.py"
    assert norm(None) == "" and norm("") == ""


#: 새 인터프리터에서 이 모듈을 맨 처음 import 한다. 순환이 있으면 부분 초기화된 모듈을 읽다 ImportError 가 난다.
_FIRST_IMPORTS = ("api.server", "api.cli", "api.fragment", "api.docstore", "api.query",
                  "api.docevidence", "api.docextract", "api.decide_read", "api.diagnose", "api.cli_gate")


@pytest.mark.parametrize("module", _FIRST_IMPORTS)
def test_module_imports_first_in_a_fresh_interpreter(module):
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONHOME", "PYTHONPATH")}
    env["PYTHONPATH"] = str(ROOT)
    done = subprocess.run([sys.executable, "-c", f"import {module}"], cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL)
    assert done.returncode == 0, done.stderr[-2000:]
