# -*- coding: utf-8 -*-
"""Figma 드리프트 대조 — 있는 것을 없다고도, 다른 것을 같다고도 하면 안 된다.

왜 이 파일이 있나: 이 대조는 **거짓 초록이 제일 위험하다**. 빨갛게 뜨면 사람이 보지만,
조용히 통과하면 "Figma 와 코드가 맞다"는 근거 없는 안심만 남는다 — 2026-09-05 이전이
정확히 그 상태였다(대조가 아예 없었고, 다크 `primary` 두 개가 몇 주 동안 갈려 있었다).

그래서 여기서 지키는 것은 셋이다: 접두 없는 `note` 산문이 토큰으로 새지 않을 것,
같은 변수의 두 모드가 생성 작업 두 건으로 갈라지지 않을 것, 값이 다르면 반드시 잡을 것.
"""
from __future__ import annotations

import importlib.util
import pathlib
import unittest

_PATH = pathlib.Path(__file__).resolve().parent.parent.parent / "scripts" / "design" / "figma_diff.py"
_spec = importlib.util.spec_from_file_location("figma_diff", _PATH)
fd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fd)


TOKENS = {
    "primitive": {"indigo_700": {"$value": "#3A4070"}, "peri_400": {"$value": "#8F96D9"}},
    "semantic": {
        "light": {"primary": "{indigo_700}", "focus": "{indigo_700}"},
        "dark": {"primary": "{peri_400}", "focus": "{peri_400}"},
    },
    "radius": {"DEFAULT": "0.875rem"}, "space": {"1": "0.25rem"},
    "fs": {"xl": "clamp(1.5rem, 1.1rem + 1.6vw, 2.25rem)"},
    "shadow": {"elev-0": "none", "elev-1": "0 1px 2px hsl(230 20% 6% / 0.07)"},
    "shadowDark": {"elev-1": "0 1px 2px hsl(0 0% 0% / 0.35)"},
    "motion": {"note": "산문 한 문단", "ease-out": "cubic-bezier(.22, 1, .36, 1)", "dur-out": ".24s"},
}


def _live(**over):
    live = {
        "variables": {
            "Primitive": {"Value": {"indigo_700": "#3A4070", "peri_400": "#8F96D9"}},
            "Semantic": {"Light": {"primary": "{indigo_700}", "focus": "{indigo_700}"},
                         "Dark": {"primary": "{peri_400}", "focus": "{peri_400}"}},
            "Scale": {"Value": {"radius/default": 14, "space/1": 4, "fs/xl": 36}},
            "Motion": {"Value": {"ease-out": "cubic-bezier(.22, 1, .36, 1)", "dur-out": 240}},
        },
        "effectStyles": [
            {"name": "shadow/elev-1", "effects": [{"type": "DROP_SHADOW", "radius": 2, "spread": 0,
             "offset": {"x": 0, "y": 1}, "color": {"r": 0.048, "g": 0.052, "b": 0.072, "a": 0.07}}]},
            {"name": "shadow/dark-elev-1", "effects": [{"type": "DROP_SHADOW", "radius": 2, "spread": 0,
             "offset": {"x": 0, "y": 1}, "color": {"r": 0, "g": 0, "b": 0, "a": 0.35}}]},
        ],
    }
    for path, val in over.items():
        coll, mode, name = path.split("__")
        live["variables"][coll][mode][name] = val
    return live


def _ops(res):
    return sum(len(v) for v in res["ops"].values())


class MatchingFileIsClean(unittest.TestCase):
    def test_no_ops_when_figma_already_matches(self):
        self.assertEqual(_ops(fd.compare(fd.expected(TOKENS), _live())), 0)

    def test_clamp_compares_against_its_upper_bound(self):
        # Figma 는 유동 크기를 못 담는다. 상한(2.25rem = 36)이 대조 지점이고,
        # 이걸 틀리면 fs/xl 이 영원히 드리프트로 뜬다.
        self.assertEqual(fd.expected(TOKENS)["scale"]["fs/xl"], 36)

    def test_prose_note_is_not_a_token(self):
        # motion·viewScene·domainTint 는 `$note` 가 아니라 `note` 를 쓴다. 안 걸러내면
        # 산문 한 문단이 Figma 에 STRING 변수로 생긴다(실제로 첫 실행에서 그랬다).
        self.assertNotIn("note", fd.expected(TOKENS)["motion"])


class DriftIsCaught(unittest.TestCase):
    def test_a_changed_alias_is_reported_as_a_fix(self):
        res = fd.compare(fd.expected(TOKENS), _live(Semantic__Dark__primary="{peri_200}"))
        self.assertEqual([(o["name"], o["to"]) for o in res["ops"]["set_value"]],
                         [("primary", "{peri_400}")])

    def test_a_missing_variable_is_one_create_carrying_both_modes(self):
        live = _live()
        del live["variables"]["Semantic"]["Light"]["focus"]
        del live["variables"]["Semantic"]["Dark"]["focus"]
        creates = fd.compare(fd.expected(TOKENS), live)["ops"]["create_variable"]
        self.assertEqual(len(creates), 1, "변수는 하나인데 모드마다 생성이 잡히면 안 된다")
        self.assertEqual(creates[0]["values"], {"Light": "{indigo_700}", "Dark": "{peri_400}"})

    def test_a_missing_dark_shadow_is_caught(self):
        live = _live()
        live["effectStyles"] = [s for s in live["effectStyles"] if s["name"] != "shadow/dark-elev-1"]
        res = fd.compare(fd.expected(TOKENS), live)
        self.assertEqual([o["name"] for o in res["ops"]["create_effect_style"]], ["shadow/dark-elev-1"])

    def test_shadow_none_is_not_expected_as_a_style(self):
        # elev-0 은 `none` 이라 효과 0개짜리 스타일이 된다. 만들 수 없으니 기대하지도 않는다.
        self.assertNotIn("shadow/elev-0", fd.expected(TOKENS)["effects"])

    def test_figma_only_keys_are_reported_but_are_not_ops(self):
        res = fd.compare(fd.expected(TOKENS), _live(Scale__Value__hairline_w=3))
        self.assertEqual(_ops(res), 0)
        self.assertEqual(res["info"]["figma_only"], ["Scale/Value/hairline_w = 3"])


if __name__ == "__main__":
    unittest.main()
