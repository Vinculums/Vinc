# -*- coding: utf-8 -*-
"""문서 미러 id 규약(`concept:doc.<doc_id>`)의 세 이름이 옛 식과 같은 문자열을 낸다.

잠그는 것:
- `DOC_MIRROR_PREFIX` 는 `concept:doc.` 바이트 그대로다. 앱의 `DOC_MIRROR`(share-docs.ts)와 짝이다.
- `mirror_id(d)` 는 옛 `"concept:doc." + d` 와 `f"concept:doc.{d}"` 가 낸 문자열과 같다.
- `doc_id_of(n)` 은 옛 `n[len("concept:doc."):]` 와 같다. 접두가 없거나 접두보다 짧은 입력도 같다
  (확인하지 않고 자르는 것이 옛 식의 동작이다).
"""
from __future__ import annotations

import pytest

from store import pkns
from store.querybase import DOC_MIRROR_PREFIX, doc_id_of, mirror_id

DOC_IDS = ["", "a", "note.md", "notes/2026/plan.md", "회의록.md", "a.b.c", "x" * 200, "concept:doc.nested",
           "graph.md~0123456789abcdef"]
#: 접두로 시작하는 것, 안 하는 것, 접두보다 짧은 것, 네임스페이싱 키.
NODE_IDS = ["concept:doc.note.md", "concept:doc.", "concept:doc.회의록.md", "concept:doc.concept:doc.x",
            "concept:topic", "record:x", "doc:note.md", "", "concept:doc", "concept:do", "Concept:doc.x",
            "user:abc" + pkns.SEP + "concept:doc.note.md", "domain:vinc/a"]


def test_prefix_is_the_stored_convention_byte_for_byte():
    assert DOC_MIRROR_PREFIX == "concept:doc."
    assert len(DOC_MIRROR_PREFIX) == 12


@pytest.mark.parametrize("doc_id", DOC_IDS)
def test_mirror_id_equals_the_old_concatenation_and_fstring(doc_id):
    assert mirror_id(doc_id) == "concept:doc." + doc_id
    assert mirror_id(doc_id) == f"concept:doc.{doc_id}"


@pytest.mark.parametrize("node_id", NODE_IDS)
def test_doc_id_of_equals_the_old_slice_for_every_input(node_id):
    assert doc_id_of(node_id) == node_id[len("concept:doc."):]


@pytest.mark.parametrize("node_id", NODE_IDS)
def test_doc_id_of_on_ids_without_the_prefix_is_the_old_blind_slice(node_id):
    if node_id.startswith("concept:doc."):
        return
    # 접두를 확인하지 않는다: 옛 슬라이스가 낸 꼬리를 그대로 낸다(빈 문자열일 수도 있다).
    assert doc_id_of(node_id) == node_id[12:]


@pytest.mark.parametrize("doc_id", DOC_IDS)
def test_round_trip(doc_id):
    assert doc_id_of(mirror_id(doc_id)) == doc_id
    assert mirror_id(doc_id).startswith(DOC_MIRROR_PREFIX)


def test_mirror_id_takes_strings_only_as_the_old_plus_did():
    with pytest.raises(TypeError):
        mirror_id(5)
    with pytest.raises(TypeError):
        "concept:doc." + 5


def test_namespaced_key_wraps_the_same_logical_id():
    assert pkns.strip(pkns.key("user:abc", mirror_id("n.md"))) == mirror_id("n.md")
    assert pkns.key("user:abc", DOC_MIRROR_PREFIX) == pkns.key("user:abc", "concept:doc.")
