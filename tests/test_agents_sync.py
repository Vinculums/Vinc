# -*- coding: utf-8 -*-
"""역할 껍데기 생성기 — 결정론과 리포 정합.

왜 이 파일이 있나: `--check` 는 "스냅샷에서 재생성한 것과 파일이 바이트가 같은가"만
본다. 그 검사는 `render_shell` 이 **결정론**일 때만 뜻이 있다. 같은 입력에서 두 번
다른 바이트가 나오면 게이트는 손대지 않은 파일도 빨갛게 만들고, 곧 꺼진다.

`build_payload` 를 여기서 직접 부르는 이유는 `--pull` 이 네트워크와 토큰을 요구하기
때문이다. 형태를 잡는 부분만 순수 함수로 떼어 두면 오프라인에서 검사할 수 있다.
"""
from __future__ import annotations

import unittest

from scripts.agents_sync import (RUNG_TO_MODEL, _manifest_docs, build_payload,
                                 dangling_reads, main, read_snapshot, render_shell)

_NODE = {
    "id": "concept:agent-example",
    "label": "Agent role: example",
    "domain": "vinc/agent",
    "tags": ["role", "agent"],
    "props": {
        "tier": "execute",
        "rung": "strong",
        "mission": "Does one thing and stops.",
        "reads": ["vinc.canon.repo-workflow", "vinc.spec.dataplane"],
        "gates": ["scripts/gate_forbidden.py"],
        "never": ["merge"],
        "person_only": ["merge"],
    },
}


class Payload(unittest.TestCase):
    def test_role_ids_only(self):
        raw = [_NODE, {"id": "record:not-a-role", "props": {}}]
        got = build_payload(raw)
        self.assertEqual([r["id"] for r in got["roles"]], ["concept:agent-example"])
        self.assertEqual(got["roles"][0]["name"], "agent-example")

    def test_reads_keep_their_order(self):
        """목록은 정렬하지 않는다 — `reads` 는 '이 순서로 읽어라'라서 정렬하면 지시가
        사라진다. dict 키만 정렬한다."""
        got = build_payload([_NODE])["roles"][0]
        self.assertEqual(got["props"]["reads"],
                         ["vinc.canon.repo-workflow", "vinc.spec.dataplane"])
        self.assertEqual(list(got["props"]), sorted(got["props"]))

    def test_payload_is_stable_under_input_order(self):
        second = dict(_NODE, id="concept:agent-another")
        a = build_payload([_NODE, second])
        b = build_payload([second, _NODE])
        self.assertEqual(a, b)


class Shell(unittest.TestCase):
    def test_render_is_deterministic(self):
        role = build_payload([_NODE])["roles"][0]
        self.assertEqual(render_shell(role), render_shell(role))

    def test_frontmatter_carries_name_description_and_model(self):
        role = build_payload([_NODE])["roles"][0]
        head = render_shell(role).splitlines()
        self.assertEqual(head[0], "---")
        self.assertEqual(head[1], "name: agent-example")
        self.assertTrue(head[2].startswith("description: Does one thing"))
        self.assertEqual(head[3], f"model: {RUNG_TO_MODEL['strong']}")

    def test_description_is_one_line(self):
        """프런트매터의 description 은 한 줄이어야 한다. 임무 문장에 줄바꿈이 들어오면
        YAML 이 깨지므로 render 가 접는다."""
        node = dict(_NODE, props=dict(_NODE["props"], mission="two\nlines here"))
        role = build_payload([node])["roles"][0]
        self.assertIn("description: two lines here", render_shell(role))

    def test_every_rung_maps_to_a_model(self):
        for rung in ("strongest", "strong", "fast", "cheapest"):
            self.assertTrue(RUNG_TO_MODEL.get(rung), rung)


class RepoIsClean(unittest.TestCase):
    def test_committed_shells_match_the_committed_snapshot(self):
        """CI 와 같은 검사를 로컬에서도 돌린다 — 게이트를 푸시 후에 처음 만나면 고치는
        자리가 이미 멀다."""
        self.assertEqual(main(["--check"]), 0, "역할 껍데기가 스냅샷과 어긋난다(출력 참조)")


class Reads(unittest.TestCase):
    """`reads` 는 이 설계의 핵심이면서 지금껏 아무도 검사하지 않는 문자열이었다.

    표기가 두 형식으로 섞여 있는 것이 그것을 가렸다. P5 코퍼스는 점 표기 자체가 store
    키이고(`vinc.spec.dataplane`), 2026-08-19 이후 문서는 해시다. 해시로 통일하는 길은
    없다 — 앞쪽에는 해시가 존재하지 않는다. 그래서 두 형식 모두 존재를 검사한다.
    """

    def test_a_missing_document_is_reported_with_its_role(self):
        snap = {"roles": [{"name": "agent-example",
                           "props": {"reads": ["vinc.canon.repo-workflow", "vinc.gone.away"]}}]}
        self.assertEqual(dangling_reads(snap, {"vinc.canon.repo-workflow"}),
                         [("agent-example", "vinc.gone.away")])

    def test_both_id_shapes_pass_when_present(self):
        snap = {"roles": [{"name": "agent-example",
                           "props": {"reads": ["vinc.spec.dataplane", "d8fa9efa40f1a62c7"]}}]}
        self.assertEqual(dangling_reads(snap, {"vinc.spec.dataplane", "d8fa9efa40f1a62c7"}), [])

    def test_a_role_with_no_reads_is_not_a_finding(self):
        self.assertEqual(dangling_reads({"roles": [{"name": "a", "props": {}}]}, set()), [])

    def test_every_committed_read_resolves_against_the_manifest(self):
        """리포 정합. 매니페스트가 낡으면 여기서 먼저 빨개진다 — 그게 맞다.
        `scripts/refresh_graph_refs.py` 를 돌려 같은 커밋에 넣으라는 신호다."""
        docs = _manifest_docs()
        self.assertIsNotNone(docs, "인용 매니페스트가 없다")
        self.assertEqual(dangling_reads(read_snapshot(), docs), [])
