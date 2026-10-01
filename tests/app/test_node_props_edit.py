"""노드 속성(props) 편집 — 소스 잠금(2026-09-26).

동작(형식 변환 · 검사 · 전체 교체)은 `app/dev/props-edit-check.ts` 가 브라우저 없이
검사하고, 저장 · 충돌 · Esc 는 헤드리스 브라우저로 확인했다. 여기서는 그 동작이 기대는
구조가 조용히 풀리지 않게 붙잡는다:

- 서버는 props 를 통째로 교체한다. 편집 결과 **전체**를 보내야 한다.
- 필드 이력이 없다. 저장 직전 다시 읽어 판(rev)이 같을 때만 써야 남의 새 편집을 안 덮는다.
- owner 를 실으면 로컬에서 교차 소유 가드가 켜져 견본 · 팩 노드가 건너뛰어진다.
- props 가 저장되지 않는 종(Entity 캐리어)에 편집 단추를 내면 값이 조용히 사라진다.

근거: [[decision:node-props-are-edited-in-the-document-pane-as-a-whole-and-checked-against-the-read-revision-2026-09-26]]
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "app"
VIEW = (APP / "src" / "surfaces" / "view.ts").read_text(encoding="utf-8")
EDIT = (APP / "src" / "props-edit.ts").read_text(encoding="utf-8")
COPY = (APP / "src" / "copy.ts").read_text(encoding="utf-8")
API = (APP / "src" / "api.ts").read_text(encoding="utf-8")


def _fn(src: str, name: str, nxt: str) -> str:
    a = src.index(f"function {name}(")
    return src[a:src.index(nxt, a)]


EDITOR = _fn(VIEW, "openPropsEditor", "function propsDl(")


class Save(unittest.TestCase):
    def test_rereads_the_node_bypassing_the_cache_before_writing(self):
        save = EDITOR[EDITOR.index("async function save()"):]
        read = save.index("api.nodeContext(cfg.owner, nd.id")
        write = save.index("api.ingestFragment(")
        self.assertLess(read, write)
        self.assertNotIn("nodeContextOnce(", save)

    def test_writes_only_when_the_revision_is_unchanged(self):
        self.assertIn("(fresh.rev ?? 0) !== (meta.rev ?? 0)", EDITOR)
        self.assertIn("V.propsConflict", EDITOR)

    def test_sends_the_whole_set_with_id_and_kind_and_no_owner(self):
        call = re.search(r"api\.ingestFragment\(\{(.*?)\}\);", EDITOR, re.S)
        assert call, "ingestFragment 호출이 없다"
        body = call.group(1)
        self.assertIn("props: next", body)
        self.assertIn("type: meta.kind", body)
        self.assertNotIn("owner", body)

    def test_reads_merged_instead_of_trusting_the_call(self):
        self.assertIn("res?.merged?.nodes?.matched", EDITOR)
        self.assertIn("res?.warnings", EDITOR)

    def test_redraws_from_a_fresh_read_and_refreshes_the_agent_list(self):
        self.assertIn("ctxCache = null;", EDITOR)
        self.assertIn("refreshLnb({ quiet: true })", EDITOR)


class Editor(unittest.TestCase):
    def test_escape_folds_only_the_editor(self):
        self.assertIn('e.key === "Escape") { e.stopPropagation(); cancel(); }', EDITOR)

    def test_listeners_are_released_with_the_box(self):
        self.assertIn("box.replaceWith(fresh)", EDITOR)

    def test_detail_passes_kind_and_revision(self):
        self.assertIn("drawNodeProps(nd, res.props, { kind: res.node_kind, rev: res.rev })", VIEW)
        self.assertIn("node_kind?: string; rev?: number;", API)


class Kinds(unittest.TestCase):
    def test_only_kinds_that_keep_props_are_editable(self):
        m = re.search(r"PROPS_EDITABLE_KINDS = \[([^\]]*)\]", EDIT)
        assert m
        kinds = sorted(re.findall(r'"(\w+)"', m.group(1)))
        self.assertEqual(kinds, ["concept", "decision", "record"])

    def test_module_is_import_free(self):
        # app/dev/props-edit-check.ts 가 node 로 직접 돌리는 성립 조건.
        self.assertIsNone(re.search(r"^import ", EDIT, re.M))
        self.assertTrue((APP / "dev" / "props-edit-check.ts").exists())


class Copy(unittest.TestCase):
    KEYS = ("propsEdit", "propsEditTip", "propsEmpty", "propsAdd", "propsSave", "propsCancel",
            "propsSaving", "propsKeyPh", "propsListHint", "propsKindLabel", "propsKinds",
            "propsYes", "propsNo", "propsRemove", "propsErr", "propsNoChange",
            "propsConflict", "propsReload", "propsFail")

    def test_both_decks_carry_every_key(self):
        for k in self.KEYS:
            self.assertEqual(len(re.findall(rf"\b{k}:", COPY)), 2, k)

    def test_no_dash_on_screen(self):
        # 화면 문구에 대시 금지(§1). 두 덱 각각 propsEdit 부터 propsFail 줄 끝까지.
        for a in (m.start() for m in re.finditer(r"propsEdit:", COPY)):
            b = COPY.index("\n", COPY.index("propsFail:", a))
            chunk = COPY[a:b]
            self.assertNotIn("—", chunk)
            self.assertNotIn("–", chunk)


if __name__ == "__main__":
    unittest.main()
