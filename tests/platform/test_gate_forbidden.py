# -*- coding: utf-8 -*-
"""gate_forbidden 게이트 단위 테스트 (stdlib unittest).

게이트가 no-op 이 아님을 잠근다: F1/F2/F7 검출 + F7 경계 구분
(weg-memory 금지 / weg-9000-memory 허용).
"""
import contextlib
import hashlib
import io
import json
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))
import gate_forbidden as g  # noqa: E402


def _scan_text(name: str, content: str):
    # name 은 루트 기준 상대경로다 — F9 처럼 경로로 관할이 갈리는 규칙이 있어서
    # 파일이 실제로 그 자리에 놓여야 한다(`app/src/...`).
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return g.scan(Path(d))


class TestFrozenExternalData(unittest.TestCase):
    def test_exact_catalog_bytes_only_exempt_the_external_name(self):
        root = Path(__file__).resolve().parents[2]
        for name, sha in g._EXTERNAL_F8_SHA256.items():
            raw = (root / name).read_bytes()
            self.assertEqual(hashlib.sha256(raw).hexdigest(), sha)
            with tempfile.TemporaryDirectory() as d:
                p = Path(d) / name
                p.parent.mkdir(parents=True)
                p.write_bytes(raw)
                self.assertEqual(g.scan(Path(d)), [])
                p.write_bytes(raw + b'\n{"surface":"atlas"}\n')
                self.assertTrue(any(v.gate == "F8" for v in g.scan(Path(d))))

    def test_same_bytes_outside_pinned_path_are_checked(self):
        raw = (Path(__file__).resolve().parents[2] / next(iter(g._EXTERNAL_F8_SHA256))).read_text(encoding="utf-8")
        self.assertTrue(any(v.gate == "F8" for v in _scan_text("app/src/copied.json", raw)))

    def test_other_rules_still_apply_to_an_exact_external_file(self):
        raw = b'atlas usage_record = 1'
        name = next(iter(g._EXTERNAL_F8_SHA256))
        with patch.dict(g._EXTERNAL_F8_SHA256, {name: hashlib.sha256(raw).hexdigest()}):
            findings = _scan_text(name, raw.decode())
        self.assertEqual([v.gate for v in findings], ["F1"])


class TestGate(unittest.TestCase):
    def test_f1_billing_metering(self):
        v = _scan_text("a.py", "usage_record = 1\ntoken_margin = 2\nUsageRecord()\n")
        gates = {x.gate for x in v}
        self.assertIn("F1", gates)
        self.assertEqual(3, len(v))

    def test_f1_allows_open_domain_billing_word(self):
        # "billing" 은 오픈도메인 데이터·QueryPlan.kind 는 정본 코드 — F1 아님(§1)
        v = _scan_text("a.py", 'domain = "billing"\nplan.kind == "recall"\ntags = ["quota"]\n')
        self.assertEqual(0, len(v))

    def test_f2_learning_copy(self):
        v = _scan_text("a.ts", '"나의 기억"\n"학습을 자산으로"\n"배움"\n')
        self.assertTrue(all(x.gate == "F2" for x in v))
        self.assertEqual(3, len(v))

    def test_f2_allows_learner_identifier(self):
        # 코드 식별자 learner_* 는 D6 전까지 허용
        v = _scan_text("a.py", "learner_pattern = load()\n")
        self.assertEqual(0, len(v))

    def test_f7_old_name_forbidden(self):
        v = _scan_text("a.py", 'path = "weg-memory/store"\n')
        self.assertEqual(1, len(v))
        self.assertEqual("F7", v[0].gate)

    def test_f7_code_provenance_allowed(self):
        # weg-9000-memory(코드 출처)는 허용 — weg-memory 부분문자열 아님
        v = _scan_text("a.py", "# weg-9000-memory 에서 추출한 엔진\n")
        self.assertEqual(0, len(v))

    def test_clean_passes(self):
        v = _scan_text("a.py", "def ingest(): return 'vinc'\n")
        self.assertEqual(0, len(v))


class TestF9KoreanCopy(unittest.TestCase):
    """F9 — 한국어 사용자 가시 어휘(vinc.spec.ui-copy-ko §4)."""

    def test_graphmd_template_is_in_scope(self):
        # GRAPH.md 한국어 템플릿은 사용자에게 파일로 나가고 사이트가 미러한다 — app/src 와
        # 같은 관할이다. 2026-09-05 까지 범위 밖이어서 2인칭이 템플릿에 살아 있었다.
        v = _scan_text("api/graphmd.py", 'x = "의미를 판단하는 것은 당신의 일입니다."' + chr(10))
        self.assertEqual(["F9"], [x.gate for x in v])

    def test_forbidden_word_in_app_src_string(self):
        v = _scan_text("app/src/copy.ts", 'note: "당신에게 남습니다",\n')
        self.assertEqual(1, len(v))
        self.assertEqual("F9", v[0].gate)

    def test_all_korean_words_are_wired(self):
        words = ("말뭉치", "코퍼스", "데이터", "지도",
                 "채굴", "표면", "레일", "관문", "당신", "귀하")
        src = "".join(f'x{i}: "{w}",\n' for i, w in enumerate(words))
        v = _scan_text("app/src/copy.ts", src)
        self.assertEqual(len(words), len(v))
        self.assertEqual({"F9"}, {x.gate for x in v})

    def test_retired_nouns_fire_in_both_scripts(self):
        """2026-09-12 발주자 지시 둘. 한 낱말의 은퇴가 한쪽 언어에서만 걸리면
        나머지 절반이 조용히 남는다 — 그래서 같은 F9 안에 둘 다 있다.
        `map` 은 GRAPH.md 영어 템플릿에도 걸려야 한다(사용자에게 파일로 나간다)."""
        for rel, src in (
            ("app/src/copy.ts", 'x: "The map couldn\'t load.",\n'),
            ("app/src/copy.ts", 'x: "Your maps are safe.",\n'),
            ("api/graphmd.py", 'x = "a knowledge map"\n'),
            ("app/src/copy.ts", 'x: "지도를 불러오지 못했어요",\n'),
            ("api/graphmd.py", 'x = "데이터 유실"\n'),
        ):
            with self.subTest(rel=rel, src=src):
                v = _scan_text(rel, src)
                self.assertEqual(1, len(v), v)
                self.assertEqual("F9", v[0].gate)

    def test_database_compound_and_capital_map_are_carved_out(self):
        """부정 전방탐색이 실제로 무엇을 빼는가. `데이터베이스` 는 합성어라 남고
        (api/graphmd.py 의 GRAPH.md 한국어 템플릿이 이 낱말을 실제로 쓴다),
        `개인정보` 와 대체어 `정보` 자체는 애초에 대상이 아니다."""
        for rel, src in (
            ("api/graphmd.py", 'x = "데이터베이스 관련"\n'),
            ("app/src/copy.ts", 'x: "개인정보 처리방침",\n'),
            ("app/src/copy.ts", 'x: "정보는 그대로 있어요",\n'),
            ("app/src/copy.ts", "const m = new Map();\n"),
            ("app/src/copy.ts", 'x: "our roadmap",\n'),
        ):
            with self.subTest(rel=rel, src=src):
                self.assertEqual([], _scan_text(rel, src))

    def test_english_map_is_scoped_to_the_copy_deck_not_all_of_app_src(self):
        """실측: `app/src/` 전체에 걸면 `.map(` 배열 호출 170건이 위반이 된다
        (view.ts 40 · view2d.ts 23 …). 그 상태의 게이트는 잡음으로 학습되고,
        그러면 진짜 금지어가 지나간다. 영어 규칙만 덱과 템플릿으로 좁힌 이유다."""
        self.assertEqual([], _scan_text("app/src/surfaces/view.ts",
                                        "const ys = xs.map((x) => x);\n"))
        self.assertEqual([], _scan_text("app/src/lnb.ts",
                                        'const s = "the map";\n'))
        self.assertEqual(1, len(_scan_text("app/src/copy.ts",
                                           'x: "the map",\n')))

    def test_comment_in_app_src_is_not_a_violation(self):
        # 이 리포의 주석 언어는 한국어다(CLAUDE.md). "제품 표면"·"레일은 좁다" 는
        # 코드를 설명하는 말이지 화면에 나가는 카피가 아니다.
        v = _scan_text("app/src/view.ts",
                       "// 이 표면의 레일은 좁다\n/* 관문 윤곽선 */\n")
        self.assertEqual(0, len(v))

    def test_scope_is_app_src_only(self):
        # 문서·스크립트·테스트의 같은 낱말은 F9 관할 밖이다.
        v = _scan_text("docs/note.md", "표면과 레일과 관문\n")
        self.assertEqual(0, len(v))

    def test_ontology_is_held_not_forbidden(self):
        # §9.2 — 영어 쪽 제품 명사 결정 전까지 보류. 금지 목록에 없어야 한다.
        v = _scan_text("app/src/copy.ts", 'x: "온톨로지",\n')
        self.assertEqual(0, len(v))


class TestExcludeIsRootAnchored(unittest.TestCase):
    """세션 부산물을 빼되 **출하 콘텐츠는 빼지 않는다**.

    `_EXCLUDE_DIRS` 는 `set(parts) & ...` 라 경로 어디에 있든 그 이름을 뺀다. 거기에
    `work` 를 넣으면 gitignore 된 `work/` 뿐 아니라 **추적되고 출하되는**
    `starter-packs/work/` 까지 빠진다(이 리포에서 123파일, 게이트 스캔 대상의 20%).
    그래서 루트 고정 제외를 따로 둔다. 이 테스트가 그 구분을 잠근다."""

    def _seen(self, *names):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            for n in names:
                p = root / n
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text("hello", encoding="utf-8")
            return {rel for _, rel in g._iter_files(root)}

    def test_루트의_부산물은_빠진다(self):
        seen = self._seen("work/perf/a.md", ".cache/embed/tokenizer.json", "keep.md")
        self.assertEqual(seen, {"keep.md"})

    def test_같은_이름이라도_루트가_아니면_스캔한다(self):
        """출하되는 스타터팩이 여기 산다. 이름 매칭으로 빼면 조용히 사각이 된다."""
        seen = self._seen("starter-packs/work/art/x.md", "a/.cache/b.md")
        self.assertIn("starter-packs/work/art/x.md", seen)
        self.assertIn("a/.cache/b.md", seen)

    def test_스타터팩_허용목록의_디렉터리가_관할_안에_있다(self):
        """허용목록에 그 디렉터리의 파일이 **이름으로** 올라 있다는 것이 곧 그곳이
        관할이라는 증거다. 제외가 이름 매칭이 되면 그 항목들은 아무 일도 안 하게 된다.

        허용목록 파일 자체는 `_iter_files` 가 걸러 내보내지 않으므로(그게 허용목록의
        뜻이다) **같은 디렉터리의 이웃 파일**이 스캔되는지로 관할을 확인한다."""
        starter = [x for x in g._ALLOWLIST_SUFFIXES if x.startswith("starter-packs/work/")]
        self.assertTrue(starter, "스타터팩 허용목록이 사라졌다면 이 테스트를 고칠 것")
        for entry in starter:
            sibling = str(Path(entry).parent / "sibling-not-allowlisted.md").replace("\\", "/")
            self.assertIn(sibling, self._seen(sibling), entry)


class TestRuleInventory(unittest.TestCase):
    """`--json` 이 실재하는 규칙 표를 싣는가.

    이 테스트가 이 패키지의 본체다. 정본 `vinc.canon.terminology` §8 은 "`--json` 의
    룰 수와 §4-7 의 살아 있는 행 수를 대조한다"고 **방법까지** 규정하는데, 출력에
    룰이 없어서 그 대조를 돌릴 수가 없었다. 규칙을 코드에만 늘리고 정본에 안 올려도
    아무도 모르는 상태가 그래서 생겼다(F9 일곱 줄이 실제로 그렇다).

    그러니 여기서 잠그는 것은 "인벤토리가 ALL_RULES 를 빠짐없이 싣는다"다 — 규칙을
    더하고 인벤토리를 안 더하면 이 테스트가 떨어진다.
    근거: [[vinc.canon.terminology §8]]
    """

    def _emit(self, name: str = "", content: str = ""):
        """`main(--json)` 을 통째로 통과시켜 stdout 을 파싱한다.

        `rule_inventory()` 를 직접 부르지 않는 이유: 정본이 가리키는 것은 **CLI 출력**
        이고, `re.Pattern` 은 JSON 직렬화가 안 되므로 직렬화 경계까지 지나야 실제로
        돌아가는 대조인지 알 수 있다.
        """
        with tempfile.TemporaryDirectory() as d:
            if name:
                f = Path(d) / name
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_text(content, encoding="utf-8")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = g.main(["--root", d, "--json"])
        return rc, json.loads(buf.getvalue())

    def test_코드의_모든_규칙이_출력에_실린다(self):
        rc, payload = self._emit()
        self.assertEqual(0, rc)
        emitted = {(r["gate"], r["label"]) for r in payload["rules"]}
        missing = {(r.gate, r.label) for r in g.ALL_RULES} - emitted
        self.assertEqual(set(), missing, "코드에 있고 인벤토리에 없는 규칙")
        self.assertEqual(len(g.ALL_RULES), payload["rule_count"])
        self.assertEqual(len(g.ALL_RULES), len(payload["rules"]))

    def test_각_항목이_대조에_필요한_것을_싣는다(self):
        # 정본 §4-7 의 표 열과 맞대 볼 수 있어야 한다: 어느 게이트 · 무슨 패턴 ·
        # 어디에 걸리는가 · 주석을 걷어 내는가.
        for r in self._emit()[1]["rules"]:
            self.assertTrue(r["gate"], r)
            self.assertTrue(r["pattern"], r)
            self.assertIsInstance(r["scope"], list)
            self.assertIsInstance(r["code_only"], bool)

    def test_게이트별_집계가_규칙_수와_맞는다(self):
        payload = self._emit()[1]
        self.assertEqual(payload["rule_count"], sum(payload["rules_by_gate"].values()))
        # F9 는 경로 한정이고 주석을 걷어 낸다 — 이 둘이 인벤토리에 보여야 정본과
        # 대조가 된다(F9 가 정본에 없다는 사실이 그래서 드러난다).
        f9 = [r for r in payload["rules"] if r["gate"] == "F9"]
        self.assertTrue(f9)
        self.assertTrue(all(r["code_only"] and r["scope"] for r in f9))

    def test_위반_출력_모양은_그대로다(self):
        """기존 네 키는 더하기 전과 같다. 인벤토리는 **추가**지 변경이 아니다."""
        rc, payload = self._emit("a.py", "usage_record = 1" + chr(10))
        self.assertEqual(1, rc)
        self.assertEqual("FAIL", payload["status"])
        self.assertEqual(1, payload["count"])
        self.assertEqual(
            {"gate", "label", "file", "line", "text"},
            set(payload["violations"][0]),
        )
        self.assertEqual("F1", payload["violations"][0]["gate"])


if __name__ == "__main__":
    unittest.main()
