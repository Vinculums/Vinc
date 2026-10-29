# -*- coding: utf-8 -*-
"""토큰의 값은 그래프에 있고, 스냅샷 하나가 소비처의 토큰 파일 넷을 정한다.

지키는 것:
- 그래프 응답(기록해 둔 픽스처)을 `pull_tokens.assemble` 에 넣으면 스냅샷 모양이 나오고,
  그 스냅샷을 `seed_tokens.plan` 으로 되돌리면 같은 노드가 나온다(복구 경로가 산다).
- 지금 커밋된 스냅샷도 plan → assemble 로 그대로 돌아온다.
- 그래프가 이상하면(겹치는 키·없는 기본 토큰·모르는 묶음) pull 이 스냅샷을 쓰지 않고 멈춘다.
- 커밋된 앱 사본이 스냅샷에서 다시 만든 것과 같다(각인 줄 제외).
근거: [[decision:design-token-values-live-in-the-graph-and-the-hub-directory-is-gone-2026-10-29]]
"""
from __future__ import annotations

import copy
import json
import pathlib
import re
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
DESIGN = ROOT / "scripts" / "design"
sys.path.insert(0, str(DESIGN))
import build_tokens  # noqa: E402
import pull_tokens  # noqa: E402
import seed_tokens  # noqa: E402

FIXTURE = json.loads((ROOT / "tests" / "design" / "fixtures" / "design_tokens_graph.json")
                     .read_text(encoding="utf-8"))
STAMP = re.compile(r"^[ \t]*(?://|/\*)[ \t]*sync-sha256:.*\r?\n", re.MULTILINE)


def _call(fixture: dict):
    def call(name: str, args: dict) -> dict:
        if name == "vinc_neighbors":
            return {"executed": True, "found": True, "neighbors": fixture["neighbors"]}
        if name == "vinc_node":
            nid = args["node_id"]
            node = fixture["root"] if nid == pull_tokens.ROOT_NODE else fixture["nodes"].get(nid)
            return {"executed": True, "found": node is not None, "node": node}
        raise AssertionError(name)
    return call


class PullAndSeedAreInverse(unittest.TestCase):
    def test_the_recorded_graph_assembles_and_seeds_back(self):
        snap = json.loads(pull_tokens.pull(_call(FIXTURE)))
        plan = seed_tokens.plan(snap)
        got = {n["id"]: n["props"] for n in plan["nodes"]}
        want = {i: n["props"] for i, n in FIXTURE["nodes"].items()}
        self.assertEqual(got, want)

    def test_only_token_ids_are_read(self):
        # 같은 주제에는 토큰 아닌 노드도 산다(concept:design-hub). 그것은 읽지 않는다.
        ids = pull_tokens.token_ids(_call(FIXTURE))
        self.assertEqual(len(ids), len(FIXTURE["nodes"]))
        self.assertTrue(all(i.startswith("concept:token.") for i in ids))

    def test_the_committed_snapshot_round_trips(self):
        snap = build_tokens.load()
        plan = seed_tokens.plan(snap)
        back = pull_tokens.assemble({"props": plan["root_props"]}, plan["nodes"])
        self.assertEqual(pull_tokens.render(back),
                         build_tokens.SNAPSHOT.read_text(encoding="utf-8"))


class PullRefusesABrokenGraph(unittest.TestCase):
    def _assemble(self, mutate):
        fx = copy.deepcopy(FIXTURE)
        mutate(fx)
        return pull_tokens.assemble(fx["root"], list(fx["nodes"].values()))

    def test_two_nodes_with_one_key(self):
        def dup(fx):
            n = copy.deepcopy(fx["nodes"]["concept:token.radius.sm"])
            n["id"] = "concept:token.radius.sm-copy"
            n["props"]["order"] = 99
            fx["nodes"][n["id"]] = n
        with self.assertRaises(pull_tokens.PullError):
            self._assemble(dup)

    def test_a_semantic_token_pointing_at_no_primitive(self):
        def bad(fx):
            fx["nodes"]["concept:token.semantic.card"]["props"]["dark"] = "{d_bg9}"
        with self.assertRaises(pull_tokens.PullError):
            self._assemble(bad)

    def test_a_group_the_root_does_not_list(self):
        def stray(fx):
            fx["nodes"]["concept:token.fs.sm"]["props"]["group"] = "fontSize"
        with self.assertRaises(pull_tokens.PullError):
            self._assemble(stray)


class TheGeneratorKeepsItsFormat(unittest.TestCase):
    def test_hsl_channels_match_the_old_engine(self):
        # 옛 엔진이 낸 값과 같은 반올림: 이 값들이 바뀌면 모든 화면 색이 1% 씩 흔들린다.
        for hexv, want in {"#F2F2F0": "60 7% 95%", "#4A4F8C": "235 31% 42%",
                           "#0A0A0A": "0 0% 4%", "#B9BEF0": "235 65% 83%",
                           "#DC2828": "0 72% 51%", "#4A4230": "42 21% 24%"}.items():
            self.assertEqual(build_tokens.hsl_channels(hexv), want, hexv)

    def test_the_app_copies_are_what_the_snapshot_makes(self):
        out = build_tokens.outputs(build_tokens.load())
        for name in ("tokens.desktop.css", "tokens.web.css", "theme.ts"):
            copy_text = (ROOT / "app" / "src" / name).read_text(encoding="utf-8")
            self.assertEqual(STAMP.sub("", copy_text), out[name], name)

    def test_motion_sits_before_shape_in_every_css(self):
        out = build_tokens.outputs(build_tokens.load())
        for name in ("site-tokens.css", "tokens.desktop.css", "tokens.web.css"):
            css = out[name]
            self.assertLess(css.index("--ease-out"), css.index("/* Shape */"), name)


if __name__ == "__main__":
    unittest.main()
